"""
步骤 5：稳健性检验（论文 §6.4 表 12、§6.5 表 13/14、图 4）

产出：
  ① 观察窗扫描      Δ = 7/14/30/60/90 天下的超额复发与 ρ
  ② 位置粒度对照    地块编号 vs 100 米网格
  ③ 零模型对照      泊松 vs 经验（循环时移）
  ④ 类别边界对照    D、G 计入 / 移出
  ⑤ 汇总表 12       各设定下的 ρ —— 应对「零相关是否只是噪声」的核心证据

前置：step2 建库、step3 跑过、step4 --apply 跑过（需要库里的 t 与 labeled 表）

═══════════ 经验零模型的设计 ═══════════
泊松零模型假设工单均匀到达，与投诉的突发性不符（见论文 §3.3.4）。
本脚本用**循环时移**构造一个保留真实到达节奏的经验零模型：

  把每张工单的结案时间沿时间轴整体平移 k 天（超出观察期则循环回到期初），
  再用完全相同的规则计算「复发」。

平移后的窗口落在该组历史中的一个随机位置，因此：
  - 若该组工单密集，随机窗口本就容易命中 → 期望值高
  - 且这一期望值来自**实际到达模式**，而非均匀性假设

对多个平移量取平均，即得经验期望复发率。
超额复发 = 实际复发 − 经验期望复发。
═══════════════════════════════════════

用法：
    python step5_robustness.py            # 全部跑（较慢，约 20–40 分钟）
    python step5_robustness.py --quick    # 只跑 Δ 扫描与类别边界（约 10 分钟）
"""

import os
import time
import argparse
import duckdb

OUT         = "out"
MEM_LIMIT   = "6GB"
THREADS     = 4

WINDOWS     = [7, 14, 30, 60, 90]     # 观察窗扫描
BASE_WINDOW = 30                      # 主设定
SHIFTS      = [91, 182, 273, 365]     # 经验零模型的循环平移量（天）
PERIOD_DAYS = 1096                    # 2023-01-01 ~ 2025-12-31
MIN_N       = 5000                    # 分组最小工单数
MIN_COVER   = 0.80                    # 分组最小归类覆盖率

os.makedirs(OUT, exist_ok=True)
con = duckdb.connect("nyc311.duckdb")
con.execute(f"PRAGMA memory_limit='{MEM_LIMIT}'")
con.execute(f"PRAGMA threads={THREADS}")


def show(title, sql, csv=None):
    print(f"\n{title}")
    con.sql(sql).show(max_rows=60)
    if csv:
        con.execute(f"COPY ({sql}) TO '{OUT}/{csv}' (HEADER, DELIMITER ',')")


def check_prereq():
    tabs = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
    missing = {"t", "labeled"} - tabs
    if missing:
        print(f"❌ 库里缺少表 {missing}")
        print("   请先依次运行 step2_analyze.py / step3_diagnose.py / step4_classify.py --apply")
        return False
    return True


# ═════════════════════════════════════════════════
# 复发计算：可指定观察窗、位置列、结案时间表达式
# ═════════════════════════════════════════════════
def build_recurrence(tag, window, loc, close_expr="c.closed_date"):
    """
    生成一张 rec_<tag> 表：unique_key, agency, complaint_type, loc, grp_n, recurred
    close_expr 可换成时移后的表达式，用于经验零模型
    """
    t0 = time.time()
    con.execute(f"""
    CREATE OR REPLACE TABLE rec_{tag} AS
    WITH grp AS (
      SELECT {loc} AS loc, complaint_type, count(*) AS grp_n
      FROM t WHERE {loc} IS NOT NULL
      GROUP BY 1, 2
    ),
    closed AS (
      SELECT * FROM t
      WHERE closed_date IS NOT NULL
        AND closed_date >= created_date
        AND {loc} IS NOT NULL
    )
    SELECT c.unique_key, c.agency, c.complaint_type,
           c.{loc} AS loc, g.grp_n,
           MAX(CASE WHEN n.unique_key IS NOT NULL THEN 1 ELSE 0 END) AS recurred
    FROM closed c
    JOIN grp g ON g.loc = c.{loc} AND g.complaint_type = c.complaint_type
    LEFT JOIN t n
      ON  n.{loc}          = c.{loc}
      AND n.complaint_type = c.complaint_type
      AND n.unique_key    <> c.unique_key
      AND n.created_date  >  {close_expr}
      AND n.created_date  <= {close_expr} + INTERVAL {window} DAY
    GROUP BY ALL;
    """)
    n = con.execute(f"SELECT count(*) FROM rec_{tag}").fetchone()[0]
    print(f"    rec_{tag}: {n:,} 行，耗时 {time.time()-t0:.0f}s")


def shifted_close(k):
    """循环时移 k 天的结案时间表达式"""
    return (f"(CASE WHEN c.closed_date + INTERVAL {k} DAY "
            f"          >= TIMESTAMP '2026-01-01' "
            f"     THEN c.closed_date + INTERVAL {k} DAY "
            f"          - INTERVAL {PERIOD_DAYS} DAY "
            f"     ELSE c.closed_date + INTERVAL {k} DAY END)")


# ═════════════════════════════════════════════════
# 把某张 rec 表转成带超额的 excess 表（泊松零模型）
# ═════════════════════════════════════════════════
def add_poisson_excess(tag, window):
    con.execute(f"""
    CREATE OR REPLACE TABLE exc_{tag} AS
    SELECT *,
           1 - exp(-1.0 * grp_n / {PERIOD_DAYS} * {window}) AS p_exp,
           recurred - (1 - exp(-1.0 * grp_n / {PERIOD_DAYS} * {window})) AS excess
    FROM rec_{tag};
    """)


# ═════════════════════════════════════════════════
# 在给定 excess 表与 NR 口径下计算 ρ
# ═════════════════════════════════════════════════
def spearman(exc_table, dim, nr_codes, label):
    """
    nr_codes: 计入非解决型的类别集合，如 "'B','C','D','E','F','G'"
    返回 (n, rho)
    """
    con.execute(f"""
    CREATE OR REPLACE TABLE xv_tmp AS
    WITH cov AS (
      SELECT {dim} AS dim FROM labeled GROUP BY 1
      HAVING avg(CASE WHEN code <> 'X' THEN 1.0 ELSE 0 END) >= {MIN_COVER}
    ),
    nr AS (
      SELECT {dim} AS dim, count(*) AS n_text,
             avg(CASE WHEN code IN ({nr_codes}) THEN 1.0 ELSE 0 END) AS nr
      FROM labeled WHERE code <> 'X'
      GROUP BY 1 HAVING count(*) >= {MIN_N}
    ),
    ex AS (
      SELECT {dim} AS dim, count(*) AS n_beh, avg(excess) AS excess
      FROM {exc_table} GROUP BY 1 HAVING count(*) >= {MIN_N}
    )
    SELECT nr.dim, round(100.0*nr.nr,2) AS nr_pct,
           round(100.0*ex.excess,2)     AS excess_pct
    FROM nr JOIN ex USING (dim) JOIN cov USING (dim);
    """)
    n = con.execute("SELECT count(*) FROM xv_tmp").fetchone()[0]
    if n < 4:
        return n, None
    rho = con.execute("""
      WITH r AS (SELECT rank() OVER (ORDER BY nr_pct)     AS r1,
                        rank() OVER (ORDER BY excess_pct) AS r2
                 FROM xv_tmp)
      SELECT round(corr(r1, r2), 4) FROM r
    """).fetchone()[0]
    return n, rho


# ═════════════════════════════════════════════════
def main(quick=False):
    if not check_prereq():
        return

    results = []          # 汇总表 12 的行
    t_start = time.time()

    # ── ① 观察窗扫描（泊松零模型，地块编号方案）──
    print("\n" + "═" * 64)
    print("① 观察窗扫描  Δ = 7/14/30/60/90 天")
    print("═" * 64)

    for w in WINDOWS:
        print(f"\n  Δ = {w} 天")
        tag = f"w{w}"
        build_recurrence(tag, w, "loc_a")
        add_poisson_excess(tag, w)

        row = con.execute(f"""
          SELECT round(100.0*avg(recurred),2), round(100.0*avg(p_exp),2),
                 round(100.0*avg(excess),2)
          FROM exc_{tag}
        """).fetchone()
        print(f"    实际 {row[0]}%   期望 {row[1]}%   超额 {row[2]}%")

        for dim, dname in [("complaint_type", "类型"), ("agency", "部门")]:
            n, rho = spearman(f"exc_{tag}", dim, "'B','C','D','E','F','G'",
                              f"Δ={w} {dname}")
            results.append((f"观察窗 {w} 天", dname, "泊松", "默认",
                            "地块编号", n, rho))
            print(f"    ρ({dname}, n={n}) = {rho}")

    con.execute(f"""
    CREATE OR REPLACE TABLE window_sweep AS
    {' UNION ALL '.join([
      f"SELECT {w} AS window_days, round(100.0*avg(recurred),2) AS observed_pct,"
      f" round(100.0*avg(p_exp),2) AS expected_pct,"
      f" round(100.0*avg(excess),2) AS excess_pct FROM exc_w{w}"
      for w in WINDOWS])}
    """)
    show("图 4 数据：超额复发随观察窗的变化",
         "SELECT * FROM window_sweep ORDER BY window_days",
         "J1_window_sweep.csv")

    if quick:
        finish(results, t_start)
        return

    # ── ② 位置粒度对照 ──
    print("\n" + "═" * 64)
    print("② 位置粒度对照：地块编号 vs 100 米网格")
    print("═" * 64)

    build_recurrence("grid", BASE_WINDOW, "loc_b")
    add_poisson_excess("grid", BASE_WINDOW)

    show("表 13：位置粒度对照", f"""
    SELECT '地块编号优先' AS scheme, count(*) AS tickets,
           round(100.0*avg(recurred),2) AS observed_pct,
           round(100.0*avg(p_exp),2)    AS expected_pct,
           round(100.0*avg(excess),2)   AS excess_pct
    FROM exc_w{BASE_WINDOW}
    UNION ALL
    SELECT '100 米网格', count(*),
           round(100.0*avg(recurred),2), round(100.0*avg(p_exp),2),
           round(100.0*avg(excess),2)
    FROM exc_grid
    """, "J2_location_granularity.csv")

    for dim, dname in [("complaint_type", "类型"), ("agency", "部门")]:
        n, rho = spearman("exc_grid", dim, "'B','C','D','E','F','G'", "网格")
        results.append(("位置粒度 100m 网格", dname, "泊松", "默认",
                        "网格", n, rho))
        print(f"  ρ({dname}, n={n}) = {rho}")

    # ── ③ 经验零模型（循环时移）──
    print("\n" + "═" * 64)
    print("③ 零模型对照：泊松 vs 经验（循环时移）")
    print("═" * 64)
    print(f"  平移量 {SHIFTS} 天，各跑一次后取平均")

    for k in SHIFTS:
        print(f"\n  平移 {k} 天")
        build_recurrence(f"s{k}", BASE_WINDOW, "loc_a", shifted_close(k))

    # 经验期望 = 各平移版本复发率的平均
    con.execute(f"""
    CREATE OR REPLACE TABLE exc_emp AS
    WITH shifted AS (
      SELECT unique_key, ({'+'.join([f'rec_s{k}.recurred' for k in SHIFTS])})
             / {len(SHIFTS)}.0 AS p_emp
      FROM rec_s{SHIFTS[0]}
      {' '.join([f'JOIN rec_s{k} USING (unique_key)' for k in SHIFTS[1:]])}
    )
    SELECT e.unique_key, e.agency, e.complaint_type, e.loc, e.grp_n,
           e.recurred, e.p_exp AS p_poisson, s.p_emp,
           e.recurred - s.p_emp AS excess
    FROM exc_w{BASE_WINDOW} e JOIN shifted s USING (unique_key);
    """)

    show("表 14：零模型对照（总体）", f"""
    SELECT round(100.0*avg(recurred),2)   AS observed_pct,
           round(100.0*avg(p_poisson),2)  AS poisson_expected_pct,
           round(100.0*avg(p_emp),2)      AS empirical_expected_pct,
           round(100.0*avg(recurred - p_poisson),2) AS excess_poisson_pct,
           round(100.0*avg(excess),2)     AS excess_empirical_pct
    FROM exc_emp
    """, "J3_null_model_overall.csv")

    show("表 14b：两种零模型下的部门排序", """
    SELECT agency, count(*) AS tickets,
           round(100.0*avg(recurred),2)             AS observed_pct,
           round(100.0*avg(recurred - p_poisson),2) AS excess_poisson,
           round(100.0*avg(excess),2)               AS excess_empirical
    FROM exc_emp GROUP BY agency HAVING count(*) >= 5000
    ORDER BY excess_empirical DESC
    """, "J4_null_model_by_agency.csv")

    print("\n↑ 若两列超额的部门排序基本一致，说明 §6.3 的排序反转不是泊松假设的产物。")

    for dim, dname in [("complaint_type", "类型"), ("agency", "部门")]:
        n, rho = spearman("exc_emp", dim, "'B','C','D','E','F','G'", "经验零模型")
        results.append(("零模型 经验时移", dname, "经验", "默认",
                        "地块编号", n, rho))
        print(f"  ρ({dname}, n={n}) = {rho}")

    # ── ④ 类别边界对照 ──
    print("\n" + "═" * 64)
    print("④ 类别边界对照：D、G 移出非解决型")
    print("═" * 64)

    show("表 15：类别边界敏感性", """
    SELECT round(100.0*avg(CASE WHEN code IN ('B','C','D','E','F','G')
                                THEN 1.0 ELSE 0 END), 2) AS nr_pct_default,
           round(100.0*avg(CASE WHEN code IN ('B','C','E','F')
                                THEN 1.0 ELSE 0 END), 2) AS nr_pct_strict
    FROM labeled WHERE code <> 'X'
    """, "J5_boundary_sensitivity.csv")

    for dim, dname in [("complaint_type", "类型"), ("agency", "部门")]:
        n, rho = spearman(f"exc_w{BASE_WINDOW}", dim, "'B','C','E','F'", "严格口径")
        results.append(("类别边界 严格口径", dname, "泊松", "严格",
                        "地块编号", n, rho))
        print(f"  ρ({dname}, n={n}) = {rho}")

    finish(results, t_start)


def finish(results, t_start):
    # ── ⑤ 汇总表 12 ──
    con.execute("DROP TABLE IF EXISTS rho_table")
    con.execute("""
    CREATE TABLE rho_table(
      setting VARCHAR, dim VARCHAR, null_model VARCHAR,
      nr_scope VARCHAR, location VARCHAR, n INTEGER, rho DOUBLE)
    """)
    for r in results:
        con.execute("INSERT INTO rho_table VALUES (?,?,?,?,?,?,?)", list(r))

    print("\n" + "═" * 64)
    print("表 12：各稳健性设定下的 Spearman ρ  ← 论文 §6.4 核心")
    print("═" * 64)

    show("按类型（样本量大，以此为主）",
         "SELECT setting, null_model, nr_scope, location, n, rho "
         "FROM rho_table WHERE dim='类型' ORDER BY setting",
         "J6_rho_robustness_type.csv")

    show("按部门（样本量小，仅供参考）",
         "SELECT setting, null_model, nr_scope, location, n, rho "
         "FROM rho_table WHERE dim='部门' ORDER BY setting",
         "J7_rho_robustness_agency.csv")

    rng = con.execute(
        "SELECT round(min(rho),4), round(max(rho),4), round(avg(rho),4) "
        "FROM rho_table WHERE dim='类型' AND rho IS NOT NULL").fetchone()
    print(f"\n类型层面 ρ 的取值范围：{rng[0]} ~ {rng[1]}，均值 {rng[2]}")
    print("\n判读：")
    print("  全部设定下 ρ 均接近 0  → 零相关稳健，§6.4 结论成立")
    print("  某些设定下 ρ 显著偏离  → 如实报告，讨论该设定为何不同")
    print(f"\n总耗时 {(time.time()-t_start)/60:.1f} 分钟")
    print(f"CSV 已写入 {OUT}/")
    con.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true",
                    help="只跑观察窗扫描（跳过位置粒度与经验零模型）")
    args = ap.parse_args()
    main(args.quick)

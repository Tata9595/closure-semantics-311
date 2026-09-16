"""
步骤 8：占位地址查证与散点图

  ① --placeholder   占位地址诊断 → 排除规则 → 排除前后结果对比（论文 §4.2.4、§6.6）
  ② --scatter       三种口径的散点图数据 + 直接出图（论文 图 2）
  ③ --gaps2         各部门未归类模板清单，按部门分组（辅助补标）

前置：step2/3/4/5 均已运行过

用法：
    python step8_finalize2.py --placeholder
    python step8_finalize2.py --scatter
    python step8_finalize2.py --gaps2
"""

import os
import math
import argparse
import duckdb

RAW         = "data/raw/*.parquet"
OUT         = "out"
FIG         = "out/fig"
LABEL_FILE  = f"{OUT}/I1_templates_to_label.csv"
BASE_WINDOW = 30
PERIOD_DAYS = 1096
MIN_N       = 5000
MIN_COVER   = 0.80

# ── 占位地址排除规则 ──
# 经 §4.2.4 的四项证据判定为地理编码默认落点的「地址前缀 + 类型」组合。
# 这些工单本身是真实的，但其位置字段不可用于基于位置的分析，故排除。
EXCLUDE_RULES = [
    ("655 EAST 230", "Noise - Residential"),
]

WIDE   = "'B','C','D','E','F','G'"
MID    = "'B','C','D','E','F'"
STRICT = "'B','C','E','F'"

os.makedirs(OUT, exist_ok=True)
os.makedirs(FIG, exist_ok=True)


def connect():
    con = duckdb.connect("nyc311.duckdb")
    con.execute("PRAGMA memory_limit='6GB'")
    con.execute("PRAGMA threads=4")
    return con


def show(con, title, sql, csv=None):
    print(f"\n{title}")
    con.sql(sql).show(max_rows=60)
    if csv:
        con.execute(f"COPY ({sql}) TO '{OUT}/{csv}' (HEADER, DELIMITER ',')")


# ═══════════════════════════════════════════════
# ① 占位地址：诊断 → 规则 → 前后对比
# ═══════════════════════════════════════════════
def run_placeholder():
    con = connect()

    print("═" * 66)
    print("第 1 步：看清楚这些超大组到底是什么")
    print("═" * 66)

    show(con, "工单量最大的 15 个「位置+类型」组", f"""
    SELECT any_value(addr_norm)              AS address,
           any_value(bbl)                    AS bbl,
           any_value(borough)                AS borough,
           complaint_type,
           any_value(agency)                 AS agency,
           count(*)                          AS tickets,
           round(count(*)*1.0/{PERIOD_DAYS}, 1) AS per_day,
           min(created_date)::DATE           AS first_seen,
           max(created_date)::DATE           AS last_seen
    FROM t WHERE loc_a IS NOT NULL
    GROUP BY loc_a, complaint_type
    ORDER BY tickets DESC LIMIT 15
    """, "M1_super_groups_detail.csv")

    print("\n↑ 用 address 与 bbl 去以下三处核对，判断是真实建筑还是地理编码默认落点：")
    print("   · 纽约市 ZoLa 地图      zola.planning.nyc.gov  （输入 BBL 或地址）")
    print("   · Google 街景           直接看是什么建筑")
    print("   · PLUTO 地块数据        含每地块的建筑面积与住宅单元数")
    print("\n   判据：普通住宅楼日均投诉超过 10 条即不合常理；超过 100 条几乎必为占位值。")

    # 同一地址跨类型的总量 —— 占位地址通常在多个类型上都异常
    show(con, "按地址汇总（跨类型），前 15", f"""
    SELECT any_value(addr_norm) AS address, any_value(bbl) AS bbl,
           any_value(borough)   AS borough,
           count(*)             AS tickets,
           round(count(*)*1.0/{PERIOD_DAYS},1) AS per_day,
           count(DISTINCT complaint_type)      AS n_types,
           count(DISTINCT agency)              AS n_agency
    FROM t WHERE loc_a IS NOT NULL
    GROUP BY loc_a ORDER BY tickets DESC LIMIT 15
    """, "M2_super_addresses.csv")

    print("\n↑ 若某地址在多个类型、多个部门上都异常高，更像系统默认落点而非真实热点。")

    print("\n" + "═" * 66)
    print("第 2 步：组大小的分布，用来定阈值")
    print("═" * 66)

    show(con, "「位置+类型」组大小的分位数", """
    SELECT count(*) AS n_groups,
           quantile_cont(cnt, 0.50)    AS p50,
           quantile_cont(cnt, 0.90)    AS p90,
           quantile_cont(cnt, 0.99)    AS p99,
           quantile_cont(cnt, 0.999)   AS p999,
           quantile_cont(cnt, 0.9999)  AS p9999,
           quantile_cont(cnt, 0.99999) AS p99999,
           max(cnt)                    AS max_group
    FROM (SELECT loc_a, complaint_type, count(*) AS cnt
          FROM t WHERE loc_a IS NOT NULL GROUP BY 1,2)
    """, "M3_group_quantiles.csv")

    show(con, "若按不同阈值排除，会剔掉多少工单", f"""
    WITH g AS (SELECT loc_a, complaint_type, count(*) AS cnt
               FROM t WHERE loc_a IS NOT NULL GROUP BY 1,2),
         tot AS (SELECT sum(cnt) AS s FROM g)
    SELECT th AS threshold_tickets,
           round(th*1.0/{PERIOD_DAYS},1) AS per_day_equiv,
           (SELECT count(*) FROM g WHERE cnt > th)          AS groups_excluded,
           (SELECT sum(cnt) FROM g WHERE cnt > th)          AS tickets_excluded,
           round(100.0*(SELECT sum(cnt) FROM g WHERE cnt > th)
                 / (SELECT s FROM tot), 3)                  AS pct_excluded
    FROM (VALUES (1000),(2000),(5000),(10000),(20000),(50000),(100000)) v(th)
    ORDER BY th
    """, "M4_threshold_options.csv")

    print("\n↑ 选阈值的原则：**剔除比例要小（建议 < 1%），但要能覆盖明显异常的组。**")
    print("   把选定的阈值和理由写进论文 §4.2.4，审稿人会问。")

    print("\n" + "═" * 66)
    print("第 3 步：按 EXCLUDE_RULES 排除后，与排除前对比")
    print("═" * 66)
    for a, c in EXCLUDE_RULES:
        print(f"  排除规则：地址以「{a}」开头 且 类型为「{c}」")

    cond = " OR ".join(
        [f"(t.addr_norm LIKE '{a.upper()}%' AND t.complaint_type = '{c}')"
         for a, c in EXCLUDE_RULES]) or "FALSE"

    n_ex = con.execute(f"SELECT count(*) FROM t WHERE {cond}").fetchone()[0]
    n_all = con.execute("SELECT count(*) FROM t").fetchone()[0]
    print(f"  命中 {n_ex:,} 张工单，占全部 {n_all:,} 张的 {100*n_ex/n_all:.2f}%\n")

    con.execute(f"""
    CREATE OR REPLACE TABLE t_clean AS
    SELECT * FROM t WHERE NOT ({cond});
    """)

    con.execute(f"""
    CREATE OR REPLACE TABLE rec_clean AS
    WITH grp AS (SELECT loc_a, complaint_type, count(*) AS grp_n
                 FROM t_clean GROUP BY 1,2),
         closed AS (SELECT * FROM t_clean
                    WHERE closed_date IS NOT NULL AND closed_date >= created_date)
    SELECT c.unique_key, c.agency, c.complaint_type, g.grp_n,
           MAX(CASE WHEN n.unique_key IS NOT NULL THEN 1 ELSE 0 END) AS recurred
    FROM closed c
    JOIN grp g ON g.loc_a = c.loc_a AND g.complaint_type = c.complaint_type
    LEFT JOIN t_clean n
      ON  n.loc_a = c.loc_a AND n.complaint_type = c.complaint_type
      AND n.unique_key <> c.unique_key
      AND n.created_date >  c.closed_date
      AND n.created_date <= c.closed_date + INTERVAL {BASE_WINDOW} DAY
    GROUP BY ALL;
    """)

    con.execute(f"""
    CREATE OR REPLACE TABLE exc_clean AS
    SELECT *, recurred - (1-exp(-1.0*grp_n/{PERIOD_DAYS}*{BASE_WINDOW})) AS excess
    FROM rec_clean;
    """)
    con.execute(f"""
    CREATE OR REPLACE TABLE exc_full AS
    SELECT unique_key, agency, complaint_type, grp_n, recurred,
           recurred - (1-exp(-1.0*grp_n/{PERIOD_DAYS}*{BASE_WINDOW})) AS excess
    FROM rec_w{BASE_WINDOW};
    """)

    show(con, "总体指标：排除前 vs 排除后", """
    SELECT '排除前' AS setting, count(*) AS tickets,
           round(100.0*avg(recurred),2) AS observed_pct,
           round(100.0*avg(excess),2)   AS excess_pct FROM exc_full
    UNION ALL
    SELECT '排除后', count(*),
           round(100.0*avg(recurred),2), round(100.0*avg(excess),2) FROM exc_clean
    """, "M5_before_after_overall.csv")

    for scope, codes, nm in [("宽 B-G", WIDE, "wide"),
                             ("中间 B-F", MID, "mid"),
                             ("窄 B,C,E,F", STRICT, "strict")]:
        r_full = spearman(con, "exc_full", "complaint_type", codes)
        r_cln = spearman(con, "exc_clean", "complaint_type", codes)
        print(f"  {scope:<12}  排除前 n={r_full[0]} ρ={r_full[1]}   "
              f"排除后 n={r_cln[0]} ρ={r_cln[1]}")

    print("\n↑ 若排除前后 ρ 基本不变，说明核心结论不受占位地址影响——这正是论文需要的稳健性证据。")
    con.close()


def spearman(con, exc_table, dim, nr_codes):
    con.execute(f"""
    CREATE OR REPLACE TABLE xv8 AS
    WITH cov AS (SELECT {dim} AS dim FROM labeled GROUP BY 1
                 HAVING avg(CASE WHEN code <> 'X' THEN 1.0 ELSE 0 END) >= {MIN_COVER}),
         nr  AS (SELECT {dim} AS dim,
                        avg(CASE WHEN code IN ({nr_codes}) THEN 1.0 ELSE 0 END) AS nr
                 FROM labeled WHERE code <> 'X'
                 GROUP BY 1 HAVING count(*) >= {MIN_N}),
         ex  AS (SELECT {dim} AS dim, avg(excess) AS excess
                 FROM {exc_table} GROUP BY 1 HAVING count(*) >= {MIN_N})
    SELECT nr.dim, round(100.0*nr.nr,2) AS nr_pct, round(100.0*ex.excess,2) AS excess_pct
    FROM nr JOIN ex USING (dim) JOIN cov USING (dim);
    """)
    n = con.execute("SELECT count(*) FROM xv8").fetchone()[0]
    if n < 4:
        return n, None
    rho = con.execute("""
      WITH r AS (SELECT rank() OVER (ORDER BY nr_pct) r1,
                        rank() OVER (ORDER BY excess_pct) r2 FROM xv8)
      SELECT round(corr(r1,r2),4) FROM r""").fetchone()[0]
    return n, rho


# ═══════════════════════════════════════════════
# ② 散点图
# ═══════════════════════════════════════════════
def run_scatter():
    con = connect()
    tabs = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
    if f"rec_w{BASE_WINDOW}" not in tabs:
        print("❌ 缺少 rec_w30，请先运行 step5_robustness.py")
        return

    con.execute(f"""
    CREATE OR REPLACE TABLE exc_base AS
    SELECT unique_key, agency, complaint_type, grp_n, recurred,
           recurred - (1-exp(-1.0*grp_n/{PERIOD_DAYS}*{BASE_WINDOW})) AS excess
    FROM rec_w{BASE_WINDOW};
    """)

    data = {}
    for scope, codes, tag in [("宽 B-G", WIDE, "wide"),
                              ("中间 B-F", MID, "mid"),
                              ("窄 B,C,E,F", STRICT, "strict")]:
        n, rho = spearman(con, "exc_base", "complaint_type", codes)
        con.execute(f"""COPY (SELECT dim AS complaint_type, nr_pct, excess_pct
                             FROM xv8 ORDER BY nr_pct DESC)
                        TO '{OUT}/M6_scatter_{tag}.csv' (HEADER, DELIMITER ',')""")
        rows = con.execute("SELECT dim, nr_pct, excess_pct FROM xv8").fetchall()
        data[tag] = (scope, n, rho, rows)
        print(f"  {scope:<12}  n={n}  ρ={rho}   → out/M6_scatter_{tag}.csv")

    # 合并成一张长表，方便 Excel 一次画
    con.execute(f"""
    COPY (
      SELECT '宽' AS scope, * FROM read_csv('{OUT}/M6_scatter_wide.csv', header=true)
      UNION ALL SELECT '中间', * FROM read_csv('{OUT}/M6_scatter_mid.csv', header=true)
      UNION ALL SELECT '窄',   * FROM read_csv('{OUT}/M6_scatter_strict.csv', header=true)
    ) TO '{OUT}/M7_scatter_all.csv' (HEADER, DELIMITER ',');
    """)
    print(f"\n合并表：out/M7_scatter_all.csv")

    # 尝试直接出图
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("\n未安装 matplotlib，跳过绘图。如需自动出图：")
        print("    pip install matplotlib --break-system-packages")
        print("否则用 out/M7_scatter_all.csv 在 Excel 里画（步骤见文末）")
        con.close()
        return

    fig, axes = plt.subplots(1, 3, figsize=(15, 5), sharey=True)
    for ax, tag in zip(axes, ["wide", "mid", "strict"]):
        scope, n, rho, rows = data[tag]
        xs = [r[1] for r in rows]
        ys = [r[2] for r in rows]
        ax.scatter(xs, ys, s=28, alpha=0.65, edgecolors="none")
        # 拟合线
        if len(xs) > 2:
            mx = sum(xs)/len(xs); my = sum(ys)/len(ys)
            den = sum((x-mx)**2 for x in xs)
            if den > 0:
                b = sum((x-mx)*(y-my) for x, y in zip(xs, ys))/den
                a = my - b*mx
                lo, hi = min(xs), max(xs)
                ax.plot([lo, hi], [a+b*lo, a+b*hi], linewidth=1.2, color="crimson")
        ax.axhline(0, linewidth=0.6, color="gray", linestyle="--")
        ax.set_title(f"{tag}   n={n}, rho={rho}", fontsize=11)
        ax.set_xlabel("Non-resolution share (%)")
        ax.grid(alpha=0.25)
    axes[0].set_ylabel("Excess recurrence (pp)")
    fig.suptitle("Figure 2. Non-resolution share vs excess recurrence, by complaint type",
                 fontsize=12)
    fig.tight_layout()
    path = f"{FIG}/fig2_scatter.png"
    fig.savefig(path, dpi=200)
    print(f"\n✅ 图已生成：{path}")
    print("   坐标轴标签用的是英文，可直接放进论文。")
    con.close()


# ═══════════════════════════════════════════════
# ③ 按部门列出未归类模板（辅助补标）
# ═══════════════════════════════════════════════
def run_gaps2():
    """
    按部门列出未归类模板，并计算：
      · 该部门当前覆盖率、距 80% 门槛还差多少张
      · 每条模板补标后能把该部门覆盖率推到多少（累计）
      · 该模板是否已在标注表中（决定是"填 code"还是"需重新 extract"）
    """
    if not os.path.exists(LABEL_FILE):
        print(f"❌ 找不到 {LABEL_FILE}")
        return
    raw = open(LABEL_FILE, "rb").read()
    for enc in ("utf-8-sig", "utf-8", "gb18030", "cp1252", "latin-1"):
        try:
            text = raw.decode(enc); break
        except UnicodeDecodeError:
            continue
    clean = LABEL_FILE[:-4] + "_utf8.csv"
    open(clean, "w", encoding="utf-8", newline="").write(text)

    con = connect()
    # 标注表全量（含已填与未填），用于判断模板在不在表里
    con.execute(f"""
    CREATE OR REPLACE TABLE lm8 AS
    SELECT template,
           NULLIF(upper(trim(CAST(code AS VARCHAR))), '') AS code
    FROM read_csv('{clean}', header=true, all_varchar=true, ignore_errors=true);
    """)

    # 每个部门的现状
    con.execute(f"""
    CREATE OR REPLACE TABLE ag_stat AS
    SELECT r.agency,
           count(*) AS closed_with_text,
           sum(CASE WHEN m.code IS NOT NULL AND m.code <> 'X' THEN 1 ELSE 0 END) AS classified
    FROM read_parquet('{RAW}') r
    LEFT JOIN lm8 m ON left(trim(r.resolution_description), {250}) = m.template
    WHERE r.closed_date IS NOT NULL
      AND r.resolution_description IS NOT NULL
      AND trim(r.resolution_description) <> ''
    GROUP BY 1;
    """)

    show(con, "各部门现状与缺口（按距门槛的差额降序）", """
    SELECT agency, closed_with_text, classified,
           round(100.0*classified/closed_with_text, 2)                  AS coverage_pct,
           greatest(0, CAST(ceil(0.80*closed_with_text) AS BIGINT) - classified)
                                                                        AS tickets_needed,
           CASE WHEN 1.0*classified/closed_with_text >= 0.80 THEN 'OK' ELSE '不足' END AS status
    FROM ag_stat
    ORDER BY tickets_needed DESC
    """, "M9_agency_gap.csv")

    print("\n↑ tickets_needed = 还需补标多少张工单才能达到 80% 门槛。")

    # 逐部门的待补模板
    targets = [r[0] for r in con.execute("""
        SELECT agency FROM ag_stat
        WHERE 1.0*classified/closed_with_text < 0.80
        ORDER BY (CAST(ceil(0.80*closed_with_text) AS BIGINT) - classified) DESC
    """).fetchall()]

    for ag in targets:
        show(con, f"【{ag}】待补模板（cum_pct = 补到该行为止，该部门可达的覆盖率）", f"""
        WITH g AS (
          SELECT left(trim(r.resolution_description), 250) AS template,
                 count(*) AS tickets,
                 max(CASE WHEN m.template IS NOT NULL THEN 1 ELSE 0 END) AS in_label_file
          FROM read_parquet('{RAW}') r
          LEFT JOIN lm8 m ON left(trim(r.resolution_description), 250) = m.template
          WHERE r.closed_date IS NOT NULL
            AND r.agency = '{ag}'
            AND r.resolution_description IS NOT NULL
            AND trim(r.resolution_description) <> ''
            AND (m.code IS NULL OR m.code = 'X')
          GROUP BY 1
        ),
        base AS (SELECT closed_with_text AS tot, classified AS cls
                 FROM ag_stat WHERE agency = '{ag}')
        SELECT tickets,
               CASE in_label_file WHEN 1 THEN '在表中·填 code'
                                  ELSE '不在表·需 extract' END        AS action,
               round(100.0*((SELECT cls FROM base)
                     + sum(tickets) OVER (ORDER BY tickets DESC
                                          ROWS UNBOUNDED PRECEDING))
                     / (SELECT tot FROM base), 2)                     AS cum_coverage_pct,
               left(template, 150)                                    AS template
        FROM g ORDER BY tickets DESC LIMIT 15
        """, f"M10_gaps_{ag}.csv")

    print("\n" + "─" * 66)
    print("怎么用这张表：")
    print("  1. 从上往下看 cum_coverage_pct，**补到它首次 ≥ 80 的那一行即可停**")
    print("  2. action 为「在表中·填 code」→ 直接在 I1_templates_to_label.csv 里填")
    print("  3. action 为「不在表·需 extract」→ 把 step4_classify.py 的")
    print("     TARGET_COV 从 95 提到 98，重跑 --extract（旧标注会自动继承），")
    print("     新增的行再填 code")
    print("  4. 判不了的用：python peek.py \"模板里的几个词\" --tail 200")
    con.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--placeholder", action="store_true", help="占位地址查证与前后对比")
    ap.add_argument("--scatter",     action="store_true", help="三种口径散点图")
    ap.add_argument("--gaps2",       action="store_true", help="按部门列出未归类模板")
    a = ap.parse_args()

    if a.placeholder:
        run_placeholder()
    elif a.scatter:
        run_scatter()
    elif a.gaps2:
        run_gaps2()
    else:
        ap.print_help()

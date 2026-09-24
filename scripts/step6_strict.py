import os
import time
import duckdb

OUT         = "out"
WINDOWS     = [7, 14, 30, 60, 90]
SHIFTS      = [91, 182, 273, 365]
BASE_WINDOW = 30
PERIOD_DAYS = 1096
MIN_N       = 5000
MIN_COVER   = 0.80

WIDE   = "'B','C','D','E','F','G'"
STRICT = "'B','C','E','F'"

os.makedirs(OUT, exist_ok=True)
con = duckdb.connect("nyc311.duckdb")
con.execute("PRAGMA memory_limit='6GB'")
con.execute("PRAGMA threads=4")


def show(title, sql, csv=None):
    print(f"\n{title}")
    con.sql(sql).show(max_rows=60)
    if csv:
        con.execute(f"COPY ({sql}) TO '{OUT}/{csv}' (HEADER, DELIMITER ',')")


def check_prereq():
    tabs = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
    need = {f"rec_w{w}" for w in WINDOWS} | {"labeled"}
    missing = need - tabs
    if missing:
        print(f"❌ 库里缺少表：{sorted(missing)}")
        print("   请先运行 step5_robustness.py（完整模式，不加 --quick）")
        return False
    if not {f"rec_s{k}" for k in SHIFTS} <= tabs:
        print("⚠️ 缺少时移表 rec_s*，经验零模型部分将跳过")
        print("   如需完整结果，请运行不加 --quick 的 step5_robustness.py")
    return True


def make_excess(window, null_model):

    name = f"ex6_{null_model}_{window}"

    if null_model == "poisson":
        con.execute(f"""
        CREATE OR REPLACE TABLE {name} AS
        SELECT unique_key, agency, complaint_type, grp_n, recurred,
               recurred - (1 - exp(-1.0*grp_n/{PERIOD_DAYS}*{window})) AS excess
        FROM rec_w{window};
        """)
    else:
        con.execute(f"""
        CREATE OR REPLACE TABLE {name} AS
        WITH emp AS (
          SELECT unique_key,
                 ({'+'.join([f'rec_s{k}.recurred' for k in SHIFTS])})/{len(SHIFTS)}.0 AS p_emp
          FROM rec_s{SHIFTS[0]}
          {' '.join([f'JOIN rec_s{k} USING (unique_key)' for k in SHIFTS[1:]])}
        )
        SELECT r.unique_key, r.agency, r.complaint_type, r.grp_n, r.recurred,
               r.recurred - e.p_emp AS excess
        FROM rec_w{window} r JOIN emp e USING (unique_key);
        """)
    return name


def spearman(exc_table, dim, nr_codes):
    con.execute(f"""
    CREATE OR REPLACE TABLE xv6 AS
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
           round(100.0*ex.excess,2) AS excess_pct
    FROM nr JOIN ex USING (dim) JOIN cov USING (dim);
    """)
    n = con.execute("SELECT count(*) FROM xv6").fetchone()[0]
    if n < 4:
        return n, None
    rho = con.execute("""
      WITH r AS (SELECT rank() OVER (ORDER BY nr_pct) r1,
                        rank() OVER (ORDER BY excess_pct) r2 FROM xv6)
      SELECT round(corr(r1,r2),4) FROM r
    """).fetchone()[0]
    return n, rho


def approx_p(rho, n):

    if rho is None or n < 4 or abs(rho) >= 1:
        return None
    import math
    t = rho * math.sqrt((n - 2) / (1 - rho * rho))

    z = abs(t)
    p = math.erfc(z / math.sqrt(2))
    return round(p, 4)


def main():
    if not check_prereq():
        return
    t0 = time.time()
    tabs = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
    has_emp = {f"rec_s{k}" for k in SHIFTS} <= tabs

    rows = []

    print("═" * 66)
    print("① 严格口径 vs 宽口径：跨观察窗与零模型")
    print("═" * 66)

    for w in WINDOWS:
        models = ["poisson"] + (["empirical"] if (has_emp and w == BASE_WINDOW) else [])
        for nm in models:
            exc = make_excess(w, nm)
            for scope, codes in [("宽 B-G", WIDE), ("严格 B,C,E,F", STRICT)]:
                for dim, dname in [("complaint_type", "类型"), ("agency", "部门")]:
                    n, rho = spearman(exc, dim, codes)
                    rows.append((w, nm, scope, dname, n, rho, approx_p(rho, n)))
                    if dname == "类型":
                        print(f"  Δ={w:>2}  {nm:<9}  {scope:<12}  "
                              f"n={n}  ρ={rho}  p≈{approx_p(rho, n)}")

    con.execute("DROP TABLE IF EXISTS rho6")
    con.execute("""CREATE TABLE rho6(window_days INTEGER, null_model VARCHAR,
                   nr_scope VARCHAR, dim VARCHAR, n INTEGER,
                   rho DOUBLE, p_approx DOUBLE)""")
    for r in rows:
        con.execute("INSERT INTO rho6 VALUES (?,?,?,?,?,?,?)", list(r))

    show("表 12（修订）：严格口径与宽口径下的 ρ —— 按类型", """
    SELECT window_days, null_model, nr_scope, n, rho, p_approx
    FROM rho6 WHERE dim='类型'
    ORDER BY nr_scope DESC, null_model, window_days
    """, "K1_rho_strict_vs_wide_type.csv")

    show("同上 —— 按部门（样本量小，仅供参考）", """
    SELECT window_days, null_model, nr_scope, n, rho, p_approx
    FROM rho6 WHERE dim='部门'
    ORDER BY nr_scope DESC, null_model, window_days
    """, "K2_rho_strict_vs_wide_agency.csv")

    summ = con.execute("""
      SELECT nr_scope, count(*) AS settings,
             round(min(rho),4) AS rho_min, round(max(rho),4) AS rho_max,
             round(avg(rho),4) AS rho_mean
      FROM rho6 WHERE dim='类型' GROUP BY 1 ORDER BY 1
    """).df() if False else None
    show("汇总：两种口径下 ρ 的取值范围（类型层面）", """
    SELECT nr_scope, count(*) AS settings,
           round(min(rho),4) AS rho_min, round(max(rho),4) AS rho_max,
           round(avg(rho),4) AS rho_mean
    FROM rho6 WHERE dim='类型' GROUP BY 1
    """, "K3_rho_summary.csv")


    print("\n" + "═" * 66)
    print("② 逐类别相关性：哪些类别携带信号，哪些在稀释")
    print("═" * 66)

    exc_base = make_excess(BASE_WINDOW, "poisson")
    per_cat = []
    for code in ["A", "B", "C", "D", "E", "F", "G"]:
        n, rho = spearman(exc_base, "complaint_type", f"'{code}'")
        per_cat.append((code, n, rho, approx_p(rho, n)))
        print(f"  {code}  n={n}  ρ={rho}  p≈{approx_p(rho, n)}")

    con.execute("DROP TABLE IF EXISTS percat")
    con.execute("CREATE TABLE percat(code VARCHAR, n INTEGER, rho DOUBLE, p_approx DOUBLE)")
    for r in per_cat:
        con.execute("INSERT INTO percat VALUES (?,?,?,?)", list(r))

    show("各类别占比与超额复发的相关性（Δ=30，泊松，类型层面）", """
    SELECT p.code,
           CASE p.code WHEN 'A' THEN '实质处置'   WHEN 'B' THEN '到场未发现'
                       WHEN 'C' THEN '责任人已离开' WHEN 'D' THEN '无需行动'
                       WHEN 'E' THEN '无法进入'   WHEN 'F' THEN '重复工单'
                       ELSE '转出/告知' END AS label,
           p.n, p.rho, p.p_approx
    FROM percat p ORDER BY p.rho DESC
    """, "K4_rho_per_category.csv")

    print("\n↑ 正相关大的类别携带信号；接近零或负相关的类别是稀释源。")
    print("  这张表为「为何严格口径更强」提供直接证据，是the paper 的关键论据。")


    print("\n" + "═" * 66)
    print("③ 图 3 散点图数据（严格口径，Δ=30，泊松）")
    print("═" * 66)

    spearman(exc_base, "complaint_type", STRICT)
    con.execute("""COPY (SELECT * FROM xv6 ORDER BY nr_pct DESC)
                   TO 'out/K5_scatter_strict_type.csv' (HEADER, DELIMITER ',')""")
    spearman(exc_base, "complaint_type", WIDE)
    con.execute("""COPY (SELECT * FROM xv6 ORDER BY nr_pct DESC)
                   TO 'out/K6_scatter_wide_type.csv' (HEADER, DELIMITER ',')""")
    print("  已导出 K5（严格口径）与 K6（宽口径），可在 Excel 画两张对照散点图")
    print("  横轴 nr_pct，纵轴 excess_pct，每点一个工单类型")

    print("\n" + "─" * 66)
    print("判读：")
    print("  严格口径 ρ 在多数设定下稳定在 0.3 左右  → 结论成立，可作为核心发现")
    print("  严格口径 ρ 只在个别设定下显著          → 降级为探索性发现，如实报告")
    print("  逐类别表中 D、G 明显偏低或为负          → 证实二者是稀释源")
    print(f"\n总耗时 {(time.time()-t0)/60:.1f} 分钟")
    print(f"CSV 已写入 {OUT}/")
    con.close()


if __name__ == "__main__":
    main()

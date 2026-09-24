import os
import math
import argparse
import duckdb

OUT         = "out"
LABEL_FILE  = f"{OUT}/I1_templates_to_label.csv"
SHEET_FILE  = f"{OUT}/L1_kappa_sheet.csv"
SAMPLE_N    = 200
WINDOWS     = [7, 14, 30, 60, 90]
SHIFTS      = [91, 182, 273, 365]
BASE_WINDOW = 30
PERIOD_DAYS = 1096
MIN_N       = 5000
MIN_COVER   = 0.80

WIDE   = "'B','C','D','E','F','G'"
MID    = "'B','C','D','E','F'"
STRICT = "'B','C','E','F'"

CODES = ["A", "B", "C", "D", "E", "F", "G", "X"]

os.makedirs(OUT, exist_ok=True)


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


def normalize_csv(path):

    raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "utf-8", "gb18030", "cp1252", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise RuntimeError(f"无法识别 {path} 的编码")
    clean = path[:-4] + "_utf8.csv"
    with open(clean, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    if enc not in ("utf-8", "utf-8-sig"):
        print(f"（标注表编码为 {enc}，已自动转存）")
    return clean


def spearman(con, exc_table, dim, nr_codes):
    con.execute(f"""
    CREATE OR REPLACE TABLE xv7 AS
    WITH cov AS (
      SELECT {dim} AS dim FROM labeled GROUP BY 1
      HAVING avg(CASE WHEN code <> 'X' THEN 1.0 ELSE 0 END) >= {MIN_COVER}
    ),
    nr AS (
      SELECT {dim} AS dim,
             avg(CASE WHEN code IN ({nr_codes}) THEN 1.0 ELSE 0 END) AS nr
      FROM labeled WHERE code <> 'X'
      GROUP BY 1 HAVING count(*) >= {MIN_N}
    ),
    ex AS (
      SELECT {dim} AS dim, avg(excess) AS excess
      FROM {exc_table} GROUP BY 1 HAVING count(*) >= {MIN_N}
    )
    SELECT nr.dim, round(100.0*nr.nr,2) AS nr_pct,
           round(100.0*ex.excess,2) AS excess_pct
    FROM nr JOIN ex USING (dim) JOIN cov USING (dim);
    """)
    n = con.execute("SELECT count(*) FROM xv7").fetchone()[0]
    if n < 4:
        return n, None
    rho = con.execute("""
      WITH r AS (SELECT rank() OVER (ORDER BY nr_pct) r1,
                        rank() OVER (ORDER BY excess_pct) r2 FROM xv7)
      SELECT round(corr(r1,r2),4) FROM r
    """).fetchone()[0]
    return n, rho


def approx_p(rho, n):
    if rho is None or n < 4 or abs(rho) >= 1:
        return None
    t = rho * math.sqrt((n - 2) / (1 - rho * rho))
    return round(math.erfc(abs(t) / math.sqrt(2)), 4)


def run_mid():
    con = connect()
    tabs = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
    if not {f"rec_w{w}" for w in WINDOWS} <= tabs:
        print("❌ 缺少 rec_w* 表，请先运行 step5_robustness.py（不加 --quick）")
        return
    has_emp = {f"rec_s{k}" for k in SHIFTS} <= tabs

    print("═" * 64)
    print("三种口径的对比（工单类型层面）")
    print("═" * 64)

    rows = []
    for w in WINDOWS:
        models = ["poisson"] + (["empirical"] if (has_emp and w == BASE_WINDOW) else [])
        for nm in models:
            name = f"ex7_{nm}_{w}"
            if nm == "poisson":
                con.execute(f"""
                CREATE OR REPLACE TABLE {name} AS
                SELECT unique_key, agency, complaint_type, grp_n, recurred,
                       recurred - (1-exp(-1.0*grp_n/{PERIOD_DAYS}*{w})) AS excess
                FROM rec_w{w};""")
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
                FROM rec_w{w} r JOIN emp e USING (unique_key);""")

            for scope, codes in [("宽 B-G", WIDE), ("中间 B-F", MID), ("窄 B,C,E,F", STRICT)]:
                n, rho = spearman(con, name, "complaint_type", codes)
                rows.append((w, nm, scope, n, rho, approx_p(rho, n)))
                print(f"  Δ={w:>2}  {nm:<9}  {scope:<12}  n={n}  ρ={rho}  p≈{approx_p(rho,n)}")

    con.execute("DROP TABLE IF EXISTS rho7")
    con.execute("""CREATE TABLE rho7(window_days INTEGER, null_model VARCHAR,
                   nr_scope VARCHAR, n INTEGER, rho DOUBLE, p_approx DOUBLE)""")
    for r in rows:
        con.execute("INSERT INTO rho7 VALUES (?,?,?,?,?,?)", list(r))

    show(con, "the paper 表 10：三种口径的相关系数汇总", """
    SELECT nr_scope, count(*) AS settings,
           round(min(rho),4) AS rho_min, round(max(rho),4) AS rho_max,
           round(avg(rho),4) AS rho_mean
    FROM rho7 GROUP BY 1
    ORDER BY rho_mean DESC
    """, "L2_three_scopes_summary.csv")

    show(con, "逐设定明细", """
    SELECT nr_scope, window_days, null_model, n, rho, p_approx
    FROM rho7 ORDER BY nr_scope, null_model, window_days
    """, "L3_three_scopes_detail.csv")


    show(con, "三种口径的工单占比", """
    SELECT round(100.0*avg(CASE WHEN code IN ('B','C','D','E','F','G') THEN 1.0 ELSE 0 END),2) AS wide_pct,
           round(100.0*avg(CASE WHEN code IN ('B','C','D','E','F')     THEN 1.0 ELSE 0 END),2) AS mid_pct,
           round(100.0*avg(CASE WHEN code IN ('B','C','E','F')         THEN 1.0 ELSE 0 END),2) AS strict_pct
    FROM labeled WHERE code <> 'X'
    """, "L4_scope_shares.csv")

    print("\n" + "─" * 64)
    print("判读（关系到the paper.3 那处理论与数据的矛盾）：")
    print("  中间口径 ρ 接近窄口径  → 结论对 D 的取舍不敏感，软肋补上")
    print("  中间口径 ρ 明显低于窄口径 → D 确实在稀释，需在正文解释")
    print("  中间口径 ρ 高于窄口径  → 说明不该剔除 D，窄口径定义要改")
    con.close()


def run_sheet():
    if not os.path.exists(LABEL_FILE):
        print(f"❌ 找不到 {LABEL_FILE}")
        return
    src = normalize_csv(LABEL_FILE)
    con = connect()

    con.execute(f"""
    CREATE OR REPLACE TABLE lbl AS
    SELECT CAST(idx AS INTEGER) AS idx, template, CAST(tickets AS BIGINT) AS tickets,
           upper(trim(CAST(code AS VARCHAR))) AS code1
    FROM read_csv('{src}', header=true, all_varchar=true, ignore_errors=true)
    WHERE code IS NOT NULL AND trim(CAST(code AS VARCHAR)) <> '';
    """)
    total = con.execute("SELECT count(*) FROM lbl").fetchone()[0]
    n = min(SAMPLE_N, total)


    con.execute("SELECT setseed(0.42)")
    con.execute(f"""
    COPY (
      SELECT idx, template, tickets, '' AS code2
      FROM lbl ORDER BY random() LIMIT {n}
    ) TO '{SHEET_FILE}' (HEADER, DELIMITER ',');
    """)

    print(f"已从 {total} 条模板中随机抽取 {n} 条，写入 {SHEET_FILE}")
    print("─" * 64)
    print("交给第二名标注者时，请说明：")
    print("  1. 只看 template 列，在 code2 列填 A–G 或 X")
    print("  2. 判断标准只有一条：**从报单市民的角度，他报的问题状态变了吗？**")
    print("       变了 → A")
    print("       没变 → 按原因分：")
    print("          B 到场未发现   人去了但没看到问题")
    print("          C 责任人已离开 问题在，但人跑了")
    print("          D 无需行动     看了，判断不用管")
    print("          E 无法进入     进不去，没查成")
    print("          F 重复工单     同一问题已有单在跟")
    print("          G 转出/告知    不归我管，或只给了信息")
    print("       判不了 → X")
    print("  3. **不要看第一次标注的结果**，独立判断（本表已隐去 code1）")
    print("  4. 填完把文件放回 out/ 原位，文件名不要改")
    print("─" * 64)
    print("然后运行：python step7_finalize.py --kappa")
    con.close()


def run_kappa():
    if not os.path.exists(SHEET_FILE):
        print(f"❌ 找不到 {SHEET_FILE}，请先运行 --sheet")
        return
    src1 = normalize_csv(LABEL_FILE)
    src2 = normalize_csv(SHEET_FILE)
    con = connect()

    con.execute(f"""
    CREATE OR REPLACE TABLE pair AS
    SELECT CAST(s.idx AS INTEGER) AS idx,
           CAST(s.tickets AS BIGINT) AS tickets,
           upper(trim(CAST(l.code  AS VARCHAR))) AS c1,
           upper(trim(CAST(s.code2 AS VARCHAR))) AS c2,
           s.template
    FROM read_csv('{src2}', header=true, all_varchar=true, ignore_errors=true) s
    JOIN read_csv('{src1}', header=true, all_varchar=true, ignore_errors=true) l
      ON CAST(s.idx AS INTEGER) = CAST(l.idx AS INTEGER)
    WHERE s.code2 IS NOT NULL AND trim(CAST(s.code2 AS VARCHAR)) <> '';
    """)
    n = con.execute("SELECT count(*) FROM pair").fetchone()[0]
    if n == 0:
        print("❌ code2 列全为空，第二名标注者尚未完成")
        return
    print(f"已配对 {n} 条双人标注\n")


    po = con.execute(
        "SELECT avg(CASE WHEN c1=c2 THEN 1.0 ELSE 0 END) FROM pair").fetchone()[0]


    pe = 0.0
    marg = {}
    for c in CODES:
        p1 = con.execute(f"SELECT avg(CASE WHEN c1='{c}' THEN 1.0 ELSE 0 END) FROM pair").fetchone()[0]
        p2 = con.execute(f"SELECT avg(CASE WHEN c2='{c}' THEN 1.0 ELSE 0 END) FROM pair").fetchone()[0]
        marg[c] = (p1, p2)
        pe += p1 * p2

    kappa = (po - pe) / (1 - pe) if pe < 1 else float("nan")


    pw = con.execute("""
      SELECT sum(CASE WHEN c1=c2 THEN tickets ELSE 0 END)*1.0/sum(tickets) FROM pair
    """).fetchone()[0]

    print("═" * 64)
    print("the paper 4.1.4：标注一致性检验")
    print("═" * 64)
    print(f"  配对样本量 n            = {n}")
    print(f"  观察一致率 Po           = {po:.4f}  ({100*po:.2f}%)")
    print(f"  期望一致率 Pe           = {pe:.4f}")
    print(f"  Cohen's kappa           = {kappa:.4f}")
    print(f"  按工单量加权的一致率     = {pw:.4f}  ({100*pw:.2f}%)")
    print()
    if kappa >= 0.8:
        verdict = "很好（≥0.80），可直接写入论文"
    elif kappa >= 0.6:
        verdict = "可接受（0.60–0.80），可写入论文并在局限性中说明"
    else:
        verdict = "❌ 偏低（<0.60），需修订类别定义后重新标注"
    print(f"  判读：{verdict}")

    con.execute("DROP TABLE IF EXISTS kappa_res")
    con.execute("CREATE TABLE kappa_res(metric VARCHAR, value DOUBLE)")
    for k, v in [("n", n), ("observed_agreement", po), ("expected_agreement", pe),
                 ("cohens_kappa", kappa), ("ticket_weighted_agreement", pw)]:
        con.execute("INSERT INTO kappa_res VALUES (?,?)", [k, float(v)])
    con.execute(f"COPY kappa_res TO '{OUT}/L5_kappa.csv' (HEADER, DELIMITER ',')")

    show(con, "混淆矩阵（行=第一标注者，列=第二标注者）", """
    SELECT c1,
      sum(CASE WHEN c2='A' THEN 1 ELSE 0 END) AS A,
      sum(CASE WHEN c2='B' THEN 1 ELSE 0 END) AS B,
      sum(CASE WHEN c2='C' THEN 1 ELSE 0 END) AS C,
      sum(CASE WHEN c2='D' THEN 1 ELSE 0 END) AS D,
      sum(CASE WHEN c2='E' THEN 1 ELSE 0 END) AS E,
      sum(CASE WHEN c2='F' THEN 1 ELSE 0 END) AS F,
      sum(CASE WHEN c2='G' THEN 1 ELSE 0 END) AS G,
      sum(CASE WHEN c2='X' THEN 1 ELSE 0 END) AS X
    FROM pair GROUP BY c1 ORDER BY c1
    """, "L6_confusion_matrix.csv")

    show(con, "分歧最大的模板（按工单量降序，前 20）", """
    SELECT tickets, c1, c2, left(template, 110) AS template
    FROM pair WHERE c1 <> c2 ORDER BY tickets DESC LIMIT 20
    """, "L7_disagreements.csv")

    print("\n↑ 逐条看分歧。若集中在某两类之间（如 B 与 D），说明该边界定义需要写得更清楚，")
    print("  并在the paper 3.2 补充判据。这段讨论本身就是方法学贡献。")
    con.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--mid",   action="store_true", help="补算中间口径相关系数")
    ap.add_argument("--sheet", action="store_true", help="生成盲标表")
    ap.add_argument("--kappa", action="store_true", help="计算一致率与 Cohen's kappa")
    a = ap.parse_args()

    if a.mid:
        run_mid()
    elif a.sheet:
        run_sheet()
    elif a.kappa:
        run_kappa()
    else:
        ap.print_help()

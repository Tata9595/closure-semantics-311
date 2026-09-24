import os
import argparse
import duckdb

RAW        = "data/raw/*.parquet"
OUT        = "out"
LABEL_FILE = f"{OUT}/I1_templates_to_label.csv"
PREFIX_LEN = 250


TARGET_COV = 95.0
MIN_COVERAGE = 80.0

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


RULES = [


    ("F", ["duplicate", "already reported", "same condition was reported"]),

    ("E", ["not able to gain access", "unable to gain access",
           "unable to gain entry", "could not gain access", "could not gain entry",
           "unable to complete", "unable to access", "no access",
           "was not able to inspect", "attempted to conduct an inspection",
           "no one was home", "no access to the", "denied access"]),

    ("C", ["those responsible", "were gone", "had gone",
           "upon arrival", "gone on arrival"]),

    ("B", ["no evidence", "observed no", "didn't observe", "did not observe",
           "found no condition", "no condition was found", "no violation",
           "did not violate", "unable to substantiate", "did not find",
           "could not find", "was not found", "were not found",
           "no violations were issued", "did not identify",
           "unable to locate", "could not locate", "no problem was found"]),

    ("D", ["was not necessary", "not necessary", "no further action",
           "determined that no action", "determined that no further",
           "no action was taken", "does not require"]),

    ("G", ["does not fall under", "jurisdiction", "website",
           "provided additional information", "referred to", "referred this",
           "forwarded to", "please contact", "transferred to",
           "mailed you", "sent you", "will be addressed by",
           "is responsible for this", "please call"]),

    ("A", ["took action to fix", "issued a summons", "violations were issued",
           "violation was issued", "corrected the condition", "corrected the problem",
           "repaired", "removed the", "cleaned", "collected the requested",
           "completed the requested", "completed the repair", "completed the work",
           "resolved the condition", "abated", "the condition was corrected",
           "made the repair", "restored"]),
]


def suggest_expr(col: str) -> str:

    parts = []
    for code, kws in RULES:
        conds = []
        for k in kws:

            safe = k.lower().replace("'", "''")
            conds.append(f"lower({col}) LIKE '%{safe}%'")
        parts.append(f"WHEN {' OR '.join(conds)} THEN '{code}'")
    return "CASE " + " ".join(parts) + " ELSE 'X' END"


def extract():
    con = connect()
    print("正在统计结案说明模板…")

    con.execute(f"""
    CREATE OR REPLACE TABLE templates AS
    WITH src AS (
      SELECT left(trim(resolution_description), {PREFIX_LEN}) AS tpl
      FROM read_parquet('{RAW}')
      WHERE closed_date IS NOT NULL
        AND resolution_description IS NOT NULL
        AND trim(resolution_description) <> ''
    ),
    agg AS (
      SELECT tpl, count(*) AS n FROM src GROUP BY tpl
    )
    SELECT tpl, n,
           round(100.0*n / sum(n) OVER (), 4)                            AS pct,
           round(100.0*sum(n) OVER (ORDER BY n DESC
                                    ROWS UNBOUNDED PRECEDING)
                 / sum(n) OVER (), 4)                                    AS cum_pct,
           {suggest_expr('tpl')}                                         AS suggested_code
    FROM agg ORDER BY n DESC;
    """)


    inherited = False
    if os.path.exists(LABEL_FILE):
        src = normalize_csv(LABEL_FILE)
        backup = LABEL_FILE[:-4] + "_backup.csv"
        with open(src, encoding="utf-8") as a, open(backup, "w", encoding="utf-8") as b:
            b.write(a.read())
        print(f"发现已有标注，已备份为 {backup}")

        con.execute(f"""
        CREATE OR REPLACE TABLE old_labels AS
        SELECT template AS tpl_old,
               length(template) AS len_old,
               upper(trim(CAST(code AS VARCHAR))) AS code
        FROM read_csv('{src}', header=true, all_varchar=true, ignore_errors=true)
        WHERE code IS NOT NULL AND trim(CAST(code AS VARCHAR)) <> '';
        """)
        n_old = con.execute("SELECT count(*) FROM old_labels").fetchone()[0]

        if n_old:
            con.execute("""
            CREATE OR REPLACE TABLE inherit AS
            WITH m AS (
              SELECT t.tpl, o.tpl_old, o.code, o.len_old,
                     row_number() OVER (PARTITION BY t.tpl
                                        ORDER BY o.len_old DESC) AS rk
              FROM templates t
              JOIN old_labels o
                ON left(t.tpl, o.len_old) = o.tpl_old
            ),
            best AS (SELECT tpl, tpl_old, code FROM m WHERE rk = 1),
            split AS (
              SELECT tpl_old, count(*) AS n_new
              FROM best GROUP BY tpl_old
            )
            SELECT b.tpl, b.code,
                   CASE WHEN s.n_new > 1 THEN 1 ELSE 0 END AS needs_review
            FROM best b JOIN split s USING (tpl_old);
            """)
            n_inh, n_rev = con.execute("""
                SELECT count(*), sum(needs_review) FROM inherit
            """).fetchone()
            print(f"已继承 {n_inh:,} 条旧标注，其中 {n_rev or 0:,} 条因前缀加长而分叉，需复核")
            inherited = True

    total_tpl = con.execute("SELECT count(*) FROM templates").fetchone()[0]
    need = con.execute(f"""
        SELECT count(*) FROM templates WHERE cum_pct <= {TARGET_COV}
    """).fetchone()[0] + 1

    print(f"\n模板总数：{total_tpl:,}")
    print(f"覆盖 {TARGET_COV}% 工单需标注前 {need:,} 条")

    show(con, "覆盖率进展", """
    SELECT '前 20 条'  AS top_n, round(max(cum_pct),2) AS coverage_pct
      FROM templates WHERE rowid < 20
    UNION ALL SELECT '前 50 条',  round(max(cum_pct),2) FROM templates WHERE rowid < 50
    UNION ALL SELECT '前 100 条', round(max(cum_pct),2) FROM templates WHERE rowid < 100
    UNION ALL SELECT '前 200 条', round(max(cum_pct),2) FROM templates WHERE rowid < 200
    UNION ALL SELECT '前 500 条', round(max(cum_pct),2) FROM templates WHERE rowid < 500
    """)

    show(con, "建议分类的初步分布（仅供参考，须人工核对）", """
    SELECT suggested_code, count(*) AS templates,
           sum(n) AS tickets, round(sum(pct), 2) AS pct
    FROM templates GROUP BY 1 ORDER BY tickets DESC
    """, "I0_suggested_distribution.csv")


    code_expr = ("COALESCE(i.code, '')" if inherited else "''")
    review_expr = ("COALESCE(i.needs_review, 0)" if inherited else "0")
    join_expr = ("LEFT JOIN inherit i ON i.tpl = t.tpl" if inherited else "")

    con.execute(f"""
    COPY (
      SELECT row_number() OVER (ORDER BY t.n DESC) AS idx,
             t.tpl AS template, t.n AS tickets, t.pct, t.cum_pct,
             t.suggested_code,
             {review_expr} AS needs_review,
             {code_expr}   AS code
      FROM templates t {join_expr}
      WHERE t.cum_pct <= {TARGET_COV + 2}
      ORDER BY t.n DESC
    ) TO '{LABEL_FILE}' (HEADER, DELIMITER ',');
    """)

    print(f"\n待标注表已导出：{LABEL_FILE}")
    print("─" * 60)
    print("下一步：")
    print(f"  1. 用 Excel 打开 {LABEL_FILE}")
    if inherited:
        print("  2. code 列已自动继承旧标注，只需处理两类行：")
        print("       needs_review = 1  → 前缀加长后分叉，必须重新判定")
        print("       code 为空         → 新出现的模板")
        print("     其余行已填好，不用动")
    else:
        print("  2. 逐行核对 suggested_code，把最终判定填进 code 列")
    print("  3. 保存（编码不限，脚本会自动处理）")
    print("  4. 运行  python step4_classify.py --gaps  查看剩余缺口")
    print("  5. 缺口可接受后运行  python step4_classify.py --apply")
    print("─" * 60)
    print("⚠️ 论文需要标注一致性检验：请第二个人独立标注其中随机 200 条，")
    print("   计算一致率与 Cohen's kappa，写入 §4.1.3。")
    con.close()


def normalize_csv(path: str) -> str:


    raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "utf-8", "gb18030", "cp1252", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise RuntimeError(f"无法识别 {path} 的编码，请在 Excel 里另存为「CSV UTF-8」")

    clean = path[:-4] + "_utf8.csv" if path.endswith(".csv") else path + "_utf8"
    with open(clean, "w", encoding="utf-8", newline="") as f:
        f.write(text)

    if enc not in ("utf-8", "utf-8-sig"):
        print(f"⚠️ 标注表编码为 {enc}（非 UTF-8），已自动转存为 {clean}")
    return clean


def apply_labels():
    if not os.path.exists(LABEL_FILE):
        print(f"❌ 找不到 {LABEL_FILE}，请先运行 --extract 并完成标注")
        return

    label_src = normalize_csv(LABEL_FILE)
    con = connect()

    con.execute(f"""
    CREATE OR REPLACE TABLE label_map AS
    SELECT template,
           upper(trim(CAST(code AS VARCHAR))) AS code
    FROM read_csv('{label_src}', header=true, all_varchar=true,
                  ignore_errors=true)
    WHERE code IS NOT NULL AND trim(CAST(code AS VARCHAR)) <> '';
    """)
    n_lab = con.execute("SELECT count(*) FROM label_map").fetchone()[0]
    if n_lab == 0:
        print("❌ code 列全为空，请先完成标注")
        return
    print(f"已读入 {n_lab:,} 条标注")


    bad = con.execute("""
        SELECT code, count(*) AS n FROM label_map
        WHERE code NOT IN ('A','B','C','D','E','F','G','X')
        GROUP BY 1 ORDER BY n DESC
    """).fetchall()
    if bad:
        print("\n⚠️ 发现非法的 code 值（只允许 A–G 与 X），这些行会被当作未归类：")
        for c, n in bad:
            print(f"    「{c}」 {n} 条")
        print("   常见原因：小写字母未转大写（已自动转）、多余空格、中文全角字符\n")

    show(con, "标注分布", """
    SELECT code, count(*) AS templates FROM label_map
    GROUP BY 1 ORDER BY templates DESC
    """)


    con.execute(f"""
    CREATE OR REPLACE TABLE labeled AS
    SELECT r.unique_key::BIGINT AS unique_key,
           r.agency, r.complaint_type,
           COALESCE(m.code, 'X') AS code
    FROM read_parquet('{RAW}') r
    LEFT JOIN label_map m
      ON left(trim(r.resolution_description), {PREFIX_LEN}) = m.template
    WHERE r.closed_date IS NOT NULL
      AND r.resolution_description IS NOT NULL
      AND trim(r.resolution_description) <> '';
    """)

    print("\n" + "═" * 62)
    print("§6.1  结案语义分布")
    print("═" * 62)

    show(con, "覆盖率", """
    SELECT count(*) AS closed_with_text,
           sum(CASE WHEN code <> 'X' THEN 1 ELSE 0 END) AS classified,
           round(100.0*avg(CASE WHEN code <> 'X' THEN 1.0 ELSE 0 END), 2) AS coverage_pct
    FROM labeled
    """, "I2_coverage.csv")

    show(con, "各类别占比（分母为已归类工单）", """
    SELECT code,
           CASE code WHEN 'A' THEN '实质处置'   WHEN 'B' THEN '到场未发现'
                     WHEN 'C' THEN '责任人已离开' WHEN 'D' THEN '无需行动'
                     WHEN 'E' THEN '无法进入'   WHEN 'F' THEN '重复工单'
                     WHEN 'G' THEN '转出/告知'  ELSE '其他' END AS label,
           count(*) AS tickets,
           round(100.0*count(*) / (SELECT count(*) FROM labeled
                                   WHERE code <> 'X'), 2) AS pct
    FROM labeled WHERE code <> 'X'
    GROUP BY 1,2 ORDER BY tickets DESC
    """, "I3_category_distribution.csv")


    print("\n" + "═" * 62)
    print("⚠️ 关键诊断：按部门的归类覆盖率")
    print("═" * 62)
    print("若各部门覆盖率差异很大，按部门的非解决型占比不可比，")
    print("交叉验证也会失效——低覆盖部门的数值由「哪些模板碰巧被归类」决定。")

    show(con, "部门覆盖率", f"""
    SELECT agency,
           count(*)                                                  AS closed_with_text,
           sum(CASE WHEN code <> 'X' THEN 1 ELSE 0 END)              AS classified,
           round(100.0*avg(CASE WHEN code <> 'X' THEN 1.0 ELSE 0 END), 2) AS coverage_pct,
           CASE WHEN avg(CASE WHEN code <> 'X' THEN 1.0 ELSE 0 END)
                     >= {MIN_COVERAGE/100.0} THEN 'OK' ELSE '不足' END AS status
    FROM labeled GROUP BY agency
    ORDER BY coverage_pct ASC
    """, "I2b_coverage_by_agency.csv")

    low = con.execute(f"""
        SELECT count(*) FROM (
          SELECT agency FROM labeled GROUP BY agency
          HAVING avg(CASE WHEN code <> 'X' THEN 1.0 ELSE 0 END) < {MIN_COVERAGE/100.0}
        )
    """).fetchone()[0]
    if low:
        print(f"\n❌ 有 {low} 个部门覆盖率低于 {MIN_COVERAGE}%。")
        print("   这些部门的 nr_pct 不可信（典型症状：恰好等于 100.0 或 0.0）。")
        print("   处理：回到标注表，把这些部门相关的 X 模板补标完整，再重跑。")
        print(f"   交叉验证将仅纳入覆盖率 ≥ {MIN_COVERAGE}% 的部门。")
    else:
        print(f"\n✅ 全部部门覆盖率均 ≥ {MIN_COVERAGE}%，可进行部门层面比较。")

    show(con, "非解决型结案占比（总体）", """
    SELECT count(*) AS classified,
           sum(CASE WHEN code IN ('B','C','D','E','F','G') THEN 1 ELSE 0 END) AS non_resolving,
           round(100.0*avg(CASE WHEN code IN ('B','C','D','E','F','G')
                                THEN 1.0 ELSE 0 END), 2) AS nr_pct,
           round(100.0*avg(CASE WHEN code = 'A' THEN 1.0 ELSE 0 END), 2) AS resolving_pct
    FROM labeled WHERE code <> 'X'
    """, "I4_nr_overall.csv")


    show(con, "类别边界敏感性：D、G 移出非解决型后", """
    SELECT round(100.0*avg(CASE WHEN code IN ('B','C','E','F')
                                THEN 1.0 ELSE 0 END), 2) AS nr_pct_strict,
           round(100.0*avg(CASE WHEN code IN ('B','C','D','E','F','G')
                                THEN 1.0 ELSE 0 END), 2) AS nr_pct_default
    FROM labeled WHERE code <> 'X'
    """, "I5_boundary_sensitivity.csv")

    show(con, "§6.1 表 2：各部门的非解决型结案占比", """
    SELECT agency, count(*) AS classified,
           round(100.0*avg(CASE WHEN code IN ('B','C','D','E','F','G')
                                THEN 1.0 ELSE 0 END), 2) AS nr_pct
    FROM labeled WHERE code <> 'X'
    GROUP BY agency HAVING count(*) >= 5000
    ORDER BY nr_pct DESC
    """, "I6_nr_by_agency.csv")

    show(con, "§6.1 表 3：各类型的非解决型结案占比（前 25）", """
    SELECT complaint_type, any_value(agency) AS agency, count(*) AS classified,
           round(100.0*avg(CASE WHEN code IN ('B','C','D','E','F','G')
                                THEN 1.0 ELSE 0 END), 2) AS nr_pct
    FROM labeled WHERE code <> 'X'
    GROUP BY complaint_type HAVING count(*) >= 5000
    ORDER BY nr_pct DESC LIMIT 25
    """, "I7_nr_by_type.csv")


    tables = [r[0] for r in con.execute("SHOW TABLES").fetchall()]
    if "excess" not in tables:
        print("\n⚠️ 库里没有 excess 表，请先运行 step3_diagnose.py，再重跑 --apply")
        con.close()
        return

    print("\n" + "═" * 62)
    print("§6.4  交叉验证：两条路径是否指向同一机制  ← 全文最关键")
    print("═" * 62)

    for dim, minn, tag in [("agency", 5000, "agency"),
                           ("complaint_type", 5000, "type")]:
        con.execute(f"""
        CREATE OR REPLACE TABLE xv_{tag} AS
        WITH cov AS (
          SELECT {dim} AS dim
          FROM labeled GROUP BY 1
          HAVING avg(CASE WHEN code <> 'X' THEN 1.0 ELSE 0 END) >= {MIN_COVERAGE/100.0}
        ),
        nr AS (
          SELECT {dim} AS dim, count(*) AS n_text,
                 avg(CASE WHEN code IN ('B','C','D','E','F','G')
                          THEN 1.0 ELSE 0 END) AS nr
          FROM labeled WHERE code <> 'X'
          GROUP BY 1 HAVING count(*) >= {minn}
        ),
        ex AS (
          SELECT {dim} AS dim, count(*) AS n_beh, avg(excess) AS excess
          FROM excess GROUP BY 1 HAVING count(*) >= {minn}
        )
        SELECT nr.dim, nr.n_text, ex.n_beh,
               round(100.0*nr.nr, 2)     AS nr_pct,
               round(100.0*ex.excess, 2) AS excess_pct
        FROM nr JOIN ex USING (dim) JOIN cov USING (dim);
        """)

        show(con, f"散点图数据（按 {dim}）", f"""
        SELECT * FROM xv_{tag} ORDER BY nr_pct DESC
        """, f"I8_crossval_{tag}.csv")

        n = con.execute(f"SELECT count(*) FROM xv_{tag}").fetchone()[0]
        if n >= 4:
            show(con, f"Spearman 秩相关（按 {dim}，n={n}）", f"""
            WITH r AS (
              SELECT rank() OVER (ORDER BY nr_pct)     AS r1,
                     rank() OVER (ORDER BY excess_pct) AS r2
              FROM xv_{tag}
            )
            SELECT {n} AS n, round(corr(r1, r2), 4) AS spearman_rho
            FROM r
            """, f"I9_spearman_{tag}.csv")
        else:
            print(f"  样本量 {n} 过小，不做相关检验")

    print("\n" + "─" * 62)
    print("判读：")
    print("  rho 显著为正 → 两条路径互证同一机制，§6.4 成立，论文核心叙事闭环")
    print("  rho 接近 0   → 两条路径各自独立成立，需改写 §1.3 与 §6.4 的论证结构")
    print("  rho 为负     → 需重新检查指标定义，不可强行解释")
    print("\n散点图用 out/I8_crossval_agency.csv 在 Excel 里画：")
    print("  横轴 nr_pct，纵轴 excess_pct，每点标 dim 名称")
    con.close()


def gaps():


    if not os.path.exists(LABEL_FILE):
        print(f"❌ 找不到 {LABEL_FILE}")
        return

    label_src = normalize_csv(LABEL_FILE)
    con = connect()

    con.execute(f"""
    CREATE OR REPLACE TABLE lm AS
    SELECT template, upper(trim(CAST(code AS VARCHAR))) AS code
    FROM read_csv('{label_src}', header=true, all_varchar=true, ignore_errors=true);
    """)

    con.execute(f"""
    CREATE OR REPLACE TABLE gaps_t AS
    SELECT left(trim(r.resolution_description), {PREFIX_LEN}) AS template,
           any_value(r.agency)                                AS agency,
           count(*)                                           AS tickets,
           COALESCE(any_value(lm.code), '(未标注)')           AS code
    FROM read_parquet('{RAW}') r
    LEFT JOIN lm ON left(trim(r.resolution_description), {PREFIX_LEN}) = lm.template
    WHERE r.closed_date IS NOT NULL
      AND r.resolution_description IS NOT NULL
      AND trim(r.resolution_description) <> ''
    GROUP BY 1;
    """)

    total = con.execute("SELECT sum(tickets) FROM gaps_t").fetchone()[0]

    print("\n" + "═" * 62)
    print("缺口诊断：未归类工单的来源")
    print("═" * 62)

    show(con, "按部门统计未归类工单量", """
    SELECT agency,
           sum(tickets) AS total,
           sum(CASE WHEN code IN ('X','(未标注)') THEN tickets ELSE 0 END) AS unclassified,
           round(100.0*sum(CASE WHEN code IN ('X','(未标注)') THEN tickets ELSE 0 END)
                 / sum(tickets), 2) AS gap_pct
    FROM gaps_t GROUP BY agency
    HAVING sum(CASE WHEN code IN ('X','(未标注)') THEN tickets ELSE 0 END) > 0
    ORDER BY unclassified DESC
    """, "I10_gaps_by_agency.csv")

    show(con, "未归类的高工单量模板（前 30，优先补这些）", f"""
    SELECT tickets,
           round(100.0*tickets/{total}, 3) AS pct_of_all,
           agency, code,
           left(template, 160) AS template
    FROM gaps_t
    WHERE code IN ('X','(未标注)')
    ORDER BY tickets DESC LIMIT 30
    """, "I11_gap_templates.csv")

    print("\n↑ 逐条看 template，能判的填进标注表的 code 列。")
    print("  若前 250 字符仍未分叉，用 peek.py 查完整文本：")
    print('      python peek.py "模板里的几个词" --tail 200')
    print("  确实无法判定的才保留 X，并在the paper 4.1 报告其占比。")
    con.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--extract", action="store_true", help="生成待标注模板表")
    ap.add_argument("--apply",   action="store_true", help="应用标注并做交叉验证")
    ap.add_argument("--gaps",    action="store_true", help="诊断：哪些模板没归类、代价多大")
    args = ap.parse_args()

    if args.extract:
        extract()
    elif args.apply:
        apply_labels()
    elif args.gaps:
        gaps()
    else:
        ap.print_help()

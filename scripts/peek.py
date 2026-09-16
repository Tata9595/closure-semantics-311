import argparse
import duckdb

RAW = "data/raw/*.parquet"


def wrap(text, width=96, indent="   "):
    return "\n".join(indent + text[i:i + width] for i in range(0, len(text), width))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pattern")
    ap.add_argument("--full", action="store_true", help="完整显示不截断")
    ap.add_argument("--head", type=int, default=200)
    ap.add_argument("--tail", type=int, default=200)
    ap.add_argument("--top",  type=int, default=10)
    args = ap.parse_args()

    pat = args.pattern.replace("'", "''")   # 转义单引号

    con = duckdb.connect("nyc311.duckdb")
    con.execute("PRAGMA memory_limit='6GB'")

    # 关键：按 **完整文本** 分组，不按前缀
    rows = con.execute(f"""
        SELECT trim(resolution_description)          AS full_text,
               length(trim(resolution_description))  AS len,
               count(*)                              AS n,
               count(DISTINCT agency)                AS n_agency,
               any_value(agency)                     AS sample_agency
        FROM read_parquet('{RAW}')
        WHERE closed_date IS NOT NULL
          AND resolution_description ILIKE '%{pat}%'
        GROUP BY 1, 2
        ORDER BY n DESC
        LIMIT {args.top}
    """).fetchall()

    if not rows:
        print(f"未找到包含「{args.pattern}」的结案说明")
        con.close()
        return

    total_all = con.execute(f"""
        SELECT count(*), count(DISTINCT trim(resolution_description))
        FROM read_parquet('{RAW}')
        WHERE closed_date IS NOT NULL
          AND resolution_description ILIKE '%{pat}%'
    """).fetchone()

    print(f"\n匹配「{args.pattern}」：共 {total_all[0]:,} 张工单，"
          f"{total_all[1]:,} 个不同文本")
    print(f"以下为工单量最大的 {len(rows)} 个变体")
    print("═" * 102)

    for i, (text, ln, n, n_ag, ag) in enumerate(rows, 1):
        share = 100.0 * n / total_all[0]
        print(f"\n【{i}】{n:>10,} 张 ({share:5.1f}%)   "
              f"部门 {ag}{' 等 ' + str(n_ag) + ' 个' if n_ag > 1 else ''}   "
              f"全文长度 {ln} 字符")
        print("─" * 102)

        if args.full or ln <= args.head + args.tail:
            print(wrap(text))
        else:
            print(wrap(text[:args.head]))
            print(f"   …… 中略 {ln - args.head - args.tail} 字符 ……")
            print(wrap(text[-args.tail:]))

    print("\n" + "═" * 102)
    print("分类提示（看**结尾**，语义差别通常在那里）：")
    print("  corrected / repaired / removed / summons / violation issued / work completed  → A")
    print("  no evidence / not found / did not observe / no violation                      → B")
    print("  gone on arrival / those responsible were gone                                 → C")
    print("  not necessary / no further action required                                    → D")
    print("  unable to gain access / no access / no one home / unable to inspect           → E")
    print("  duplicate / already reported                                                  → F")
    print("  jurisdiction / referred to / website / please contact                         → G")
    print("\n若同一前缀下 A 和 B 两种走向都存在，说明 PREFIX_LEN 太短：")
    print("  把 step4_classify.py 里的 PREFIX_LEN 从 120 提到 200 或 250，重跑 --extract")
    con.close()


if __name__ == "__main__":
    main()

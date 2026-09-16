import argparse
import duckdb

ap = argparse.ArgumentParser()
ap.add_argument("addr", nargs="?", default="655 EAST 230", help="地址前缀")
ap.add_argument("--type", default="Noise - Residential", help="工单类型")
args = ap.parse_args()

addr = args.addr.replace("'", "''").upper()
ctype = args.type.replace("'", "''")

con = duckdb.connect("nyc311.duckdb")
con.execute("PRAGMA memory_limit='6GB'")

WHERE = f"addr_norm LIKE '{addr}%' AND complaint_type = '{ctype}'"

n = con.execute(f"SELECT count(*) FROM t WHERE {WHERE}").fetchone()[0]
print(f"\n查询：地址以「{addr}」开头，类型「{ctype}」")
print(f"共 {n:,} 张工单\n")
if n == 0:
    print("没有匹配记录，检查地址写法（注意标准化后是全大写、AVENUE→AVE）")
    con.close()
    raise SystemExit

# ── 小时分布 ──
print("═" * 56)
print("① 按小时分布")
print("═" * 56)
rows = con.execute(f"""
    SELECT hour(created_date) AS h, count(*) AS n
    FROM t WHERE {WHERE} GROUP BY 1 ORDER BY 1
""").fetchall()

mx = max(r[1] for r in rows) if rows else 1
for h, c in rows:
    bar = "█" * max(1, round(40 * c / mx))
    print(f"  {h:02d}:00  {c:>8,}  {bar}")

night = sum(c for h, c in rows if h >= 20 or h <= 2)
print(f"\n  夜间（20:00–02:00）占比：{100*night/n:.1f}%")
print(f"  若接近 29%（7/24），说明分布均匀，不符合真实噪音投诉的作息特征")

# ── 月度分布 ──
print("\n" + "═" * 56)
print("② 按月份分布")
print("═" * 56)
rows = con.execute(f"""
    SELECT strftime(created_date, '%Y-%m') AS m, count(*) AS n
    FROM t WHERE {WHERE} GROUP BY 1 ORDER BY 1
""").fetchall()

mx = max(r[1] for r in rows) if rows else 1
for m, c in rows:
    bar = "█" * max(1, round(40 * c / mx))
    print(f"  {m}  {c:>8,}  {bar}")

# ── 与该地址其他类型的对比 ──
print("\n" + "═" * 56)
print("③ 同一地址的其他工单类型")
print("═" * 56)
con.sql(f"""
    SELECT complaint_type, any_value(agency) AS agency, count(*) AS tickets
    FROM t WHERE addr_norm LIKE '{addr}%'
    GROUP BY 1 ORDER BY tickets DESC LIMIT 10
""").show(max_rows=10)
print("↑ 若只有一个类型畸高、其余正常，更像该类型的专用落点；")
print("  若多个类型都畸高，更像整体的地址默认值。")

# ── 结案说明分布 ──
print("═" * 56)
print("④ 这批工单的结案说明")
print("═" * 56)
con.sql(f"""
    SELECT left(trim(p.resolution_description), 80) AS resolution, count(*) AS n
    FROM t
    JOIN read_parquet('data/raw/*.parquet') p
      ON CAST(p.unique_key AS BIGINT) = t.unique_key
    WHERE t.addr_norm LIKE '{addr}%'
      AND t.complaint_type = '{ctype}'
      AND p.resolution_description IS NOT NULL
      AND trim(p.resolution_description) <> ''
    GROUP BY 1 ORDER BY n DESC LIMIT 6
""").show(max_rows=6)

# ── 与全市同类型的小时分布对比 ──
print("═" * 56)
print("⑤ 小时分布：该地址 vs 全市同类型")
print("═" * 56)
print("若两条曲线高度吻合，说明该地址是全市同类工单的汇集点，")
print("即其昼夜形态是继承来的，而非自身真实的噪音作息。\n")
con.sql(f"""
    WITH here AS (
      SELECT hour(created_date) AS h, count(*) AS n
      FROM t WHERE {WHERE} GROUP BY 1),
    city AS (
      SELECT hour(created_date) AS h, count(*) AS n
      FROM t WHERE complaint_type = '{ctype}' GROUP BY 1)
    SELECT here.h AS hour,
           round(100.0*here.n / (SELECT sum(n) FROM here), 2) AS here_pct,
           round(100.0*city.n / (SELECT sum(n) FROM city), 2) AS citywide_pct,
           round(100.0*here.n / (SELECT sum(n) FROM here)
                 - 100.0*city.n / (SELECT sum(n) FROM city), 2) AS diff
    FROM here JOIN city USING (h) ORDER BY hour
""").show(max_rows=24)

# ── 与次高组的比值 ──
print("═" * 56)
print("⑤ 该组在全部「位置+类型」组中的位置")
print("═" * 56)
con.sql(f"""
    WITH g AS (SELECT loc_a, complaint_type, count(*) AS cnt
               FROM t WHERE loc_a IS NOT NULL GROUP BY 1,2),
         r AS (SELECT *, row_number() OVER (ORDER BY cnt DESC) AS rk FROM g)
    SELECT rk, cnt AS tickets,
           round(cnt*1.0 / lead(cnt) OVER (ORDER BY rk), 2) AS ratio_to_next
    FROM r WHERE rk <= 5 ORDER BY rk
""").show(max_rows=5)
print("↑ ratio_to_next 是本组与下一名的倍数。若第 1 名远大于第 2 名（如 > 5 倍），")
print("  可用「超过次高组 N 倍」作为排除规则，比绝对阈值更有据可循。")

con.close()

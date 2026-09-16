"""
步骤 2：字段完整率统计 + 30 天复发率计算（无 pandas 依赖版）

用法：
    pip install duckdb --break-system-packages
    python step2_analyze.py

输出：
  屏幕打印全部结果
  同时把关键表写入 out/ 目录的 CSV，方便发给我看

判读标准：
  BBL 完整率 > 80%    → §4.1 走分支 A
  复发率落在 5%–20%   → 选题落地
  复发率 ≈ 0 或 ≈ 100% → 识别方法要改，看第 6 节诊断输出
"""

import os
import duckdb

RAW         = "data/raw/*.parquet"
WINDOW_DAYS = 30
OUT         = "out"
MEM_LIMIT   = "6GB"      # 按你的内存调整，8GB 机器建议 5GB
THREADS     = 4

os.makedirs(OUT, exist_ok=True)
con = duckdb.connect("nyc311.duckdb")
con.execute(f"PRAGMA memory_limit='{MEM_LIMIT}'")
con.execute(f"PRAGMA threads={THREADS}")


def show(title, sql, csv=None):
    """打印结果表；给了 csv 名就同时落盘"""
    print(f"\n{title}")
    con.sql(sql).show(max_rows=40)
    if csv:
        con.execute(f"COPY ({sql}) TO '{OUT}/{csv}' (HEADER, DELIMITER ',')")


# ═════════════════════════════════════════════
# 1. 规范化视图
# ═════════════════════════════════════════════
print("正在建规范化表，首次运行需要几分钟…")
con.execute(f"""
CREATE OR REPLACE TABLE base AS
SELECT
    CAST(unique_key AS BIGINT)              AS unique_key,
    TRY_CAST(created_date AS TIMESTAMP)     AS created_date,
    TRY_CAST(closed_date  AS TIMESTAMP)     AS closed_date,
    agency,
    complaint_type,
    descriptor,
    resolution_description,
    borough,
    NULLIF(TRIM(bbl), '')                   AS bbl,
    TRY_CAST(latitude  AS DOUBLE)           AS lat,
    TRY_CAST(longitude AS DOUBLE)           AS lon,
    -- 地址标准化：大写、压空格、统一常见后缀
    NULLIF(
      regexp_replace(
        regexp_replace(
          regexp_replace(upper(TRIM(incident_address)), '\\s+', ' ', 'g'),
          '\\b(STREET)\\b', 'ST', 'g'),
        '\\b(AVENUE)\\b', 'AVE', 'g')
    , '')                                   AS addr_norm
FROM read_parquet('{RAW}');
""")

n_total = con.execute("SELECT count(*) FROM base").fetchone()[0]
print(f"原始工单数：{n_total:,}")

# ═════════════════════════════════════════════
# 2. A组：位置字段完整率 ← 决定 A/B 分支
# ═════════════════════════════════════════════
print("\n" + "═" * 60)
print("A. 位置字段完整率")
print("═" * 60)

show("完整率", f"""
SELECT
  round(100.0*count(bbl)/{n_total}, 2)                          AS bbl_pct,
  round(100.0*count(addr_norm)/{n_total}, 2)                    AS addr_pct,
  round(100.0*count(CASE WHEN lat IS NOT NULL AND lon IS NOT NULL
                    THEN 1 END)/{n_total}, 2)                   AS latlon_pct,
  round(100.0*count(CASE WHEN bbl IS NOT NULL OR addr_norm IS NOT NULL
                          OR (lat IS NOT NULL AND lon IS NOT NULL)
                    THEN 1 END)/{n_total}, 2)                   AS any_pct
FROM base
""", "A1_completeness.csv")

show("唯一位置数（判断标准化是否过度合并）", """
SELECT count(DISTINCT bbl)       AS uniq_bbl,
       count(DISTINCT addr_norm) AS uniq_addr
FROM base
""", "A2_unique_locations.csv")

# ═════════════════════════════════════════════
# 3. 分析表：位置主键
# ═════════════════════════════════════════════
con.execute("""
CREATE OR REPLACE TABLE t AS
SELECT *,
       COALESCE('BBL:' || bbl, 'ADR:' || addr_norm)   AS loc_a,
       CASE WHEN lat IS NOT NULL AND lon IS NOT NULL
            THEN 'G:' || CAST(floor(lat/0.0009) AS BIGINT)
                 || '_' || CAST(floor(lon/0.0012) AS BIGINT)
       END                                            AS loc_b
FROM base
WHERE created_date IS NOT NULL AND complaint_type IS NOT NULL;
""")

# ═════════════════════════════════════════════
# 4. B组：数据概况
# ═════════════════════════════════════════════
print("\n" + "═" * 60)
print("B. 数据概况")
print("═" * 60)

show("概况", """
SELECT count(*)                                    AS tickets,
       count(DISTINCT agency)                      AS agencies,
       count(DISTINCT complaint_type)              AS types,
       round(100.0*count(closed_date)/count(*), 2) AS closure_rate_pct,
       min(created_date)::DATE                     AS date_from,
       max(created_date)::DATE                     AS date_to
FROM t
""", "B1_overview.csv")

# ═════════════════════════════════════════════
# 5. C组：30 天复发率 ← 核心变量
# ═════════════════════════════════════════════
def recur_sql(loc, extra_group=""):
    g = f", c.{extra_group}" if extra_group else ""
    return f"""
    WITH closed AS (
      SELECT * FROM t
      WHERE closed_date IS NOT NULL
        AND closed_date >= created_date
        AND {loc} IS NOT NULL
    ),
    flagged AS (
      SELECT c.unique_key{g},
             MAX(CASE WHEN n.unique_key IS NOT NULL THEN 1 ELSE 0 END) AS recurred
      FROM closed c
      LEFT JOIN t n
        ON  n.{loc}          = c.{loc}
        AND n.complaint_type = c.complaint_type
        AND n.unique_key    <> c.unique_key
        AND n.created_date  >  c.closed_date
        AND n.created_date  <= c.closed_date + INTERVAL {WINDOW_DAYS} DAY
      GROUP BY ALL
    )"""


print("\n" + "═" * 60)
print(f"C. {WINDOW_DAYS} 天复发率  ← 核心变量")
print("═" * 60)

show("分支 A：BBL / 标准化地址", recur_sql("loc_a") + """
SELECT count(*) AS closed_tickets, sum(recurred) AS recurred,
       round(100.0*avg(recurred), 2) AS recur_rate_pct FROM flagged
""", "C1_recur_branchA.csv")

show("分支 B：100 米网格", recur_sql("loc_b") + """
SELECT count(*) AS closed_tickets, sum(recurred) AS recurred,
       round(100.0*avg(recurred), 2) AS recur_rate_pct FROM flagged
""", "C2_recur_branchB.csv")

# ═════════════════════════════════════════════
# 6. 按部门分解（论文 §6.2 预览）
# ═════════════════════════════════════════════
print("\n" + "═" * 60)
print("D. 按责任部门的复发率（分支 A，工单数 ≥ 5000）")
print("═" * 60)

show("部门分解", recur_sql("loc_a", "agency") + """
SELECT agency, count(*) AS tickets,
       round(100.0*avg(recurred), 2) AS recur_rate_pct
FROM flagged GROUP BY agency HAVING count(*) >= 5000
ORDER BY recur_rate_pct DESC
""", "D1_by_agency.csv")

print("\n" + "═" * 60)
print("E. 按工单类型的复发率（前 20，工单数 ≥ 5000）")
print("═" * 60)

show("类型分解", recur_sql("loc_a", "complaint_type") + """
SELECT complaint_type, count(*) AS tickets,
       round(100.0*avg(recurred), 2) AS recur_rate_pct
FROM flagged GROUP BY complaint_type HAVING count(*) >= 5000
ORDER BY recur_rate_pct DESC LIMIT 20
""", "E1_by_type.csv")

# ═════════════════════════════════════════════
# 7. 诊断
# ═════════════════════════════════════════════
print("\n" + "═" * 60)
print("F. 诊断：单个「位置+类型」组的工单数分布")
print("═" * 60)

show("组大小分位数", """
SELECT quantile_cont(cnt, 0.50) AS p50,
       quantile_cont(cnt, 0.90) AS p90,
       quantile_cont(cnt, 0.99) AS p99,
       max(cnt)                 AS max_group
FROM (SELECT loc_a, complaint_type, count(*) AS cnt
      FROM t WHERE loc_a IS NOT NULL GROUP BY 1,2)
""", "F1_group_sizes.csv")

print("↑ max_group 若达数万，说明存在超大聚合位置（公园/地铁站等），")
print("  会人为抬高复发率，需在论文 §6.6 作为失效案例报告。")

# 结案原因的粗分类 —— 论文 §4.2 的补充证据
print("\n" + "═" * 60)
print("G. 最常见的结案说明（前 15）")
print("═" * 60)

show("结案说明分布", """
SELECT left(resolution_description, 90) AS resolution_snippet,
       count(*) AS n,
       round(100.0*count(*)/(SELECT count(*) FROM t WHERE closed_date IS NOT NULL), 2) AS pct
FROM t WHERE closed_date IS NOT NULL AND resolution_description IS NOT NULL
GROUP BY 1 ORDER BY n DESC LIMIT 15
""", "G1_resolutions.csv")

print("↑ 注意「不归本部门管辖」「现场未发现问题」这类非解决型结案的占比，")
print("  可作为实质解决率之外的佐证维度写进论文。")

con.close()
print(f"\n完成。CSV 已写入 {OUT}/ ，数据库存为 nyc311.duckdb")

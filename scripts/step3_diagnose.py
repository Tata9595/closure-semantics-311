"""
步骤 3：诊断 —— 为什么复发率高达 46.76%，以及如何修正

回答三个问题：
  ① 超大组到底是什么？（最大组 173,945 张工单）
  ② 复发率在不同组大小下如何变化？
  ③ 扣掉地点基础繁忙度后，「超额复发」是多少？

直接连 step2 建好的 nyc311.duckdb，不用重跑建表。

用法：
    python step3_diagnose.py
"""

import os
import duckdb

OUT         = "out"
WINDOW_DAYS = 30
TOTAL_DAYS  = 1096          # 2023-01-01 ~ 2025-12-31

os.makedirs(OUT, exist_ok=True)
con = duckdb.connect("nyc311.duckdb")
con.execute("PRAGMA memory_limit='6GB'")
con.execute("PRAGMA threads=4")


def show(title, sql, csv=None):
    print(f"\n{title}")
    con.sql(sql).show(max_rows=40)
    if csv:
        con.execute(f"COPY ({sql}) TO '{OUT}/{csv}' (HEADER, DELIMITER ',')")


# ═════════════════════════════════════════════
# ① 超大组是什么？
# ═════════════════════════════════════════════
print("═" * 62)
print("① 最大的 20 个「位置+类型」组")
print("═" * 62)

show("超大组", """
SELECT loc_a, complaint_type, agency, count(*) AS n,
       any_value(addr_norm) AS sample_address,
       any_value(borough)   AS borough
FROM t WHERE loc_a IS NOT NULL
GROUP BY loc_a, complaint_type, agency
ORDER BY n DESC LIMIT 20
""", "H1_super_groups.csv")

print("↑ 看 sample_address。若是公园、地铁站、大型公寓、或明显是占位地址，")
print("  说明位置粒度相对该地点的工单量过粗，需在论文中处理。")


# ═════════════════════════════════════════════
# ② 复发率随组大小的变化
# ═════════════════════════════════════════════
print("\n" + "═" * 62)
print("② 复发率 × 组大小分层  ← 关键诊断")
print("═" * 62)

con.execute(f"""
CREATE OR REPLACE TABLE flagged AS
WITH grp AS (
  SELECT loc_a, complaint_type, count(*) AS grp_n
  FROM t WHERE loc_a IS NOT NULL
  GROUP BY 1, 2
),
closed AS (
  SELECT * FROM t
  WHERE closed_date IS NOT NULL
    AND closed_date >= created_date
    AND loc_a IS NOT NULL
)
SELECT c.unique_key, c.agency, c.complaint_type, c.loc_a, g.grp_n,
       MAX(CASE WHEN n.unique_key IS NOT NULL THEN 1 ELSE 0 END) AS recurred
FROM closed c
JOIN grp g ON g.loc_a = c.loc_a AND g.complaint_type = c.complaint_type
LEFT JOIN t n
  ON  n.loc_a          = c.loc_a
  AND n.complaint_type = c.complaint_type
  AND n.unique_key    <> c.unique_key
  AND n.created_date  >  c.closed_date
  AND n.created_date  <= c.closed_date + INTERVAL {WINDOW_DAYS} DAY
GROUP BY ALL;
""")

show("按组大小分层", """
SELECT CASE
         WHEN grp_n = 1              THEN '1'
         WHEN grp_n <= 3             THEN '2-3'
         WHEN grp_n <= 10            THEN '4-10'
         WHEN grp_n <= 50            THEN '11-50'
         WHEN grp_n <= 200           THEN '51-200'
         WHEN grp_n <= 1000          THEN '201-1000'
         ELSE '1000+'
       END                                        AS group_size,
       count(*)                                   AS tickets,
       round(100.0*count(*)/sum(count(*)) OVER (), 2) AS share_pct,
       round(100.0*avg(recurred), 2)              AS recur_rate_pct
FROM flagged
GROUP BY 1
ORDER BY min(grp_n)
""", "H2_by_group_size.csv")

print("↑ 若 1000+ 那一层复发率接近 100%、且工单占比很大，")
print("  就确认了 46.76% 是被超大组拉起来的。")


# ═════════════════════════════════════════════
# ③ 超额复发：扣掉地点基础繁忙度
# ═════════════════════════════════════════════
print("\n" + "═" * 62)
print("③ 超额复发  ← 修正后的核心指标")
print("═" * 62)
print("对每个「位置+类型」组，在其基础到访率下 30 天内本就该出现后续工单的概率：")
print(f"    P_expected = 1 - exp(-λ·{WINDOW_DAYS})，  λ = 组内工单数 / {TOTAL_DAYS}")
print("    超额复发 = 实际复发 - P_expected")

con.execute(f"""
CREATE OR REPLACE TABLE excess AS
SELECT *,
       1 - exp(-1.0 * grp_n / {TOTAL_DAYS} * {WINDOW_DAYS}) AS p_expected,
       recurred - (1 - exp(-1.0 * grp_n / {TOTAL_DAYS} * {WINDOW_DAYS})) AS excess
FROM flagged;
""")

show("总体", """
SELECT count(*)                          AS closed_tickets,
       round(100.0*avg(recurred),   2)   AS observed_pct,
       round(100.0*avg(p_expected), 2)   AS expected_pct,
       round(100.0*avg(excess),     2)   AS excess_pct
FROM excess
""", "H3_excess_overall.csv")

show("按部门（工单数 ≥ 5000，按超额降序）", """
SELECT agency, count(*) AS tickets,
       round(100.0*avg(recurred),   2) AS observed_pct,
       round(100.0*avg(p_expected), 2) AS expected_pct,
       round(100.0*avg(excess),     2) AS excess_pct
FROM excess GROUP BY agency HAVING count(*) >= 5000
ORDER BY excess_pct DESC
""", "H4_excess_by_agency.csv")

show("按工单类型（工单数 ≥ 5000，超额最高的 20 类）", """
SELECT complaint_type, any_value(agency) AS agency, count(*) AS tickets,
       round(100.0*avg(recurred),   2) AS observed_pct,
       round(100.0*avg(p_expected), 2) AS expected_pct,
       round(100.0*avg(excess),     2) AS excess_pct
FROM excess GROUP BY complaint_type HAVING count(*) >= 5000
ORDER BY excess_pct DESC LIMIT 20
""", "H5_excess_by_type_top.csv")

show("按工单类型（超额最低的 20 类）", """
SELECT complaint_type, any_value(agency) AS agency, count(*) AS tickets,
       round(100.0*avg(recurred),   2) AS observed_pct,
       round(100.0*avg(p_expected), 2) AS expected_pct,
       round(100.0*avg(excess),     2) AS excess_pct
FROM excess GROUP BY complaint_type HAVING count(*) >= 5000
ORDER BY excess_pct ASC LIMIT 20
""", "H6_excess_by_type_bottom.csv")


# ═════════════════════════════════════════════
# ④ 稳健性：剔除超大组后的原始复发率
# ═════════════════════════════════════════════
print("\n" + "═" * 62)
print("④ 稳健性：剔除超大组后的原始复发率")
print("═" * 62)

show("按组大小上限截断", """
SELECT '不设限'   AS cap, count(*) AS tickets,
       round(100.0*avg(recurred), 2) AS recur_rate_pct FROM flagged
UNION ALL SELECT '组≤1000', count(*), round(100.0*avg(recurred),2)
  FROM flagged WHERE grp_n <= 1000
UNION ALL SELECT '组≤200',  count(*), round(100.0*avg(recurred),2)
  FROM flagged WHERE grp_n <= 200
UNION ALL SELECT '组≤50',   count(*), round(100.0*avg(recurred),2)
  FROM flagged WHERE grp_n <= 50
UNION ALL SELECT '组≤10',   count(*), round(100.0*avg(recurred),2)
  FROM flagged WHERE grp_n <= 10
""", "H7_capped.csv")


# ═════════════════════════════════════════════
# ⑤ 结案说明分布（旧版脚本缺这块）
# ═════════════════════════════════════════════
print("\n" + "═" * 62)
print("⑤ 最常见的结案说明（前 20）")
print("═" * 62)

show("结案说明", """
SELECT left(resolution_description, 100) AS resolution_snippet,
       count(*) AS n,
       round(100.0*count(*) / (SELECT count(*)
                               FROM read_parquet('data/raw/*.parquet')
                               WHERE closed_date IS NOT NULL), 2) AS pct
FROM read_parquet('data/raw/*.parquet')
WHERE closed_date IS NOT NULL
  AND resolution_description IS NOT NULL
  AND trim(resolution_description) <> ''
GROUP BY 1 ORDER BY n DESC LIMIT 20
""", "H8_resolutions.csv")

print("↑ 关注「不归本部门管辖」「到场时已无异常」这类非解决型结案的占比。")

con.close()
print(f"\n完成。CSV 已写入 {OUT}/")

import os
import duckdb

RAW         = "data/raw/*.parquet"
WINDOW_DAYS = 30
OUT         = "out"
MEM_LIMIT   = "6GB"
THREADS     = 4

os.makedirs(OUT, exist_ok=True)
con = duckdb.connect("nyc311.duckdb")
con.execute(f"PRAGMA memory_limit='{MEM_LIMIT}'")
con.execute(f"PRAGMA threads={THREADS}")


def show(title, sql, csv=None):

    print(f"\n{title}")
    con.sql(sql).show(max_rows=40)
    if csv:
        con.execute(f"COPY ({sql}) TO '{OUT}/{csv}' (HEADER, DELIMITER ',')")


print("Building the normalised table; the first run takes a few minutes...")
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
print(f"Raw tickets: {n_total:,}")


print("\n" + "═" * 60)
print("A. Location field completeness")
print("═" * 60)

show("Completeness", f"""
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

show("Distinct locations (checks that normalisation did not over-merge)", """
SELECT count(DISTINCT bbl)       AS uniq_bbl,
       count(DISTINCT addr_norm) AS uniq_addr
FROM base
""", "A2_unique_locations.csv")


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


print("\n" + "═" * 60)
print("B. Dataset overview")
print("═" * 60)

show("Overview", """
SELECT count(*)                                    AS tickets,
       count(DISTINCT agency)                      AS agencies,
       count(DISTINCT complaint_type)              AS types,
       round(100.0*count(closed_date)/count(*), 2) AS closure_rate_pct,
       min(created_date)::DATE                     AS date_from,
       max(created_date)::DATE                     AS date_to
FROM t
""", "B1_overview.csv")


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
print(f"C. Raw {WINDOW_DAYS}-day recurrence")
print("═" * 60)

show("Key A: tax lot, falling back to normalised address", recur_sql("loc_a") + """
SELECT count(*) AS closed_tickets, sum(recurred) AS recurred,
       round(100.0*avg(recurred), 2) AS recur_rate_pct FROM flagged
""", "C1_recur_branchA.csv")

show("Key B: 100 m grid (robustness comparison)", recur_sql("loc_b") + """
SELECT count(*) AS closed_tickets, sum(recurred) AS recurred,
       round(100.0*avg(recurred), 2) AS recur_rate_pct FROM flagged
""", "C2_recur_branchB.csv")


print("\n" + "═" * 60)
print("D. Recurrence by agency (key A, groups of 5000+ tickets)")
print("═" * 60)

show("By agency", recur_sql("loc_a", "agency") + """
SELECT agency, count(*) AS tickets,
       round(100.0*avg(recurred), 2) AS recur_rate_pct
FROM flagged GROUP BY agency HAVING count(*) >= 5000
ORDER BY recur_rate_pct DESC
""", "D1_by_agency.csv")

print("\n" + "═" * 60)
print("E. Recurrence by complaint type (top 20, 5000+ tickets)")
print("═" * 60)

show("By complaint type", recur_sql("loc_a", "complaint_type") + """
SELECT complaint_type, count(*) AS tickets,
       round(100.0*avg(recurred), 2) AS recur_rate_pct
FROM flagged GROUP BY complaint_type HAVING count(*) >= 5000
ORDER BY recur_rate_pct DESC LIMIT 20
""", "E1_by_type.csv")


print("\n" + "═" * 60)
print("F. Diagnostic: distribution of location-type group sizes")
print("═" * 60)

show("Group-size quantiles", """
SELECT quantile_cont(cnt, 0.50) AS p50,
       quantile_cont(cnt, 0.90) AS p90,
       quantile_cont(cnt, 0.99) AS p99,
       max(cnt)                 AS max_group
FROM (SELECT loc_a, complaint_type, count(*) AS cnt
      FROM t WHERE loc_a IS NOT NULL GROUP BY 1,2)
""", "F1_group_sizes.csv")

print("If max_group reaches tens of thousands, an over-aggregated location")
print("(a park, a transit hub, or a geocoding default) is inflating recurrence.")


print("\n" + "═" * 60)
print("G. Most frequent resolution descriptions (top 15)")
print("═" * 60)

show("Resolution description frequency", """
SELECT left(resolution_description, 90) AS resolution_snippet,
       count(*) AS n,
       round(100.0*count(*)/(SELECT count(*) FROM t WHERE closed_date IS NOT NULL), 2) AS pct
FROM t WHERE closed_date IS NOT NULL AND resolution_description IS NOT NULL
GROUP BY 1 ORDER BY n DESC LIMIT 15
""", "G1_resolutions.csv")

print("Note the share of non-resolving closures such as \"not within our")
print("jurisdiction\" or \"no evidence observed\"; step4 classifies these.")

con.close()
print(f"\nDone. CSV written to {OUT}/ ; database saved as nyc311.duckdb")

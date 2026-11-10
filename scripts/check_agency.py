import duckdb
from revision_analyses import EXTRA_AGENCY as assumed

con = duckdb.connect("nyc311.duckdb", read_only=True)
placeholders = ",".join(["?"] * len(assumed))
rows = con.execute(f"""
    SELECT complaint_type, agency, count(*) AS n
    FROM t
    WHERE complaint_type IN ({placeholders})
    GROUP BY 1, 2
    ORDER BY 1, 3 DESC
""", list(assumed)).fetchall()

bad = 0
for ctype, actual, n in rows:
    flag = "" if assumed[ctype] == actual else "   <-- MISMATCH"
    bad += assumed[ctype] != actual
    print(f"{ctype:45s} assumed={assumed[ctype]:5s} actual={str(actual):6s} n={n:>9,}{flag}")

found = {r[0] for r in rows}
missing = sorted(set(assumed) - found)
print(f"\n{len(found)} of {len(assumed)} types found, {bad} mismatching rows")
if missing:
    print("not found in table t:", missing)
if bad == 0 and not missing and len(rows) == len(found):
    print("All assignments are correct; no change needed.")
else:
    print("Please send this output back so the agency-adjusted results can be recomputed.")

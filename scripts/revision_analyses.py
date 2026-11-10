import sys, os, glob, csv, math
import numpy as np

OUT = sys.argv[1] if len(sys.argv) > 1 else "out"
SEED, B = 20261110, 20000

EXTRA_AGENCY = {
    "Building/Use": "DOB", "Special Projects Inspection Team (SPIT)": "DOB", "General Construction/Plumbing": "DOB",
    "Boilers": "DOB", "Elevator": "DOB",
    "ELECTRIC": "HPD", "GENERAL": "HPD", "SAFETY": "HPD", "FLOORING/STAIRS": "HPD", "WATER LEAK": "HPD",
    "Encampment": "NYPD", "Non-Emergency Police Matter": "NYPD", "Noise - Vehicle": "NYPD", "Noise - Park": "NYPD",
    "Blocked Driveway": "NYPD", "Noise - Street/Sidewalk": "NYPD", "Noise - Commercial": "NYPD",
    "Curb Condition": "DOT", "Broken Parking Meter": "DOT", "Sidewalk Condition": "DOT", "Street Sign - Missing": "DOT",
    "Street Condition": "DOT", "Street Sign - Damaged": "DOT",
    "Animal in a Park": "DPR", "Damaged Tree": "DPR", "Dead/Dying Tree": "DPR",
    "Dirty Condition": "DSNY", "Illegal Dumping": "DSNY", "Street Sweeping Complaint": "DSNY",
    "Missed Collection": "DSNY", "Graffiti": "DSNY", "Illegal Posting": "DSNY",
}



def read_csv(path, encodings=("utf-8-sig", "utf-8", "gbk")):
    for enc in encodings:
        try:
            with open(path, newline="", encoding=enc) as f:
                return list(csv.DictReader(f))
        except (UnicodeDecodeError, UnicodeError):
            continue
    raise RuntimeError(f"cannot decode {path}")


def rank(x, method="average"):
    x = np.asarray(x, float)
    order = np.argsort(x, kind="mergesort")
    xs = x[order]
    r = np.empty(len(x))
    i = 0
    while i < len(x):
        j = i
        while j + 1 < len(x) and xs[j + 1] == xs[i]:
            j += 1
        r[order[i:j + 1]] = (i + j) / 2 + 1 if method == "average" else i + 1
        i = j + 1
    return r


def pearson(a, b):
    return float(np.corrcoef(a, b)[0, 1])


def spearman(a, b):
    return pearson(rank(a), rank(b))


def norm_sf(z):
    return 0.5 * math.erfc(z / math.sqrt(2))


def _betacf(a, b, x):
    qab, qap, qam = a + b, a + 1, a - 1
    c, d = 1.0, 1 - qab * x / qap
    d = 1 / (d if abs(d) > 1e-300 else 1e-300)
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1 + aa * d; d = 1 / (d if abs(d) > 1e-300 else 1e-300)
        c = 1 + aa / c if abs(1 + aa / c) > 1e-300 else 1e-300
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1 + aa * d; d = 1 / (d if abs(d) > 1e-300 else 1e-300)
        c = 1 + aa / c if abs(1 + aa / c) > 1e-300 else 1e-300
        de = d * c
        h *= de
        if abs(de - 1) < 1e-12:
            break
    return h


def betainc(a, b, x):
    if x <= 0: return 0.0
    if x >= 1: return 1.0
    lbt = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log(1 - x)
    if x < (a + 1) / (a + b + 2):
        return math.exp(lbt) * _betacf(a, b, x) / a
    return 1 - math.exp(lbt) * _betacf(b, a, 1 - x) / b


def t_two_sided_p(t, df):
    return betainc(df / 2, 0.5, df / (df + t * t))


def steiger(r12, r13, r23, n):
    """Steiger (1980) Z for two dependent correlations sharing one variable (Meng-Rosenthal-Rubin form)."""
    z12, z13 = math.atanh(r12), math.atanh(r13)
    rm2 = (r12 ** 2 + r13 ** 2) / 2
    f = min((1 - r23) / (2 * (1 - rm2)), 1)
    h = (1 - f * rm2) / (1 - rm2)
    z = (z13 - z12) * math.sqrt((n - 3) / (2 * (1 - r23) * h))
    return z, 2 * norm_sf(abs(z))



def load_types():
    def col(path, key):
        return {r["complaint_type"]: float(r[key]) for r in read_csv(path)}
    wide = col(f"{OUT}/M6_scatter_wide.csv", "nr_pct")
    mid = col(f"{OUT}/M6_scatter_mid.csv", "nr_pct")
    strict = col(f"{OUT}/M6_scatter_strict.csv", "nr_pct")
    excess = col(f"{OUT}/M6_scatter_wide.csv", "excess_pct")
    amap = {}
    for f in glob.glob(f"{OUT}/*.csv"):
        if os.path.basename(f).startswith("R1_"):   # skip this script's own output
            continue
        try:
            rows = read_csv(f)
        except Exception:
            continue
        if rows and "complaint_type" in rows[0] and "agency" in rows[0]:
            for r in rows:
                amap[r["complaint_type"]] = r["agency"]
    types = list(wide)
    agency = [amap.get(t, EXTRA_AGENCY.get(t)) for t in types]
    source = ["out/*.csv" if t in amap else "EXTRA_AGENCY" for t in types]
    return (types, np.array([wide[t] for t in types]), np.array([mid[t] for t in types]),
            np.array([strict[t] for t in types]), np.array([excess[t] for t in types]), agency, source)


def main():
    types, wide, mid, strict, y, agency, source = load_types()
    n = len(types)
    X = {"wide": wide, "mid": mid, "strict": strict}
    print(f"types: {n}; agency missing: {sum(a is None for a in agency)}; "
          f"agency from EXTRA_AGENCY: {source.count('EXTRA_AGENCY')}")

    print("\n[1] identity check (primary setting: Poisson, 30 days, placeholder included)")
    a_share = np.round(100 - wide, 2)
    print(f"  minimum ranks: wide {pearson(rank(wide, 'min'), rank(y, 'min')):.4f}  "
          f"A {pearson(rank(a_share, 'min'), rank(y, 'min')):.4f}")
    print(f"  average ranks: wide {spearman(wide, y):.4f}  A {spearman(a_share, y):.4f}")
    print("  average-rank primary values:", {k: round(spearman(v, y), 4) for k, v in X.items()})

    print(f"\n[2] bootstrap over types ({B} replicates) and Steiger test")
    rng = np.random.default_rng(SEED)
    keys = ["wide", "mid", "strict", "mid-wide", "strict-wide"]
    bs = {k: np.empty(B) for k in keys}
    for b in range(B):
        i = rng.integers(0, n, n)
        r = {k: spearman(v[i], y[i]) for k, v in X.items()}
        for k in X:
            bs[k][b] = r[k]
        bs["mid-wide"][b] = r["mid"] - r["wide"]
        bs["strict-wide"][b] = r["strict"] - r["wide"]
        if (b + 1) % 5000 == 0:
            print(f"    ... {b + 1} replicates")
    for k in keys:
        lo, hi = np.nanpercentile(bs[k], [2.5, 97.5])
        print(f"  {k:12s} 95% [{lo:+.3f}, {hi:+.3f}]")
    r = {k: spearman(v, y) for k, v in X.items()}
    for other in ["mid", "strict"]:
        z, p = steiger(r["wide"], r[other], spearman(wide, X[other]), n)
        print(f"  Steiger {other} vs wide: Z = {z:.2f}, p = {p:.3f}")

    print("\n[3] agency")
    levels = sorted(set(agency))
    D = np.array([[1.0 if a == lv else 0.0 for lv in levels] for a in agency])
    def resid(v):
        beta, *_ = np.linalg.lstsq(D, v, rcond=None)
        return v - D @ beta
    dfree = n - 2 - (D.shape[1] - 1)
    for k, v in X.items():
        pr = pearson(resid(rank(v)), resid(rank(y)))
        p = t_two_sided_p(pr * math.sqrt(dfree / (1 - pr ** 2)), dfree)
        print(f"  partial rho | agency  {k:6s} {pr:+.3f}  p = {p:.4f}")
    agency = np.array(agency)
    for lv in levels:
        m = agency == lv
        if m.sum() >= 8:
            print(f"  within {lv:5s} n={m.sum():2d} " +
                  "  ".join(f"{k}={spearman(v[m], y[m]):+.3f}" for k, v in X.items()))
    g = wide - mid
    print("  mean G share (%) by agency:")
    for lv in sorted(levels, key=lambda a: -g[agency == a].mean()):
        print(f"    {lv:6s} types={int((agency == lv).sum()):2d}  mean G = {g[agency == lv].mean():5.1f}")

    print("\n[4] Cohen's kappa (164 templates)")
    sheet = {r["idx"]: r for r in read_csv(f"{OUT}/L1_kappa_sheet_utf8.csv")}
    pre = {r["idx"]: r["code"] for r in read_csv(f"{OUT}/I1_templates_to_label_bak.csv", ("gbk", "utf-8-sig"))}
    post = {r["idx"]: r["code"] for r in read_csv(f"{OUT}/I1_templates_to_label_utf8.csv")}
    idx = [i for i in sheet if i in pre and i in post]
    wts = np.array([float(sheet[i]["tickets"]) for i in idx])
    c2 = np.array([sheet[i]["code2"] for i in idx])
    for label, src in [("before adjudication", pre), ("after adjudication", post)]:
        c1 = np.array([src[i] for i in idx])
        cats = sorted(set(c1) | set(c2))
        for wname, wt in [("template", np.ones_like(wts)), ("ticket", wts)]:
            po = wt[c1 == c2].sum() / wt.sum()
            pe = sum((wt[c1 == c].sum() / wt.sum()) * (wt[c2 == c].sum() / wt.sum()) for c in cats)
            print(f"  {label:20s} {wname:8s}-weighted  Po={po:.4f}  Pe={pe:.4f}  kappa={(po - pe) / (1 - pe):.3f}")
        print(f"  disagreements {label}: {(c1 != c2).sum()}")

    with open(f"{OUT}/R1_type74_with_agency.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["complaint_type", "agency", "agency_source", "wide", "mid", "strict", "excess_pct"])
        for row in zip(types, agency, source, wide, mid, strict, y):
            w.writerow(row)
    print(f"\nwrote {OUT}/R1_type74_with_agency.csv")


if __name__ == "__main__":
    main()

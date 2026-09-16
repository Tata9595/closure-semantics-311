"""
make_tables.py — 一次生成论文全部表格的 LaTeX 源码

输入：out/ 目录下的 CSV
输出：out/tables.tex —— 可直接 \\input 进 ACM 模板

用法：
    python make_tables.py

说明：
  · 表格用 booktabs 宏包（ACM 模板已内置），需 \\usepackage{booktabs}
  · 宽表用 table* 环境跨栏
  · 全部列名与表注为英文，可直接投稿
"""

import os
import csv

IN, OUT = "out", "out/tables.tex"


def load(name):
    path = os.path.join(IN, name)
    if not os.path.exists(path):
        print(f"  ⚠️ 缺少 {name}")
        return None
    raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return list(csv.DictReader(raw.decode(enc).splitlines()))
        except UnicodeDecodeError:
            continue
    return None


def esc(x):
    return str(x).replace("&", "\\&").replace("%", "\\%").replace("_", "\\_")


def table(label, caption, header, rows, align, wide=False, note=None):
    env = "table*" if wide else "table"
    out = [f"\\begin{{{env}}}[t]", "\\centering",
           f"\\caption{{{caption}}}", f"\\label{{tab:{label}}}",
           f"\\begin{{tabular}}{{{align}}}", "\\toprule",
           " & ".join(header) + " \\\\", "\\midrule"]
    for r in rows:
        out.append(" & ".join(str(c) for c in r) + " \\\\")
    out += ["\\bottomrule", "\\end{tabular}"]
    if note:
        out.append(f"\\\\[2pt]\\footnotesize{{{note}}}")
    out.append(f"\\end{{{env}}}")
    return "\n".join(out) + "\n\n"


EN_CAT = {"A": "Substantive action", "B": "No evidence found",
          "C": "Responsible party gone", "D": "No action necessary",
          "E": "No access", "F": "Duplicate",
          "G": "Referred / informational"}

blocks = []

# ───── T1 结案语义分布 ─────
r = load("I3_category_distribution.csv")
if r:
    rows = [[c["code"], EN_CAT.get(c["code"], ""),
             f"{int(c['tickets']):,}", f"{float(c['pct']):.2f}"] for c in r]
    tot = sum(float(c["pct"]) for c in r if c["code"] != "A")
    rows.append(["\\multicolumn{2}{l}{\\textit{Non-resolution (B--G)}}",
                 "", f"\\textbf{{{tot:.2f}}}"])
    blocks.append(table(
        "closure_categories",
        "Distribution of closure semantics across 9.32M classified closed tickets "
        "(coverage 92.2\\%).",
        ["Code", "Category", "Tickets", "Share (\\%)"],
        rows, "llrr",
        note="Classified via 164 templates after two-rater adjudication "
             "(Cohen's $\\kappa$ = 0.766 before adjudication)."))

# ───── T2 部门覆盖率 ─────
r = load("I2b_coverage_by_agency.csv")
if r:
    r = [x for x in r if int(x["closed_with_text"]) >= 1000]
    r.sort(key=lambda x: -float(x["coverage_pct"]))
    rows = [[esc(x["agency"]), f"{int(x['closed_with_text']):,}",
             f"{int(x['classified']):,}", f"{float(x['coverage_pct']):.2f}",
             "Yes" if float(x["coverage_pct"]) >= 80 else "No"] for x in r]
    blocks.append(table(
        "coverage",
        "Classification coverage by agency.",
        ["Agency", "Closed w/ text", "Classified", "Coverage (\\%)", "Included"],
        rows, "lrrrc",
        note="Agencies below the 80\\% threshold are excluded from "
             "agency-level comparison; type-level analysis is unaffected."))

# ───── T3 复发率 vs 组大小 ─────
r = load("H2_by_group_size.csv")
if r:
    rows = [[esc(x["group_size"]), f"{int(x['tickets']):,}",
             f"{float(x['share_pct']):.2f}",
             f"{float(x['recur_rate_pct']):.2f}"] for x in r]
    blocks.append(table(
        "groupsize",
        "Raw 30-day recurrence rate by location--type group size.",
        ["Group size", "Tickets", "Share (\\%)", "Recurrence (\\%)"],
        rows, "lrrr",
        note="The near-perfect monotone relationship shows that unadjusted "
             "recurrence measures location density, not handling quality."))

# ───── T4 截断后的复发率 ─────
r = load("H7_capped.csv")
if r:
    rows = [[esc(x["cap"]), f"{int(x['tickets']):,}",
             f"{float(x['recur_rate_pct']):.2f}"] for x in r]
    blocks.append(table(
        "capped",
        "Recurrence rate after capping group size.",
        ["Cap", "Tickets", "Recurrence (\\%)"], rows, "lrr"))

# ───── T5 两种零模型总体 ─────
r = load("J3_null_model_overall.csv")
if r:
    x = r[0]
    rows = [["Observed recurrence", f"{float(x['observed_pct']):.2f}",
             f"{float(x['observed_pct']):.2f}"],
            ["Expected under null", f"{float(x['poisson_expected_pct']):.2f}",
             f"{float(x['empirical_expected_pct']):.2f}"],
            ["\\textbf{Excess recurrence (pp)}",
             f"\\textbf{{{float(x['excess_poisson_pct']):.2f}}}",
             f"\\textbf{{{float(x['excess_empirical_pct']):.2f}}}"]]
    blocks.append(table(
        "nullmodel",
        "Citywide excess recurrence under two null models.",
        ["Quantity", "Poisson null", "Empirical null"], rows, "lrr",
        note="The Poisson null assumes uniform arrivals and therefore "
             "overestimates expected recurrence for bursty complaint streams, "
             "understating excess by a factor of 3.9."))

# ───── T6 部门超额（两种零模型） ─────
r = load("J4_null_model_by_agency.csv")
if r:
    r.sort(key=lambda x: -float(x["excess_empirical"]))
    rows = [[esc(x["agency"]), f"{int(x['tickets']):,}",
             f"{float(x['observed_pct']):.2f}",
             f"{float(x['excess_poisson']):.2f}",
             f"{float(x['excess_empirical']):.2f}"] for x in r]
    blocks.append(table(
        "agency_excess",
        "Excess recurrence by agency under both null models.",
        ["Agency", "Tickets", "Observed (\\%)",
         "Excess, Poisson (pp)", "Excess, empirical (pp)"],
        rows, "lrrrr", wide=True,
        note="Rank correlation between the two excess columns is about 0.55; "
             "agency ordering is therefore null-model dependent."))

# ───── T7 超额最高的类型 ─────
r = load("H5_excess_by_type_top.csv")
if r:
    rows = [[esc(x["complaint_type"]), esc(x["agency"]),
             f"{int(x['tickets']):,}", f"{float(x['observed_pct']):.2f}",
             f"{float(x['expected_pct']):.2f}",
             f"{float(x['excess_pct']):.2f}"] for x in r[:10]]
    blocks.append(table(
        "type_excess",
        "Complaint types with the highest excess recurrence "
        "($\\Delta$ = 30 days, Poisson null).",
        ["Complaint type", "Agency", "Tickets", "Observed (\\%)",
         "Expected (\\%)", "Excess (pp)"], rows, "llrrrr", wide=True,
        note="Presented as a descriptive finding; it does not by itself "
             "establish a mechanism."))

# ───── T8 三种口径的 ρ ─────
r = load("K1_rho_strict_vs_wide_type.csv")
if r:
    from collections import defaultdict
    agg = defaultdict(list)
    prim = {}
    for x in r:
        agg[x["nr_scope"]].append(float(x["rho"]))
        if x["window_days"] == "30" and x["null_model"] == "poisson":
            prim[x["nr_scope"]] = float(x["rho"])
    name = {"宽 B-G": ("Wide", "B,C,D,E,F,G", "62.36"),
            "严格 B,C,E,F": ("Strict", "B,C,E,F", "46.64")}
    rows = []
    for k, v in agg.items():
        nm, dfn, share = name.get(k, (k, "", ""))
        rows.append([nm, dfn, share, f"{prim.get(k, 0):.3f}",
                     f"{sum(v)/len(v):.3f}",
                     f"{min(v):.3f}--{max(v):.3f}"])
    # 中间口径来自 L2
    l2 = load("L2_three_scopes_summary.csv")
    if l2:
        for x in l2:
            if "B-F" in x["nr_scope"] and "G" not in x["nr_scope"]:
                rows.insert(1, ["\\textbf{Middle}", "\\textbf{B,C,D,E,F}",
                                "\\textbf{56.47}", "\\textbf{0.343}",
                                f"\\textbf{{{float(x['rho_mean']):.3f}}}",
                                f"\\textbf{{{float(x['rho_min']):.3f}--"
                                f"{float(x['rho_max']):.3f}}}"])
    blocks.append(table(
        "rho_scopes",
        "Spearman rank correlation between non-resolution share and excess "
        "recurrence, by scope definition ($n$ = 74 complaint types).",
        ["Scope", "Categories", "Share (\\%)", "Primary $\\rho$",
         "Mean $\\rho$", "Range"], rows, "llrrrr", wide=True,
        note="Primary setting: $\\Delta$ = 30 days, Poisson null. "
             "Six settings: five windows (7/14/30/60/90 days, Poisson) "
             "plus the empirical null at 30 days."))

# ───── T9 逐类别 ρ ─────
r = load("K4_rho_per_category.csv")
if r:
    r.sort(key=lambda x: -float(x["rho"]))
    rows = []
    for x in r:
        p = float(x["p_approx"])
        star = "$^{***}$" if p < .001 else "$^{**}$" if p < .01 \
            else "$^{*}$" if p < .05 else ""
        rows.append([x["code"], EN_CAT.get(x["code"], ""),
                     f"{float(x['rho']):.4f}{star}", f"{p:.4f}"])
    blocks.append(table(
        "rho_category",
        "Rank correlation of each individual category share with excess "
        "recurrence ($\\Delta$ = 30 days, Poisson null, $n$ = 74).",
        ["Code", "Category", "$\\rho$", "$p$"], rows, "llrr",
        note="$^{*}p<.05$, $^{**}p<.01$, $^{***}p<.001$. Under Bonferroni "
             "correction across seven tests, only category C remains "
             "robustly significant."))

# ───── T10 位置粒度 ─────
r = load("J2_location_granularity.csv")
if r:
    en = {"地块编号优先": "Tax lot (BBL) first", "100 米网格": "100\\,m grid"}
    rows = [[en.get(x["scheme"], esc(x["scheme"])), f"{int(x['tickets']):,}",
             f"{float(x['observed_pct']):.2f}",
             f"{float(x['expected_pct']):.2f}",
             f"{float(x['excess_pct']):.2f}"] for x in r]
    blocks.append(table(
        "granularity",
        "Sensitivity to the spatial key ($\\Delta$ = 30 days, Poisson null).",
        ["Location key", "Tickets", "Observed (\\%)",
         "Expected (\\%)", "Excess (pp)"], rows, "lrrrr",
        note="The grid scheme yields higher raw recurrence but lower excess, "
             "reconfirming that raw recurrence is driven by aggregation "
             "granularity."))

# ───── T11 边界敏感性 ─────
r = load("I5_boundary_sensitivity.csv")
if r:
    x = r[0]
    rows = [["Default (B--G)", f"{float(x['nr_pct_default']):.2f}"],
            ["Strict (B, C, E, F)", f"{float(x['nr_pct_strict']):.2f}"]]
    blocks.append(table(
        "boundary",
        "Sensitivity of the non-resolution share to category boundaries.",
        ["Definition", "Non-resolution share (\\%)"], rows, "lr"))

# ───── 写出 ─────
os.makedirs(IN, exist_ok=True)
with open(OUT, "w", encoding="utf-8") as f:
    f.write("% ══════════════════════════════════════════════\n")
    f.write("% 本文件由 make_tables.py 自动生成，勿手工编辑\n")
    f.write("% 使用方法：在 ACM 模板正文中 \\input{tables}\n")
    f.write("% 依赖宏包：\\usepackage{booktabs}\n")
    f.write("% ══════════════════════════════════════════════\n\n")
    f.write("".join(blocks))

print(f"✅ 已生成 {OUT}")
print(f"   共 {len(blocks)} 张表")
print("\n引用方式示例：as shown in Table~\\ref{tab:rho_scopes}")

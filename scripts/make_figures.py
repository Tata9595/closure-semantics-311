"""
make_figures.py — 一次生成论文的全部图

输入：out/ 目录下由 step3–step8 产出的 CSV
输出：out/fig/ 下的 PNG（300 dpi，英文标注，可直接投稿）

用法：
    pip install matplotlib pandas --break-system-packages
    python make_figures.py

生成：
    fig1_recurrence_vs_groupsize.png   复发率 vs 组大小（对数横轴）
    fig2_scatter_scopes.png            三种口径 vs 超额复发 散点
    fig3_rho_by_window.png             秩相关随观察窗变化
    fig4_category_distribution.png     七类结案分布
    fig5_excess_by_agency.png          各部门超额复发（两种零模型）
    fig6_rho_per_category.png          逐类别相关系数
    fig7_null_model_comparison.png     两种零模型对比
    fig8_coverage_by_agency.png        各部门归类覆盖率
"""

import os
import csv
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

IN = "out"
FIG = "out/fig"
os.makedirs(FIG, exist_ok=True)

DPI = 300
plt.rcParams.update({
    "font.size": 10,
    "axes.grid": True,
    "grid.alpha": 0.25,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "figure.autolayout": False,
})

BLUE, RED, GRAY, ORANGE = "#2c6fbb", "#c0392b", "#7f8c8d", "#e08214"


def load(name):
    """读 CSV，自动处理编码；文件不存在则返回 None"""
    path = os.path.join(IN, name)
    if not os.path.exists(path):
        print(f"  ⚠️ 缺少 {name}，跳过相关图")
        return None
    raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    return list(csv.DictReader(text.splitlines()))


def save(fig, name):
    path = os.path.join(FIG, name)
    fig.savefig(path, dpi=DPI, bbox_inches="tight")
    plt.close(fig)
    print(f"  ✅ {path}")


# ═══════════ 图 1：复发率 vs 组大小 ═══════════
def fig1():
    rows = load("H2_by_group_size.csv")
    if not rows:
        return
    # 用各区间的几何中点作为横轴位置
    mid = {"1": 1, "2-3": 2.4, "4-10": 6.3, "11-50": 23,
           "51-200": 101, "201-1000": 448, "1000+": 2000}
    xs, ys, labs, share = [], [], [], []
    for r in rows:
        g = r["group_size"]
        if g in mid:
            xs.append(mid[g])
            ys.append(float(r["recur_rate_pct"]))
            labs.append(g)
            share.append(float(r["share_pct"]))

    fig, ax = plt.subplots(figsize=(6.4, 4.2))
    ax.plot(xs, ys, marker="o", color=BLUE, linewidth=1.8, markersize=7, zorder=3)
    for x, y, l in zip(xs, ys, labs):
        ax.annotate(l, (x, y), textcoords="offset points",
                    xytext=(0, -16), ha="center", fontsize=8, color=GRAY)
    ax.set_xscale("log")
    ax.set_xlabel("Tickets per location–type group (log scale)")
    ax.set_ylabel("30-day recurrence rate (%)")
    ax.set_ylim(-5, 105)
    ax.set_title("Raw recurrence tracks group size almost perfectly", fontsize=11)
    save(fig, "fig1_recurrence_vs_groupsize.png")


# ═══════════ 图 2：三种口径散点 ═══════════
def fig2():
    panels = [("K6_scatter_wide_type.csv", "Wide (B–G)", 0.122),
              ("K5_scatter_strict_type.csv", "Strict (B,C,E,F)", 0.331)]
    mid = load("M6_scatter_mid.csv")
    if mid:
        panels.insert(1, ("M6_scatter_mid.csv", "Middle (B–F)", 0.343))

    data = [(load(f), t, r) for f, t, r in panels]
    data = [(d, t, r) for d, t, r in data if d]
    if not data:
        return

    fig, axes = plt.subplots(1, len(data), figsize=(5.0 * len(data), 4.4),
                             sharey=True)
    if len(data) == 1:
        axes = [axes]
    for ax, (rows, title, rho) in zip(axes, data):
        xs = [float(r["nr_pct"]) for r in rows]
        ys = [float(r["excess_pct"]) for r in rows]
        ax.scatter(xs, ys, s=26, alpha=0.6, color=BLUE, edgecolors="none", zorder=3)
        mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
        den = sum((x - mx) ** 2 for x in xs)
        if den:
            b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den
            a = my - b * mx
            lo, hi = min(xs), max(xs)
            ax.plot([lo, hi], [a + b * lo, a + b * hi],
                    color=RED, linewidth=1.5, zorder=4)
        ax.axhline(0, color=GRAY, linewidth=0.7, linestyle="--", zorder=1)
        ax.set_title(f"{title}\nSpearman $\\rho$ = {rho:.3f}  (n = {len(xs)})",
                     fontsize=10)
        ax.set_xlabel("Non-resolution share (%)")
    axes[0].set_ylabel("Excess recurrence (pp)")
    fig.suptitle("Predictive power depends on how non-resolution is defined",
                 fontsize=12, y=1.02)
    save(fig, "fig2_scatter_scopes.png")


# ═══════════ 图 3：秩相关随观察窗 ═══════════
def fig3():
    rows = load("K1_rho_strict_vs_wide_type.csv")
    if not rows:
        return
    series = {}
    for r in rows:
        if r["null_model"] != "poisson":
            continue
        series.setdefault(r["nr_scope"], []).append(
            (int(r["window_days"]), float(r["rho"])))

    name = {"宽 B-G": "Wide (B–G)", "严格 B,C,E,F": "Strict (B,C,E,F)",
            "中间 B-F": "Middle (B–F)"}
    color = {"宽 B-G": ORANGE, "严格 B,C,E,F": BLUE, "中间 B-F": RED}

    fig, ax = plt.subplots(figsize=(6.4, 4.2))
    for k, pts in series.items():
        pts.sort()
        ax.plot([p[0] for p in pts], [p[1] for p in pts],
                marker="o", linewidth=1.8, markersize=6,
                label=name.get(k, k), color=color.get(k, GRAY), zorder=3)
    ax.axhline(0, color=GRAY, linewidth=0.7, linestyle="--")
    ax.set_xlabel("Observation window $\\Delta$ (days)")
    ax.set_ylabel("Spearman $\\rho$")
    ax.set_xticks([7, 14, 30, 60, 90])
    ax.legend(frameon=False, fontsize=9)
    ax.set_title("Correlation decays monotonically as the window widens",
                 fontsize=11)
    save(fig, "fig3_rho_by_window.png")


# ═══════════ 图 4：七类结案分布 ═══════════
def fig4():
    rows = load("I3_category_distribution.csv")
    if not rows:
        return
    en = {"A": "Substantive action", "B": "No evidence found",
          "C": "Responsible party gone", "D": "No action necessary",
          "E": "No access", "F": "Duplicate", "G": "Referred / informational"}
    rows = sorted(rows, key=lambda r: float(r["pct"]))
    labs = [f"{r['code']}  {en.get(r['code'], '')}" for r in rows]
    vals = [float(r["pct"]) for r in rows]
    cols = [RED if r["code"] == "A" else BLUE for r in rows]

    fig, ax = plt.subplots(figsize=(7.2, 4.0))
    bars = ax.barh(labs, vals, color=cols, height=0.62, zorder=3)
    for b, v in zip(bars, vals):
        ax.text(v + 0.5, b.get_y() + b.get_height() / 2,
                f"{v:.2f}%", va="center", fontsize=9)
    ax.set_xlabel("Share of classified closed tickets (%)")
    ax.set_xlim(0, max(vals) * 1.16)
    ax.grid(axis="y", visible=False)
    ax.set_title("Only 37.6% of closures record substantive action",
                 fontsize=11)
    save(fig, "fig4_category_distribution.png")


# ═══════════ 图 5：各部门超额复发（两种零模型） ═══════════
def fig5():
    rows = load("J4_null_model_by_agency.csv")
    if not rows:
        return
    rows = sorted(rows, key=lambda r: float(r["excess_empirical"]))
    ag = [r["agency"] for r in rows]
    poi = [float(r["excess_poisson"]) for r in rows]
    emp = [float(r["excess_empirical"]) for r in rows]
    y = range(len(ag))
    h = 0.38

    fig, ax = plt.subplots(figsize=(7.0, 5.2))
    ax.barh([i + h / 2 for i in y], emp, height=h, color=BLUE,
            label="Empirical null (circular shift)", zorder=3)
    ax.barh([i - h / 2 for i in y], poi, height=h, color=ORANGE,
            label="Poisson null", zorder=3)
    ax.axvline(0, color="black", linewidth=0.8)
    ax.set_yticks(list(y))
    ax.set_yticklabels(ag)
    ax.set_xlabel("Excess recurrence (pp)")
    ax.legend(frameon=False, fontsize=9, loc="lower right")
    ax.grid(axis="y", visible=False)
    ax.set_title("Agency ranking depends on the choice of null model",
                 fontsize=11)
    save(fig, "fig5_excess_by_agency.png")


# ═══════════ 图 6：逐类别相关系数 ═══════════
def fig6():
    rows = load("K4_rho_per_category.csv")
    if not rows:
        return
    en = {"A": "A  Substantive action", "B": "B  No evidence found",
          "C": "C  Responsible party gone", "D": "D  No action necessary",
          "E": "E  No access", "F": "F  Duplicate",
          "G": "G  Referred / informational"}
    rows = sorted(rows, key=lambda r: float(r["rho"]))
    labs = [en.get(r["code"], r["code"]) for r in rows]
    vals = [float(r["rho"]) for r in rows]
    ps = [float(r["p_approx"]) for r in rows]
    cols = [BLUE if v > 0 else ORANGE for v in vals]

    fig, ax = plt.subplots(figsize=(7.2, 4.0))
    bars = ax.barh(labs, vals, color=cols, height=0.62, zorder=3)
    for b, v, p in zip(bars, vals, ps):
        star = "***" if p < 0.001 else "**" if p < 0.01 else "*" if p < 0.05 else ""
        off = 0.012 if v >= 0 else -0.012
        ax.text(v + off, b.get_y() + b.get_height() / 2,
                f"{v:.3f}{star}", va="center",
                ha="left" if v >= 0 else "right", fontsize=9)
    ax.axvline(0, color="black", linewidth=0.8)
    ax.set_xlabel("Spearman $\\rho$ with excess recurrence")
    ax.set_xlim(min(vals) - 0.10, max(vals) + 0.12)
    ax.grid(axis="y", visible=False)
    ax.set_title("Which closure categories carry signal  "
                 "(* p<.05, ** p<.01, *** p<.001)", fontsize=10.5)
    save(fig, "fig6_rho_per_category.png")


# ═══════════ 图 7：两种零模型对比 ═══════════
def fig7():
    rows = load("J3_null_model_overall.csv")
    if not rows:
        return
    r = rows[0]
    obs = float(r["observed_pct"])
    poi_e = float(r["poisson_expected_pct"])
    emp_e = float(r["empirical_expected_pct"])
    poi_x = float(r["excess_poisson_pct"])
    emp_x = float(r["excess_empirical_pct"])

    fig, ax = plt.subplots(figsize=(6.6, 3.6))
    labels = ["Poisson null", "Empirical null\n(circular shift)"]
    ypos = [1, 0]
    ax.barh(ypos, [poi_e, emp_e], height=0.45, color=GRAY,
            label="Expected (baseline arrival)", zorder=3)
    ax.barh(ypos, [poi_x, emp_x], left=[poi_e, emp_e], height=0.45,
            color=RED, label="Excess", zorder=3)
    for yp, e, x in zip(ypos, [poi_e, emp_e], [poi_x, emp_x]):
        ax.text(e / 2, yp, f"{e:.2f}%", va="center", ha="center",
                color="white", fontsize=9)
        ax.text(e + x / 2, yp, f"+{x:.2f}", va="center", ha="center",
                color="white", fontsize=9, fontweight="bold")
    ax.axvline(obs, color="black", linewidth=1.1, linestyle=":")
    ax.text(obs + 0.4, 1.45, f"observed {obs:.2f}%", fontsize=9)
    ax.set_yticks(ypos)
    ax.set_yticklabels(labels)
    ax.set_xlabel("Recurrence rate (%)")
    ax.set_xlim(0, obs * 1.14)
    ax.legend(frameon=False, fontsize=9, loc="upper center",
              bbox_to_anchor=(0.5, -0.22), ncol=2)
    ax.grid(axis="y", visible=False)
    ax.set_ylim(-0.6, 1.6)
    ax.set_title("The null model choice changes excess recurrence 3.9-fold",
                 fontsize=11)
    save(fig, "fig7_null_model_comparison.png")


# ═══════════ 图 8：各部门归类覆盖率 ═══════════
def fig8():
    rows = load("I2b_coverage_by_agency.csv")
    if not rows:
        return
    rows = [r for r in rows if int(r["closed_with_text"]) >= 1000]
    rows = sorted(rows, key=lambda r: float(r["coverage_pct"]))
    ag = [r["agency"] for r in rows]
    cov = [float(r["coverage_pct"]) for r in rows]
    cols = [BLUE if c >= 80 else ORANGE for c in cov]

    fig, ax = plt.subplots(figsize=(6.8, 4.6))
    bars = ax.barh(ag, cov, color=cols, height=0.62, zorder=3)
    for b, c in zip(bars, cov):
        ax.text(c + 1.2, b.get_y() + b.get_height() / 2,
                f"{c:.1f}%", va="center", fontsize=9)
    ax.axvline(80, color=RED, linewidth=1.2, linestyle="--", zorder=4)
    ax.text(80.8, -0.75, "80% threshold", color=RED, fontsize=9)
    ax.set_xlabel("Classification coverage (%)")
    ax.set_xlim(0, 112)
    ax.grid(axis="y", visible=False)
    ax.set_title("Coverage is uneven across agencies", fontsize=11)
    save(fig, "fig8_coverage_by_agency.png")


if __name__ == "__main__":
    print("生成论文图表…\n")
    for fn in (fig1, fig2, fig3, fig4, fig5, fig6, fig7, fig8):
        try:
            fn()
        except Exception as e:
            print(f"  ❌ {fn.__name__} 失败：{type(e).__name__}: {e}")
    print(f"\n完成。全部 PNG 位于 {FIG}/（300 dpi，英文标注）")

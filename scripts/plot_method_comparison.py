#!/usr/bin/env python3
"""
Publication-quality method-comparison figures for the LoadMatch LP + GA workflow.

Figures produced
----------------
  fig1_overview   — Cleveland dot plot: normalised annual cost, all 29 regions
  fig2_pathway    — Optimisation pathway: cost progression across all four cases
  fig3_exemplar   — Detailed cost bars + optimised-parameter heatmap for one region

Usage (run from repo root)
--------------------------
    python -m scripts.plot_method_comparison
    python -m scripts.plot_method_comparison --exemplar EUROPE --highlight UNITED-STATES CHINA
    python -m scripts.plot_method_comparison --output figures/method_comparison/
"""
from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib as mpl
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
import pandas as pd
from matplotlib.colors import TwoSlopeNorm
from matplotlib.patches import ConnectionPatch

# ── Global style ──────────────────────────────────────────────────────────────
mpl.rcParams.update({
    "font.family":       "sans-serif",
    "font.sans-serif":   ["Helvetica", "Arial", "Liberation Sans", "DejaVu Sans"],
    "font.size":         8,
    "axes.titlesize":    9,
    "axes.labelsize":    8,
    "xtick.labelsize":   7,
    "ytick.labelsize":   7,
    "legend.fontsize":   7,
    "figure.dpi":        300,
    "axes.spines.top":   False,
    "axes.spines.right": False,
    "axes.linewidth":    0.8,
    "lines.linewidth":   1.5,
    "lines.markersize":  4,
    "xtick.major.width": 0.8,
    "ytick.major.width": 0.8,
    "xtick.minor.width": 0.5,
    "ytick.minor.width": 0.5,
    "patch.linewidth":   0.5,
})

# Okabe-Ito CVD-safe palette
C = {
    "Baseline": "#888888",
    "LP":       "#0072B2",
    "GA (bl)":  "#E69F00",
    "GA (LP)":  "#009E73",
}
MK   = {"Baseline": "D", "LP": "s", "GA (bl)": "^", "GA (LP)": "o"}
MKSZ = {"Baseline": 18,  "LP": 18,  "GA (bl)": 20,  "GA (LP)": 18}
CASES = ["Baseline", "LP", "GA (bl)", "GA (LP)"]
LBL   = {
    "Baseline": "Baseline",
    "LP":       "LP warm-start",
    "GA (bl)":  "GA (baseline start)",
    "GA (LP)":  "GA (LP warm-start)",
}

FACTOR_KEYS = [
    "FACONWIN", "FACOFFWIN", "FACUTILPV", "FACRESPV",
    "FACCOMPV", "CSPTURBFAC", "FACSHT",   "BATDISCH",
    "STORHBAT", "STORHCOLD", "STORHHWAT", "STORHPHS",
    "STORUGDYS","DAYH2STOR", "CPERFORM",  "FCDISCH", "FCCHARG",
]
FACTOR_LBL = {
    "FACONWIN":  "Onshore wind CF",
    "FACOFFWIN": "Offshore wind CF",
    "FACUTILPV": "Utility PV CF",
    "FACRESPV":  "Rooftop PV CF",
    "FACCOMPV":  "CPV CF",
    "CSPTURBFAC":"CSP turbine CF",
    "FACSHT":    "Solar heat CF",
    "BATDISCH":  "Battery discharge (GW)",
    "STORHBAT":  "Battery storage (h)",
    "STORHCOLD": "Cold TES (h)",
    "STORHHWAT": "Hot-water TES (h)",
    "STORHPHS":  "PHS storage (h)",
    "STORUGDYS": "Underground stor. (d)",
    "DAYH2STOR": "H₂ storage (d)",
    "CPERFORM":  "Heat-pump COP",
    "FCDISCH":   "Fuel-cell discharge (GW)",
    "FCCHARG":   "Fuel-cell charge (GW)",
}
# Parameter categories (from PARAM_REGISTRY in run_full_workflow.py)
FACTOR_CATS = {
    "FACONWIN": "capacity", "FACOFFWIN": "capacity", "FACUTILPV": "capacity",
    "FACRESPV": "capacity", "FACCOMPV":  "capacity", "CSPTURBFAC": "capacity",
    "FACSHT":   "capacity",
    "BATDISCH": "tw",       "FCDISCH":   "tw",       "FCCHARG":   "tw",
    "STORHBAT": "hours",    "STORHCOLD": "hours",    "STORHHWAT": "hours",
    "STORHPHS": "hours",
    "STORUGDYS": "days",    "DAYH2STOR": "days",
    "CPERFORM":  "cop",
}
# countrystats.dat column that holds the reference installed MW for each capacity factor
FACTOR_REFCOL = {
    "FACONWIN":  "TMWONWIND",
    "FACOFFWIN": "TWOFFWIND",   # column name has no 'M'
    "FACUTILPV": "TMWUTILPV",
    "FACRESPV":  "TMWRESPV",
    "FACCOMPV":  "TMWCOMPV",
    "CSPTURBFAC":"TMWCSPORIG",
    "FACSHT":    "TMWSOLTH",
}

def _load_ref_gw(region: str, countrystats: Path) -> dict[str, float]:
    """Return reference installed GW per capacity-factor key for a region."""
    cs = pd.read_csv(countrystats, sep="\t", skiprows=1)
    grp = cs[cs["GRID-REGION"] == region]
    return {
        key: float(grp[col].sum()) / 1000.0
        for key, col in FACTOR_REFCOL.items()
        if col in cs.columns
    }

# Energy-mix sources: (display label, CSV column, intuitive CVD-safe color)
GEN_SOURCES = [
    ("Wind",       "Generation (TWh/yr)/Wind",       "#56B4E9"),  # sky blue
    ("Solar",      "Generation (TWh/yr)/Solar",      "#F0E442"),  # yellow
    ("Solar heat", "Generation (TWh/yr)/Solar heat", "#E69F00"),  # orange
    ("Hydro",      "Generation (TWh/yr)/Hydro",      "#009E73"),  # blue-green
    ("Geo (elec)", "Generation (TWh/yr)/Geo (elec)", "#D55E00"),  # vermillion
    ("Geo (heat)", "Generation (TWh/yr)/Geo (heat)", "#CC79A7"),  # reddish purple
    ("Wave",       "Generation (TWh/yr)/Wave",       "#0072B2"),  # deep blue
    ("Tidal",      "Generation (TWh/yr)/Tidal",      "#332288"),  # dark blue
]


# Cost waterfall: category → CSV sub-labels (mirrors COST_GROUPS in plot_results.py)
_COST_PREFIX  = "Cost by category (c/kWh, MN)/"
_END_ENERGY   = "End use & load (TWh/yr)/End-energy gen"
_COST_GROUPS  = {
    "Electricity gen.": ["ELECTRICITY GEN ONLY", "ADDED-HYDRO-TURBS"],
    "Heat gen.":        ["SOLAR+GEOTHERM HEAT"],
    "Battery storage":  ["LI-BATTERY STORAGE"],
    "H2 electricity":   ["H2-ELEC PROD/STOR/FC"],
    "Other storage":    ["CSPPCM + PHS STORAGE", "CWSTES+PCMICE STOR",
                         "HWSTES STORAGE", "UTES STORAGE",
                         "HPUMPS FOR HWST+UTES", "INDUS FIREBRICK STOR"],
    "H2 production":    ["H2-PROD/COMP/STOR"],
    "T&D":              ["SHORT-DIST TRANSMISS", "LONG-DIST-TRANS", "DISTRIBUTION"],
}
# CVD-safe delta bar colors (Paul Tol Muted — distinct from all four case colors)
_CLR_DOWN = "#117733"   # dark green  → cost reduction
_CLR_UP   = "#CC6677"   # muted red   → cost increase


# ── Data helpers ──────────────────────────────────────────────────────────────
def load(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path).rename(columns={
        "Identification/Region":               "region",
        "Identification/Case":                 "case",
        "Identification/Feasible":             "feasible",
        "Annual cost ($B/yr)/Cost LO ($B/yr)": "cost_lo",
        "Annual cost ($B/yr)/Cost MN ($B/yr)": "cost_mn",
        "Annual cost ($B/yr)/Cost HI ($B/yr)": "cost_hi",
    })
    df = df.rename(columns={
        c: c.replace("Optimised factors/", "") for c in df.columns
    })
    df["feasible"] = df["feasible"].astype(str).str.strip().str.lower() == "true"
    return df[df["feasible"]].copy()


def _wide(df: pd.DataFrame, metric: str) -> pd.DataFrame:
    """Pivot to region × case table for a single metric."""
    return df.pivot_table(
        index="region", columns="case", values=metric, aggfunc="first"
    ).reindex(columns=CASES)


def _save(fig: plt.Figure, stem: Path, dpi: int = 300) -> None:
    stem.parent.mkdir(parents=True, exist_ok=True)
    for ext in ("pdf", "png"):
        p = stem.with_suffix(f".{ext}")
        fig.savefig(p, dpi=dpi, bbox_inches="tight")
        print(f"  Saved {p}")
    plt.close(fig)


def _legend_handles() -> list:
    return [
        mpl.lines.Line2D([], [], color=C[c], marker=MK[c], linestyle="",
                         markersize=5, label=LBL[c])
        for c in CASES
    ]


# ── Figure 1: Cleveland dot plot — all regions ────────────────────────────────
def plot_overview(df: pd.DataFrame, out: Path) -> None:
    mn = _wide(df, "cost_mn")
    lo = _wide(df, "cost_lo")
    hi = _wide(df, "cost_hi")

    bl = mn["Baseline"]
    mn_n = mn.div(bl, axis=0)
    lo_n = lo.div(bl, axis=0)
    hi_n = hi.div(bl, axis=0)

    order = sorted(bl.index.tolist(), reverse=True)
    mn_n, lo_n, hi_n = mn_n.loc[order], lo_n.loc[order], hi_n.loc[order]
    n = len(order)
    y = np.arange(n)

    fig, ax = plt.subplots(figsize=(3.5, max(2.625, n * 0.27)))

    for case in CASES:
        if case not in mn_n.columns:
            continue
        mn_v = mn_n[case].values
        lo_v = lo_n[case].values
        hi_v = hi_n[case].values
        for i, (lv, hv) in enumerate(zip(lo_v, hi_v)):
            if np.isfinite(lv) and np.isfinite(hv):
                ax.plot([lv, hv], [y[i], y[i]],
                        color=C[case], lw=0.7, alpha=0.35, zorder=2)
        ax.scatter(mn_v, y, color=C[case], marker=MK[case],
                   s=MKSZ[case], zorder=4, linewidths=0)

    ax.axvline(1.0, color="#AAAAAA", lw=0.8, ls="--")
    ax.text(1.002, n - 0.5, "Baseline", ha="left", va="top",
            fontsize=6.5, color="#888888")

    ax.set_yticks(y)
    ax.set_yticklabels(
        [r.replace("-", "‑") for r in order], fontsize=6.5
    )
    ax.set_xlabel("Annual system cost  (relative to baseline)")
    ax.set_axisbelow(True)
    ax.grid(which="major", axis="x", color="#DDDDDD", lw=0.4, zorder=0)
    ax.xaxis.set_minor_locator(mticker.AutoMinorLocator(2))
    ax.grid(which="minor", axis="x", color="#EEEEEE", lw=0.25, zorder=0)
    xmin  = max(0.0, float(np.nanmin(lo_n.values)) - 0.02)
    xlim_right = 1.5
    ax.set_xlim(left=xmin, right=xlim_right)

    # Annotate points clipped by the x-axis limit
    for case in CASES:
        if case not in mn_n.columns:
            continue
        for region, v in zip(order, mn_n[case].values):
            if np.isfinite(v) and v > xlim_right:
                yi = order.index(region)
                ax.scatter(xlim_right, yi, marker=">", color=C[case],
                           s=22, zorder=6, clip_on=False)
                ax.text(xlim_right - 0.01, yi + 0.42,
                        f"{LBL[case]}: {v:.2f}×",
                        ha="right", va="bottom", fontsize=6.0,
                        color=C[case])

    ax.legend(handles=_legend_handles(), loc="lower right",
              frameon=False, ncol=1)

    fig.tight_layout()
    _save(fig, out / "fig1_overview")


# ── Figure 2: Optimisation pathway ───────────────────────────────────────────
def plot_pathway(df: pd.DataFrame, out: Path, highlight: list[str]) -> None:
    mn   = _wide(df, "cost_mn")
    bl   = mn["Baseline"]
    norm = mn.div(bl, axis=0).reindex(columns=CASES)

    x    = np.arange(len(CASES))
    fig, ax = plt.subplots(figsize=(7.0, 5.25))

    # ── all-region background lines ──
    for region in norm.index:
        row = norm.loc[region, CASES].values.astype(float)
        ax.plot(x, row, color="#CCCCCC", lw=0.55, alpha=0.75, zorder=1)

    # ── highlighted regions ──
    hl_palette = ["#D55E00", "#CC79A7", "#56B4E9"]
    for k, region in enumerate(highlight):
        if region not in norm.index:
            continue
        row = norm.loc[region, CASES].values.astype(float)
        col = hl_palette[k % len(hl_palette)]
        ax.plot(x, row, color=col, lw=1.8, zorder=4,
                label=region.replace("-", "‑"))
        ax.scatter(x, row, color=col, s=40, zorder=5,
                   edgecolors="white", linewidths=0.6)

    # ── global mean ──
    mean = norm.mean(axis=0)[CASES].values
    ax.plot(x, mean, color="#222222", lw=2.2, zorder=3,
            marker="o", ms=5.5, label="All-region mean")
    ax.scatter(x, mean, color="#222222", s=45, zorder=5,
               edgecolors="white", linewidths=0.6)

    # ── baseline reference ──
    ax.axhline(1.0, color="#AAAAAA", lw=0.8, ls="--")

    ylim_bot, ylim_top = 0.85, 1.10
    ax.set_ylim(ylim_bot, ylim_top)

    # Annotate points clipped above ylim
    for region in norm.index:
        for j, case in enumerate(CASES):
            v = float(norm.loc[region, case]) if case in norm.columns else np.nan
            if np.isfinite(v) and v > ylim_top:
                ax.scatter(x[j], ylim_top, marker="^", color=C[case],
                           s=22, zorder=6, clip_on=False)
                ax.text(x[j] + 0.08, ylim_top - 0.002,
                        f"{region.replace('-', '‑')}\n{v:.0%}",
                        ha="left", va="top", fontsize=6.0,
                        color=C[case])

    ax.set_xticks(x)
    ax.set_xticklabels([LBL[c] for c in CASES], rotation=15, ha="right")
    ax.yaxis.set_major_formatter(
        mticker.FuncFormatter(lambda v, _: f"{v:.0%}")
    )
    ax.set_ylabel("Annual system cost  (% of baseline)")
    ax.legend(loc="lower left", frameon=False)

    fig.tight_layout()
    _save(fig, out / "fig2_pathway")


# ── Figure 3: Exemplary region — cost bars + factor heatmap ──────────────────
def plot_exemplar(df: pd.DataFrame, region: str, out: Path,
                  countrystats: Path | None = None) -> None:
    sub = df[df["region"] == region].set_index("case")
    if sub.empty:
        print(f"  [skip] Region '{region}' not found.")
        return

    present  = [c for c in CASES if c in sub.index]
    avail_f  = [k for k in FACTOR_KEYS if k in sub.columns]
    cases_b  = [c for c in CASES if c in sub.index]
    ref_gw   = _load_ref_gw(region, countrystats) if countrystats and countrystats.exists() else {}
    n_cases  = len(present)
    n_params = len(avail_f)

    fig = plt.figure(
        figsize=(7.0, max(4.8, n_cases * 0.90 + 1.0)),
        layout="constrained",
    )
    gs  = fig.add_gridspec(1, 2, width_ratios=[1, 2.8])
    ax1  = fig.add_subplot(gs[0, 0])
    ax2  = fig.add_subplot(gs[0, 1], sharey=ax1)
    fig.get_layout_engine().set(wspace=0.05)

    # ── Panel A: horizontal cost bars ─────────────────────────────────────────
    bl_cost = float(sub.loc["Baseline", "cost_mn"]) if "Baseline" in sub.index else None

    for i, case in enumerate(present):
        mn_val = float(sub.loc[case, "cost_mn"])
        lo_val = float(sub.loc[case, "cost_lo"])
        hi_val = float(sub.loc[case, "cost_hi"])

        ax1.barh(i, mn_val, color=C[case], alpha=0.85, height=0.62, zorder=3)
        ax1.errorbar(mn_val, i,
                     xerr=[[mn_val - lo_val], [hi_val - mn_val]],
                     fmt="none", color="#333333",
                     capsize=3, lw=0.9, zorder=4)

        if bl_cost is not None and case != "Baseline":
            pct = (mn_val / bl_cost - 1.0) * 100.0
            label = f"${mn_val:.1f} B\n({pct:+.1f}%)"
        else:
            label = f"${mn_val:.1f} B"
        ax1.text(mn_val * 0.50, i, label,
                 ha="center", va="center", fontsize=6.5, color="white")

    if bl_cost is not None:
        ax1.axvline(bl_cost, color=C["Baseline"], lw=0.8, ls="--", alpha=0.6)

    # Fine separator lines at case boundaries, connected across both panels
    for y_sep in [i + 0.5 for i in range(n_cases - 1)]:
        ax1.axhline(y_sep, color="#BBBBBB", lw=0.5, zorder=5)
        ax2.axhline(y_sep, color="#BBBBBB", lw=0.5, zorder=5)
        cp = ConnectionPatch(
            xyA=(1.0, y_sep), xyB=(0.0, y_sep),
            coordsA=ax1.get_yaxis_transform(),
            coordsB=ax2.get_yaxis_transform(),
            color="#BBBBBB", lw=0.5, zorder=5, clip_on=False,
        )
        fig.add_artist(cp)

    ax1.set_yticks(range(n_cases))
    ax1.set_yticklabels([c for c in present], rotation=90, ha="center")
    ax1.tick_params(axis="y", pad=22)
    ax1.set_xlabel("Annual system cost ($B/yr)")
    ax1.set_ylim(-0.5, n_cases - 0.5)
    ax1.set_title("A)", pad=5, loc="left", fontweight="bold")

    # ── Panel B: factor-ratio heatmap — cases on y, params on x ──────────────
    bl_vals = sub.loc["Baseline", avail_f].values.astype(float) if "Baseline" in sub.index else None

    mat = np.full((len(cases_b), n_params), np.nan)
    for i, case in enumerate(cases_b):
        vals = sub.loc[case, avail_f].values.astype(float)
        if bl_vals is not None:
            with np.errstate(divide="ignore", invalid="ignore"):
                log2r = np.where(
                    bl_vals > 0,
                    np.log2(np.clip(vals / bl_vals, 0.125, 8.0)),
                    np.where(vals == 0, 0.0, np.nan),
                )
        else:
            log2r = np.zeros(n_params)
        mat[i, :] = log2r

    # y-positions for cases_b align with their bars in panel A
    case_y  = {c: present.index(c) for c in cases_b if c in present}
    y_edges = np.array([case_y[c] - 0.5 for c in cases_b] + [case_y[cases_b[-1]] + 0.5])
    x_edges = np.arange(n_params + 1) - 0.5

    # Norm using actual data min/max so full color range is used
    mat_flat = mat[~np.isnan(mat)]
    v_lo = float(mat_flat.min()) if len(mat_flat) else -0.5
    v_hi = float(mat_flat.max()) if len(mat_flat) else 0.5
    if v_lo >= 0: v_lo = -0.1
    if v_hi <= 0: v_hi = 0.1
    max_abs = max(abs(v_lo), abs(v_hi))
    vnorm = TwoSlopeNorm(vmin=v_lo, vcenter=0.0, vmax=v_hi)

    pcm = ax2.pcolormesh(x_edges, y_edges, mat, cmap="RdBu_r", norm=vnorm)

    # Fine white grid lines to separate cells
    for xi in x_edges:
        ax2.axvline(xi, color="white", lw=0.4, zorder=6)
    for yi in y_edges:
        ax2.axhline(yi, color="white", lw=0.4, zorder=6)

    for i, case in enumerate(cases_b):
        cy = case_y[case]
        for j in range(n_params):
            v = mat[i, j]
            if not np.isnan(v):
                if case == "Baseline":
                    v_abs = bl_vals[j] if bl_vals is not None else 0.0
                    cat   = FACTOR_CATS.get(avail_f[j], "capacity")
                    if cat == "tw":
                        label = f"{v_abs * 1000:.0f}\nGW"
                    elif cat == "hours":
                        label = f"{v_abs:.0f} h"
                    elif cat == "days":
                        label = f"{v_abs:.0f} d"
                    elif cat == "cop":
                        label = f"{v_abs:.1f}"
                    else:  # capacity: show absolute GW if reference available
                        key = avail_f[j]
                        rg  = ref_gw.get(key)
                        if rg is not None:
                            gw = v_abs * rg
                            label = f"{gw:.0f}\nGW"
                        else:
                            label = f"×{v_abs:.2f}"
                    ax2.text(j, cy, label, ha="center", va="center",
                             fontsize=5.0, color="black")
                else:
                    txt_col = "white" if abs(v) > max_abs * 0.55 else "black"
                    ax2.text(j, cy, f"\xd7{2**v:.2f}",
                             ha="center", va="center",
                             fontsize=5.0, color=txt_col)

    ax2.set_xticks(range(n_params))
    ax2.set_xticklabels(
        [FACTOR_LBL.get(k, k) for k in avail_f],
        rotation=90, ha="center", fontsize=6.5,
    )
    ax2.tick_params(axis="x", labelrotation=90)
    ax2.tick_params(axis="y", labelleft=False, left=False)
    ax2.spines["left"].set_visible(False)
    ax2.set_title("B)", pad=5, loc="left", fontweight="bold")

    # Vertical colorbar to the right of panel B
    ticks = [v_lo, v_lo * 0.5, 0.0, v_hi * 0.5, v_hi]
    cb = fig.colorbar(pcm, ax=ax2, location="right", shrink=0.8, aspect=25, pad=0.02)
    cb.set_ticks(ticks)
    cb.ax.set_yticklabels([f"\xd7{2**t:.2f}" for t in ticks], fontsize=6)
    cb.set_label("Factor relative to baseline", fontsize=7, rotation=270, labelpad=10)

    slug = region.lower().replace("-", "_")
    _save(fig, out / f"fig3_{slug}")


# ── Figure 4: Energy-mix stacked bars — all regions, all cases ───────────────
def plot_energy_mix(df: pd.DataFrame, out: Path) -> None:
    CASE_SHORT = {
        "Baseline": "BL",
        "LP":       "LP",
        "GA (bl)":  "GA(BL)",
        "GA (LP)":  "GA(LP)",
    }

    cases_avail = [c for c in CASES if c in df["case"].values]
    nc = len(cases_avail)

    # Alphabetical, A at top — same ordering as fig 1
    all_regions  = sorted(df["region"].unique().tolist(), reverse=True)
    n            = len(all_regions)
    topmost      = all_regions[-1]   # first alphabetically → highest y → top of figure

    avail = [(lbl, col, clr) for lbl, col, clr in GEN_SOURCES if col in df.columns]

    # height: 0.067" per bar (2/3 of full size); 4 cases × 29 regions ≈ 8"
    fig, ax = plt.subplots(figsize=(3.5, max(2.625, n * nc * 0.067 + 0.5)))

    for i, region in enumerate(all_regions):
        for j, case in enumerate(cases_avail):
            sub = df[(df["region"] == region) & (df["case"] == case)]
            if sub.empty:
                continue
            row   = sub.iloc[0]
            total = sum(float(row.get(col, 0) or 0) for _, col, _ in avail)
            if total <= 0:
                continue

            y_pos = i * nc + j
            left  = 0.0
            for lbl, col, clr in avail:
                val = float(row.get(col, 0) or 0)
                pct = val / total * 100.0
                if pct < 0.05:
                    left += pct
                    continue
                ax.barh(y_pos, pct, left=left, height=1.0, color=clr)
                left += pct

            # Annotate case name once — only inside the topmost region's bars
            if region == topmost:
                ax.text(1.5, y_pos, CASE_SHORT.get(case, case),
                        ha="left", va="center", fontsize=6,
                        color="white", fontweight="bold", zorder=10)

    # Separators: thin white between cases, grey between regions
    for i in range(n):
        base = i * nc
        for j in range(1, nc):
            ax.axhline(base + j - 0.5, color="white", lw=0.5, zorder=5)
        if i > 0:
            ax.axhline(base - 0.5, color="#888888", lw=0.8, zorder=6)

    # Y-axis: region names centred on each 4-bar group
    ticks = [i * nc + (nc - 1) / 2.0 for i in range(n)]
    ax.set_yticks(ticks)
    ax.set_yticklabels([r.replace("-", "‑") for r in all_regions], fontsize=6.5)
    ax.tick_params(axis="y", length=0)
    ax.spines["left"].set_visible(False)

    ax.set_xlim(0, 100)
    ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:.0f}%"))
    ax.set_xlabel("Share of total energy supply")
    ax.set_ylim(-0.5, n * nc - 0.5)

    legend_handles = [
        mpl.patches.Patch(color=clr, label=lbl)
        for lbl, col, clr in avail
        if col in df.columns and float(df[col].max()) > 0
    ]
    ax.legend(handles=legend_handles, frameon=False, ncol=4, fontsize=6,
              bbox_to_anchor=(0.5, -0.04), loc="upper center")

    fig.tight_layout()
    _save(fig, out / "fig4_energy_mix")


# ── Figure 5: Cost waterfall — one panel per comparison case ─────────────────
def _cost_groups_bil(row: pd.Series) -> dict | None:
    """c/kWh cost columns × end-energy TWh → grouped B$/yr dict."""
    energy = float(row.get(_END_ENERGY, 0) or 0)
    if energy <= 0:
        return None
    return {
        grp: sum(float(row.get(_COST_PREFIX + lbl, 0) or 0) for lbl in lbls) * energy / 100
        for grp, lbls in _COST_GROUPS.items()
    }


def plot_waterfall(df: pd.DataFrame, region: str, out: Path,
                   ylim: tuple[float, float] | None = None) -> None:
    sub = df[df["region"] == region].set_index("case")
    if sub.empty or "Baseline" not in sub.index:
        print(f"  [skip] No Baseline data for waterfall '{region}'.")
        return

    bl_grp = _cost_groups_bil(sub.loc["Baseline"])
    if bl_grp is None:
        print(f"  [skip] No cost-group data for '{region}'.")
        return

    bl_total = sum(bl_grp.values())
    groups   = list(_COST_GROUPS.keys())

    comparison = [
        (case, _cost_groups_bil(sub.loc[case]), C[case])
        for case in ["LP", "GA (bl)", "GA (LP)"]
        if case in sub.index
    ]
    comparison = [(n, g, c) for n, g, c in comparison if g is not None]
    if not comparison:
        print(f"  [skip] No comparison cases for waterfall '{region}'.")
        return

    ncols     = len(comparison)
    fig, axes = plt.subplots(1, ncols, figsize=(7.0, 5.25),
                             sharey=True, layout="constrained",
                             gridspec_kw={"wspace": 0.08})
    if ncols == 1:
        axes = [axes]

    def _panel(ax, case_grp, case_color, case_name, panel_label, first):
        case_total  = sum(case_grp.values())
        net_delta   = case_total - bl_total
        deltas      = {g: case_grp[g] - bl_grp[g] for g in groups}
        sorted_grps = sorted(groups, key=lambda g: deltas[g])

        labels = sorted_grps + ["Net"]
        n_bars = len(labels)
        x      = np.arange(n_bars)
        bar_w  = min(0.55, 5.0 / n_bars)

        # Waterfall from 0: each bar floats at the running cumulative delta
        running  = 0.0
        bottoms  = []
        heights  = []
        bar_clrs = []
        for g in sorted_grps:
            d = deltas[g]
            bottoms.append(running + d if d < 0 else running)
            heights.append(abs(d))
            bar_clrs.append(_CLR_DOWN if d < 0 else _CLR_UP)
            running += d
        # Net bar: solid, from 0 to net_delta
        bottoms.append(min(0.0, net_delta))
        heights.append(abs(net_delta))
        bar_clrs.append(case_color)

        ax.bar(x, heights, bar_w, bottom=bottoms, color=bar_clrs,
               edgecolor="#444444", lw=0.4, alpha=0.88)
        ax.axhline(0, color="#888888", lw=0.8, zorder=0)
        ax.set_xlim(-0.6, n_bars - 0.4)

        # Connectors at the running total between consecutive group bars
        running = 0.0
        for i, g in enumerate(sorted_grps[:-1]):
            running += deltas[g]
            ax.plot([x[i] + bar_w / 2, x[i + 1] - bar_w / 2],
                    [running, running],
                    color="#666666", lw=0.6, ls="--", zorder=5)
        # No connector to the Net bar (it stands alone from 0)

        # Group delta annotations — rotated, small font, at the free end of each bar
        running = 0.0
        for xi, g in enumerate(sorted_grps):
            d   = deltas[g]
            running += d
            sgn = "−" if d < 0 else "+"
            off = (0, -3) if d < 0 else (0, 3)
            va  = "top"   if d < 0 else "bottom"
            ax.annotate(f"{sgn}${abs(d):.1f}B", xy=(xi, running), xytext=off,
                        textcoords="offset points",
                        ha="center", va=va, fontsize=6,
                        color=_CLR_DOWN if d < 0 else _CLR_UP, rotation=90)

        # Net bar annotation — horizontal, bold, absolute + relative
        pct  = net_delta / bl_total * 100
        sign = "−" if net_delta < 0 else "+"
        ann  = f"{sign}${abs(net_delta):.1f} B\n({pct:+.1f}%)"
        off  = (0, -4) if net_delta < 0 else (0, 4)
        va   = "top"   if net_delta < 0 else "bottom"
        ax.annotate(ann, xy=(n_bars - 1, net_delta), xytext=off,
                    textcoords="offset points",
                    ha="center", va=va, fontsize=7, fontweight="bold",
                    color=case_color)

        ax.set_xticks(x)
        ax.set_xticklabels(labels, rotation=55, ha="right")
        ax.set_title(f"{panel_label})", loc="left", fontweight="bold")
        if first:
            ax.set_ylabel("Cost change vs. baseline ($B/yr)")

        return min(b + h for b, h in zip(bottoms, heights)), \
               max(b + h for b, h in zip(bottoms, heights))

    results = [
        _panel(axes[pi], cgrp, ccolor, cname, "ABC"[pi], pi == 0)
        for pi, (cname, cgrp, ccolor) in enumerate(comparison)
    ]
    if ylim is not None:
        y_lo, y_hi = ylim
    else:
        g_min  = min(lo for lo, hi in results)
        g_max  = max(hi for lo, hi in results)
        margin = max(abs(g_min), abs(g_max)) * 0.45
        y_lo   = g_min - margin
        y_hi   = g_max + margin
    axes[0].set_ylim(y_lo, y_hi)

    # Single shared legend outside the panels, below x-axis labels
    legend_handles = [
        mpl.patches.Patch(facecolor=C["Baseline"], label="Baseline"),
        mpl.patches.Patch(facecolor=_CLR_DOWN,     label="Cost reduction"),
        mpl.patches.Patch(facecolor=_CLR_UP,        label="Cost increase"),
    ] + [
        mpl.patches.Patch(facecolor=ccolor, label=f"{cname} total")
        for cname, _, ccolor in comparison
    ]
    axes[0].legend(handles=legend_handles, frameon=False, fontsize=6,
                   ncol=2, loc="lower left")

    slug = region.lower().replace("-", "_")
    _save(fig, out / f"fig5_waterfall_{slug}")


# ── CLI ───────────────────────────────────────────────────────────────────────
def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--input", type=Path,
        default=Path("data/results_verification/results_export.csv"),
        help="Path to results_export.csv (default: data/results_verification/results_export.csv)",
    )
    p.add_argument(
        "--output", type=Path,
        default=Path("figures/method_comparison"),
        help="Output directory (default: figures/method_comparison/)",
    )
    p.add_argument(
        "--exemplar", default="UNITED-STATES",
        help="Region for the detailed fig3 (default: UNITED-STATES)",
    )
    p.add_argument(
        "--highlight", nargs="*", default=None,
        help="Regions to highlight in the pathway fig2 (default: exemplar only)",
    )
    p.add_argument(
        "--countrystats", type=Path,
        default=Path("data/raw/countrystats.dat"),
        help="Path to countrystats.dat (default: data/raw/countrystats.dat)",
    )
    p.add_argument(
        "--waterfall-ylim", nargs=2, type=float, default=None,
        metavar=("LO", "HI"),
        help="Y-axis limits for the waterfall plot, e.g. --waterfall-ylim 600 850 "
             "(default: auto from 0)",
    )
    return p.parse_args()


def main() -> None:
    args   = parse_args()
    df     = load(args.input)
    hl     = args.highlight if args.highlight is not None else [args.exemplar]

    print(
        f"Loaded {len(df)} rows · {df['region'].nunique()} regions · "
        f"{df['case'].nunique()} cases"
    )

    plot_overview(df, args.output)
    plot_pathway(df, args.output, highlight=hl)
    plot_exemplar(df, args.exemplar, args.output, countrystats=args.countrystats)
    plot_energy_mix(df, args.output)
    wf_ylim = tuple(args.waterfall_ylim) if args.waterfall_ylim else None
    plot_waterfall(df, args.exemplar, args.output, ylim=wf_ylim)
    print("Done.")


if __name__ == "__main__":
    main()

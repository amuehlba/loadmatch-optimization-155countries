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

import json
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
    "Baseline":      "#888888",
    "LP":            "#0072B2",
    "First feasible":"#D55E00",
    "GA (bl)":       "#E69F00",
    "GA (LP)":       "#009E73",
}
MK   = {"Baseline": "D", "LP": "s", "First feasible": "*", "GA (bl)": "^", "GA (LP)": "o"}
MKSZ = {"Baseline": 18,  "LP": 18,  "First feasible": 22,  "GA (bl)": 20,  "GA (LP)": 18}
CASES = ["Baseline", "LP", "GA (bl)", "GA (LP)"]
OVERVIEW_CASES = ["Baseline", "LP", "First feasible", "GA (bl)", "GA (LP)"]
LBL   = {
    "Baseline":      "Baseline",
    "LP":            "LP warm-start",
    "First feasible":"First LOADMATCH-feasible",
    "GA (bl)":       "GA (baseline start)",
    "GA (LP)":       "GA (LP warm-start)",
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
def load(path: Path, feasible_only: bool = True) -> pd.DataFrame:
    df = pd.read_csv(path).rename(columns={
        "Identification/Region":               "region",
        "Identification/Case":                 "case",
        "Identification/Feasible":             "feasible",
        "Annual cost ($B/yr)/Cost LO ($B/yr)": "cost_lo",
        "Annual cost ($B/yr)/Cost MN ($B/yr)": "cost_mn",
        "Annual cost ($B/yr)/Cost HI ($B/yr)": "cost_hi",
        # Capital cost from the LP solver (T$/yr); only populated for LP rows.
        # For infeasible LP regions this is the only cost available.
        "Annual cost ($B/yr)/Capital ($T/yr, MN)": "capital_mn_tusd",
    })
    df = df.rename(columns={
        c: c.replace("Optimised factors/", "") for c in df.columns
    })
    df["feasible"] = df["feasible"].astype(str).str.strip().str.lower() == "true"
    if feasible_only:
        return df[df["feasible"]].copy()
    return df.copy()


def _wide(df: pd.DataFrame, metric: str, cases=None) -> pd.DataFrame:
    """Pivot to region × case table for a single metric."""
    if cases is None:
        cases = CASES
    return df.pivot_table(
        index="region", columns="case", values=metric, aggfunc="first"
    ).reindex(columns=cases)


def _save(fig: plt.Figure, stem: Path, dpi: int = 300) -> None:
    stem.parent.mkdir(parents=True, exist_ok=True)
    for ext in ("pdf", "png"):
        p = stem.with_suffix(f".{ext}")
        fig.savefig(p, dpi=dpi, bbox_inches="tight")
        print(f"  Saved {p}")
    plt.close(fig)


def _legend_handles(cases=None) -> list:
    if cases is None:
        cases = CASES
    return [
        mpl.lines.Line2D([], [], color=C[c], marker=MK[c], linestyle="",
                         markersize=5, label=LBL[c])
        for c in cases
    ]


# ── Figure 1: Cleveland dot plot — all regions ────────────────────────────────
def plot_overview(df: pd.DataFrame, df_all: pd.DataFrame, out: Path) -> None:
    # Pivot feasible-only for non-LP cases + error bars (clean data)
    mn = _wide(df, "cost_mn", cases=OVERVIEW_CASES)
    lo = _wide(df, "cost_lo", cases=OVERVIEW_CASES)
    hi = _wide(df, "cost_hi", cases=OVERVIEW_CASES)

    bl = mn["Baseline"]
    mn_n = mn.div(bl, axis=0)
    lo_n = lo.div(bl, axis=0)
    hi_n = hi.div(bl, axis=0)

    # LP costs read directly from df_all — captures infeasible rows the pivot skips.
    # For infeasible LP regions, cost_mn is null; fall back to the LP solver's own
    # capital-cost objective (capital_mn_tusd × 1000 converts T$/yr → B$/yr).
    bl_cost_sr = df_all[df_all["case"] == "Baseline"].set_index("region")["cost_mn"]
    lp_df      = df_all[df_all["case"] == "LP"].set_index("region")
    lp_cost_sr = lp_df["cost_mn"].fillna(lp_df["capital_mn_tusd"] * 1000)
    lp_infeas  = set(
        df_all.loc[(df_all["case"] == "LP") & (~df_all["feasible"]), "region"]
    )

    order = sorted(bl.index.tolist(), reverse=True)
    mn_n, lo_n, hi_n = mn_n.loc[order], lo_n.loc[order], hi_n.loc[order]
    n = len(order)
    y = np.arange(n)

    # Normalised LP cost per region (direct lookup, not via pivot)
    lp_norm = np.array([
        lp_cost_sr.get(r, np.nan) / bl_cost_sr.get(r, np.nan) for r in order
    ])

    fig, ax = plt.subplots(figsize=(3.5, max(2.625, n * 0.27)))

    for case in OVERVIEW_CASES:
        if case not in mn_n.columns and case != "LP":
            continue

        # Choose cost values: direct lookup for LP, pivot for everything else
        mn_v = lp_norm if case == "LP" else mn_n[case].values

        lo_v = lo_n[case].values if case in lo_n.columns else np.full(n, np.nan)
        hi_v = hi_n[case].values if case in hi_n.columns else np.full(n, np.nan)
        for i, (lv, hv) in enumerate(zip(lo_v, hi_v)):
            if np.isfinite(lv) and np.isfinite(hv):
                ax.plot([lv, hv], [y[i], y[i]],
                        color=C[case], lw=0.7, alpha=0.35, zorder=2)

        if case == "LP":
            feas_mask   = np.array([r not in lp_infeas for r in order])
            ax.scatter(np.where( feas_mask, mn_v, np.nan), y,
                       color=C[case], marker=MK[case], s=MKSZ[case],
                       zorder=4, linewidths=0)
            ax.scatter(np.where(~feas_mask, mn_v, np.nan), y,
                       facecolors="none", edgecolors=C[case],
                       marker=MK[case], s=MKSZ[case], zorder=4, linewidths=0.9)
        else:
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
    all_vals = np.concatenate([lo_n.values.ravel(), lp_norm])
    xmin = max(0.0, float(np.nanmin(all_vals)) - 0.02)
    xlim_right = 1.5
    ax.set_xlim(left=xmin, right=xlim_right)

    # Annotate points clipped by the x-axis limit
    for case in OVERVIEW_CASES:
        case_vals = lp_norm if case == "LP" else (
            mn_n[case].values if case in mn_n.columns else np.full(n, np.nan)
        )
        for i, (region, v) in enumerate(zip(order, case_vals)):
            if np.isfinite(v) and v > xlim_right:
                ax.scatter(xlim_right, i, marker=">", color=C[case],
                           s=22, zorder=6, clip_on=False)
                tag = " (CAPEX only, infeas.)" if (case == "LP" and region in lp_infeas) else ""
                ax.text(xlim_right - 0.01, i + 0.42,
                        f"{LBL[case]}{tag}: {v:.2f}×",
                        ha="right", va="bottom", fontsize=6.0, color=C[case])

    legend_handles = _legend_handles(cases=OVERVIEW_CASES) + [
        mpl.lines.Line2D([], [], color=C["LP"], marker=MK["LP"], linestyle="",
                         markersize=5, markerfacecolor="none",
                         markeredgecolor=C["LP"], markeredgewidth=0.9,
                         label="LP (LOADMATCH-infeasible; CAPEX only)"),
    ]
    ax.legend(handles=legend_handles,
              loc="upper center", bbox_to_anchor=(0.5, -0.06),
              frameon=False, ncol=2, fontsize=7,
              handlelength=1.0, handletextpad=0.4, columnspacing=1.2)

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


# ── LP solution helpers ───────────────────────────────────────────────────────

def _load_lp_solution(lp_dir: Path, region: str) -> dict | None:
    """Load lp_solution.json; return None if absent or LP failed."""
    p = lp_dir / region / "lp_solution.json"
    if not p.exists():
        return None
    with open(p) as f:
        sol = json.load(f)
    return None if sol.get("lp_failed") else sol


def _load_lp_dispatch(lp_dir: Path, region: str) -> pd.DataFrame | None:
    """Load lp_dispatch_<month>.csv; return None if absent."""
    sol = _load_lp_solution(lp_dir, region)
    if sol is None:
        return None
    month = sol["exemplary_month"]["month_name"].lower()
    p = lp_dir / region / f"lp_dispatch_{month}.csv"
    return pd.read_csv(p) if p.exists() else None


# ── Figure 6: LP dispatch — exemplary month, electric sector ─────────────────

_ELEC_SUPPLY = [   # (display label, CSV columns, fill color)
    ("Fixed baseload", ["fixed_elec_mw"],                                    "#009E73"),
    ("Wind",           ["gen_wind_on_mw", "gen_wind_off_mw"],                "#56B4E9"),
    ("Solar PV",       ["gen_pv_res_mw", "gen_pv_com_mw", "gen_pv_util_mw"],"#F0E442"),
    ("CSP",            ["gen_csp_mw"],                                       "#E69F00"),
    ("Battery",        ["dis_bat_mw"],                                       "#CC79A7"),
    ("PHS",            ["dis_phs_mw"],                                       "#0072B2"),
    ("H₂ fuel cell",  ["fuelcell_mw"],                                  "#D55E00"),
]
_ELEC_CHG = [   # shown below zero (storage charging / auxiliary electric loads)
    ("Battery chg.",  ["chg_bat_mw"],              "#CC79A7"),
    ("PHS chg.",      ["chg_phs_mw"],               "#0072B2"),
    ("Electrolysis",  ["electrolyser_mw"],          "#D55E00"),
    ("Heat pump+AC",  ["heat_pump_mw", "ac_mw"],    "#777777"),
]


def plot_lp_dispatch(regions: list[str], lp_dir: Path, out: Path) -> None:
    """Stacked-area electric dispatch for the LP exemplary month — one figure per region."""
    for region in regions:
        df  = _load_lp_dispatch(lp_dir, region)
        sol = _load_lp_solution(lp_dir, region)
        if df is None or sol is None:
            print(f"  [skip fig6] No LP dispatch data for {region}.")
            continue

        fig, ax    = plt.subplots(figsize=(7.0, 3.2), layout="constrained")
        GW         = 1e-3
        hrs        = np.arange(len(df))
        month_name = sol["exemplary_month"]["month_name"]

        def _col(*cols):
            arrs = [np.clip(df[c].values, 0, None) for c in cols if c in df.columns]
            return sum(arrs) * GW if arrs else np.zeros(len(df))

        sup_v, sup_l, sup_c = [], [], []
        for label, cols, color in _ELEC_SUPPLY:
            v = _col(*cols)
            if v.max() > 0.5:
                sup_v.append(v); sup_l.append(label); sup_c.append(color)

        chg_v, chg_l, chg_c = [], [], []
        for label, cols, color in _ELEC_CHG:
            v = _col(*cols)
            if v.max() > 0.5:
                chg_v.append(-v); chg_l.append(label); chg_c.append(color)

        if sup_v:
            ax.stackplot(hrs, sup_v, labels=sup_l, colors=sup_c, alpha=0.88)
        if chg_v:
            ax.stackplot(hrs, chg_v, labels=chg_l, colors=chg_c, alpha=0.55)

        ax.plot(hrs, df["elec_load_mw"].values * GW,
                color="#111111", lw=1.3, zorder=9, label="Electric load")
        ax.axhline(0, color="#BBBBBB", lw=0.5, zorder=0)

        tick_h = np.arange(0, len(hrs), 168)
        ax.set_xticks(tick_h)
        ax.set_xticklabels([f"{month_name[:3]} {1 + int(t) // 24}" for t in tick_h])
        ax.set_xlim(0, len(hrs) - 1)
        ax.set_ylabel("Power (GW)")
        ax.set_title(f"{region}  —  LP electric dispatch, {month_name}",
                     loc="left", fontweight="bold")

        handles, labels = ax.get_legend_handles_labels()
        ax.legend(handles, labels, frameon=False, ncol=4, fontsize=6,
                  loc="upper right")

        slug = region.lower().replace("-", "_")
        _save(fig, out / f"fig6_lp_dispatch_{slug}")


# ── Figure 7: LP installed capacities and storage ────────────────────────────

_CAP_ITEMS = [   # (JSON key in capacities_gw, display label, bar color)
    ("onshore_wind",  "Onshore wind",    "#56B4E9"),
    ("offshore_wind", "Offshore wind",   "#0072B2"),
    ("res_pv",        "Residential PV", "#F0E442"),
    ("com_pv",        "Commercial PV",  "#F5D060"),
    ("utility_pv",    "Utility PV",      "#FAF0A0"),
    ("csp",           "CSP",            "#E69F00"),
    ("solar_thermal", "Solar thermal",  "#AA55AA"),
    ("battery_power", "Battery (power)","#CC79A7"),
    ("phs_power",     "PHS (power)",    "#4488BB"),
    ("h2_fc_power",   "H₂ fuel cell", "#D55E00"),
    ("h2_chg_power",  "Electrolyser",   "#882255"),
]
_STOR_ITEMS = [   # (JSON key in storage_gwh, display label, bar color)
    ("utes",     "UTES (seasonal)", "#009E73"),
    ("h2",       "H₂ storage",  "#D55E00"),
    ("hw_stes",  "Hot-water TES",   "#E69F00"),
    ("cold_tes", "Cold TES",        "#56B4E9"),
    ("battery",  "Battery",         "#CC79A7"),
    ("phs",      "PHS",             "#4488BB"),
    ("heat_bat", "Heat battery",    "#882255"),
]


def plot_lp_capacity(regions: list[str], lp_dir: Path, out: Path) -> None:
    """Horizontal bar charts of LP installed capacity (GW) and storage (GWh) — one figure per region."""
    def _bars(ax, items, data, x_label, title):
        vals, labels, colors = [], [], []
        for key, label, color in items:
            v = data.get(key, 0.0)
            if v > 0.5:
                vals.append(v); labels.append(label); colors.append(color)
        if not vals:
            ax.set_visible(False)
            return
        y = np.arange(len(vals))
        ax.barh(y, vals, color=colors, height=0.65, alpha=0.88,
                edgecolor="#444444", linewidth=0.3)
        ax.set_yticks(y)
        ax.set_yticklabels(labels, fontsize=7)
        ax.set_xlabel(x_label)
        ax.set_title(title, loc="left", fontsize=7.5, fontweight="bold")
        xmax = max(vals)
        for yi, v in enumerate(vals):
            ax.text(v + xmax * 0.02, yi, f"{v:,.0f}", va="center", fontsize=6)
        ax.set_xlim(0, xmax * 1.20)
        ax.grid(axis="x", color="#EEEEEE", lw=0.4, zorder=0)
        ax.set_axisbelow(True)

    for region in regions:
        sol = _load_lp_solution(lp_dir, region)
        if sol is None:
            print(f"  [skip fig7] No LP solution for {region}.")
            continue
        fig, (ax_c, ax_s) = plt.subplots(1, 2, figsize=(7.0, 3.0), layout="constrained")
        _bars(ax_c, _CAP_ITEMS,  sol["capacities_gw"],
              "Installed capacity (GW)", f"{region}  —  generation & power")
        _bars(ax_s, _STOR_ITEMS, sol["storage_gwh"],
              "Storage energy (GWh)",   f"{region}  —  storage energy")
        slug = region.lower().replace("-", "_")
        _save(fig, out / f"fig7_lp_capacity_{slug}")


# ── Figure 8: LP → GA(LP) three-way comparison — one figure per region ───────

# Factor keys → (display label, unit conversion to GW)
_CMP_GEN_KEYS = [
    ("FACONWIN",   "Onshore wind"),
    ("FACOFFWIN",  "Offshore wind"),
    ("FACRESPV",   "Residential PV"),
    ("FACCOMPV",   "Commercial PV"),
    ("FACUTILPV",  "Utility PV"),
    ("CSPTURBFAC", "CSP"),
    ("FACSHT",     "Solar thermal"),
]
_CMP_PWR_KEYS = [   # stored in TW; ×1000 → GW
    ("BATDISCH",  "Battery (power)"),
    ("FCDISCH",   "H₂ fuel cell"),
    ("FCCHARG",   "Electrolyser"),
]


def plot_lp_comparison(
    df: pd.DataFrame,
    region: str,
    lp_dir: Path,
    out: Path,
    countrystats: Path | None = None,
) -> None:
    """Per-region three-panel figure: cost bars + LP vs GA(LP) capacity comparison.

    Uses results_export.csv for Fortran costs and factor values (Baseline / LP /
    GA(LP)).  The LP here is whatever factors are recorded in results_export; it
    may differ from a fresh lp_solution.json if run_ga_from_lp has not been
    re-executed after the latest LP solve.
    """
    sub    = df[df["region"] == region].set_index("case")
    ref_gw = _load_ref_gw(region, countrystats) if countrystats and countrystats.exists() else {}

    cases_present = [c for c in ["Baseline", "LP", "GA (LP)"] if c in sub.index]
    if not cases_present:
        print(f"  [skip fig8] No results_export data for '{region}'.")
        return

    fig = plt.figure(figsize=(7.0, 4.4), layout="constrained")
    gs  = fig.add_gridspec(1, 2, width_ratios=[1, 2.4])
    ax_cost = fig.add_subplot(gs[0])
    ax_cap  = fig.add_subplot(gs[1])

    # ── Panel A: Fortran cost bars ────────────────────────────────────────────
    bl_cost = float(sub.loc["Baseline", "cost_mn"]) if "Baseline" in sub.index else None
    for i, case in enumerate(cases_present):
        mn = float(sub.loc[case, "cost_mn"])
        lo = float(sub.loc[case, "cost_lo"])
        hi = float(sub.loc[case, "cost_hi"])
        ax_cost.barh(i, mn, color=C[case], alpha=0.85, height=0.60, zorder=3)
        ax_cost.errorbar(mn, i, xerr=[[mn - lo], [hi - mn]],
                         fmt="none", color="#333333", capsize=3, lw=0.9, zorder=4)
        if bl_cost and case != "Baseline":
            pct = (mn / bl_cost - 1.0) * 100
            lbl = f"${mn:.1f} B\n({pct:+.1f}%)"
        else:
            lbl = f"${mn:.1f} B"
        ax_cost.text(mn * 0.50, i, lbl, ha="center", va="center",
                     fontsize=6.5, color="white", fontweight="bold")
    if bl_cost:
        ax_cost.axvline(bl_cost, color=C["Baseline"], lw=0.8, ls="--", alpha=0.5)
    ax_cost.set_yticks(range(len(cases_present)))
    ax_cost.set_yticklabels([LBL.get(c, c) for c in cases_present])
    ax_cost.set_xlabel("Annual cost ($B/yr)")
    ax_cost.set_title("A)", loc="left", fontweight="bold")
    ax_cost.set_ylim(-0.5, len(cases_present) - 0.5)

    # ── Panel B: LP vs GA(LP) capacity (GW) — grouped horizontal bars ─────────
    ga_row = sub.loc["GA (LP)"] if "GA (LP)" in sub.index else None

    # LP capacities: read directly from lp_solution.json (GW already computed)
    # — more reliable than CSV factor columns which may be NaN
    lp_sol = _load_lp_solution(lp_dir, region)
    _lp_cap = lp_sol.get("capacities_gw", {}) if lp_sol else {}

    # factor-key → lp_solution.json capacities_gw key
    _GEN_LP_KEY = {
        "FACONWIN":  "onshore_wind",
        "FACOFFWIN": "offshore_wind",
        "FACRESPV":  "res_pv",
        "FACCOMPV":  "com_pv",
        "FACUTILPV": "utility_pv",
        "CSPTURBFAC":"csp",
        "FACSHT":    "solar_thermal",
    }
    _PWR_LP_KEY = {
        "BATDISCH": "battery_power",
        "FCDISCH":  "h2_fc_power",
        "FCCHARG":  "h2_chg_power",
    }

    def _flt(row, key):
        """NaN-safe float extraction from a pandas Series row."""
        if row is None or key not in row:
            return 0.0
        v = row[key]
        if v is None:
            return 0.0
        try:
            f = float(v)
            return 0.0 if (f != f) else f   # f != f is True only for NaN
        except (ValueError, TypeError):
            return 0.0

    tech_labels, lp_vals, ga_vals = [], [], []

    for fkey, label in _CMP_GEN_KEYS:
        lv = float(_lp_cap.get(_GEN_LP_KEY.get(fkey, ""), 0.0) or 0.0)
        gv = _flt(ga_row, fkey) * ref_gw.get(fkey, 0.0)
        if lv > 0.5 or gv > 0.5:
            tech_labels.append(label); lp_vals.append(lv); ga_vals.append(gv)

    for fkey, label in _CMP_PWR_KEYS:
        lv = float(_lp_cap.get(_PWR_LP_KEY.get(fkey, ""), 0.0) or 0.0)
        gv = _flt(ga_row, fkey) * 1000
        if lv > 0.1 or gv > 0.1:
            tech_labels.append(label); lp_vals.append(lv); ga_vals.append(gv)

    if not tech_labels:
        ax_cap.text(0.5, 0.5, "No factor data available",
                    ha="center", va="center", transform=ax_cap.transAxes, fontsize=7)
    else:
        y = np.arange(len(tech_labels))
        h = 0.30
        ax_cap.barh(y + h / 2, lp_vals, h, color=C["LP"],      alpha=0.85,
                    label=LBL["LP"],      edgecolor="#444", lw=0.3)
        ax_cap.barh(y - h / 2, ga_vals, h, color=C["GA (LP)"], alpha=0.85,
                    label=LBL["GA (LP)"], edgecolor="#444", lw=0.3)

        xmax = max(max(lp_vals, default=0), max(ga_vals, default=0), 1.0)
        for i, (lv, gv) in enumerate(zip(lp_vals, ga_vals)):
            if lv > 0.5:
                ax_cap.text(lv + xmax * 0.01, i + h / 2, f"{lv:.0f}",
                            va="center", fontsize=5.5)
            if gv > 0.5:
                ax_cap.text(gv + xmax * 0.01, i - h / 2, f"{gv:.0f}",
                            va="center", fontsize=5.5)
        ax_cap.set_xlim(0, xmax * 1.22)
        ax_cap.set_yticks(y)
        ax_cap.set_yticklabels(tech_labels, fontsize=7)
        ax_cap.set_xlabel("Installed capacity (GW)")
        ax_cap.legend(frameon=False, fontsize=6.5, loc="lower right")
        ax_cap.grid(axis="x", color="#EEEEEE", lw=0.4, zorder=0)
        ax_cap.set_axisbelow(True)

    ax_cap.set_title("B)", loc="left", fontweight="bold")
    fig.suptitle(region, fontsize=9, fontweight="bold", x=0.02, ha="left")

    slug = region.lower().replace("-", "_")
    _save(fig, out / f"fig8_lp_comparison_{slug}")


# ── Figure 11: LP feasibility path — LP → first feasible → GA(LP) optimal ────

def _load_lp_history(results_dir: Path, region: str,
                     filename: str = "lp_ga_factor_history.log"):
    """Parse lp_ga_factor_history.log.

    Returns (df, lp_rec, first_feas_rec, best_rec) where each *_rec is a plain
    dict of lowercase-keyed factor values plus 'label', 'feasible',
    'cost_mn_bil_per_year'.  Returns (None, None, None, None) if the log is absent.

    Logic for 'first feasible':
      1. LP-eval record itself, if FEASIBLE=True.
      2. First inflate-subunity*/inflate-step* record that is feasible.
      3. First GA-gen*-ind* record that is feasible.
    """
    try:
        from scripts.factor_history_tools import parse_factor_history, records_to_dataframe
    except ModuleNotFoundError:
        from factor_history_tools import parse_factor_history, records_to_dataframe
    log_path = results_dir / region / filename
    if not log_path.exists():
        return None, None, None, None

    recs = parse_factor_history(log_path)
    if not recs:
        return None, None, None, None
    df = records_to_dataframe(recs)

    lp_rec = recs[0] if recs[0].get("label", "").startswith("LP") else None

    first_feas_rec = None
    for rec in recs:
        if rec.get("feasible"):
            first_feas_rec = rec
            break

    feas_df = df[df["feasible"] == True]
    if feas_df.empty:
        best_rec = None
    else:
        best_rec = feas_df.loc[feas_df["cost_mn_bil_per_year"].idxmin()].to_dict()

    return df, lp_rec, first_feas_rec, best_rec


def plot_lp_feasibility_path(
    regions: list[str],
    lp_dir: Path,
    results_dir: Path,
    out: Path,
    df: pd.DataFrame | None = None,
    countrystats: Path | None = None,
) -> None:
    """Per-region figure: LP (hourly) → first LOADMATCH-feasible → GA(LP) optimal.

    Saved as fig11_lp_feasibility_path_{slug}.pdf/png.

    Panel A: Cost progression across the three snapshots.
    Panel B: GA trial cost scatter (feasible=green, infeasible=red) with the
             three snapshots marked.
    Panel C: Generation-capacity and storage-power factors at each snapshot —
             grouped horizontal bars showing what the GA had to change to achieve
             30-second feasibility.
    """
    for region in regions:
        df_hist, lp_rec, first_feas_rec, best_rec = _load_lp_history(results_dir, region)
        if df_hist is None:
            print(f"  [skip fig11] No lp_ga_factor_history.log for {region}.")
            continue

        ref_gw = _load_ref_gw(region, countrystats) if countrystats and countrystats.exists() else {}

        # ── GA(bl) history ────────────────────────────────────────────────────
        _CLR_GABL = C["GA (bl)"]
        df_hist_bl, _, _, best_rec_gabl = _load_lp_history(
            results_dir, region, filename="factor_history.log")

        gabl_cost = None
        if best_rec_gabl is not None:
            gabl_cost = float(best_rec_gabl.get("cost_mn_bil_per_year", float("inf")))
            if np.isinf(gabl_cost):
                gabl_cost = None

        # ── Baseline from CSV ─────────────────────────────────────────────────
        _CLR_BL = C["Baseline"]
        bl_sub  = (df[(df["region"] == region) & (df["case"] == "Baseline")]
                   if df is not None else pd.DataFrame())

        def _bl_fval(key):
            if bl_sub.empty:
                return 0.0
            cols = [c for c in bl_sub.columns if c.split("/")[0].upper() == key.upper()]
            if not cols:
                return 0.0
            v = bl_sub[cols[0]].iloc[0]
            try:
                f = float(v)
                return 0.0 if (f != f) else f
            except (TypeError, ValueError):
                return 0.0

        bl_cost = float(bl_sub["cost_mn"].iloc[0]) if not bl_sub.empty else None

        # ── Classify the three comparison points ─────────────────────────────
        lp_feasible    = bool(lp_rec.get("feasible", False)) if lp_rec else False
        lp_cost        = float(lp_rec.get("cost_mn_bil_per_year", float("inf"))) if lp_rec else float("inf")

        ff_label  = first_feas_rec.get("label", "?") if first_feas_rec else "—"
        ff_cost   = float(first_feas_rec.get("cost_mn_bil_per_year", float("inf"))) if first_feas_rec else float("inf")
        ff_is_lp  = (first_feas_rec is lp_rec or ff_label == "LP-eval") if first_feas_rec else True

        best_cost = float(best_rec.get("cost_mn_bil_per_year", float("inf"))) if best_rec else float("inf")
        best_label = best_rec.get("label", "?") if best_rec else "—"

        # Trial index of first feasible (for annotation)
        first_feas_trial = None
        if first_feas_rec and not feas_empty(df_hist):
            match = df_hist[df_hist["label"] == ff_label]
            if not match.empty:
                first_feas_trial = int(match.index[0])

        # ── Key factors to display ────────────────────────────────────────────
        # (factor_key, display_label, unit, scale)
        # scale converts raw factor value to displayable GW: factor × ref_gw[key]
        # For TW factors (BATDISCH, FCDISCH, FCCHARG): raw×1000 = GW
        DISP_PARAMS = [
            ("faconwin",   "Onshore wind",   "GW", lambda v: v * ref_gw.get("FACONWIN",  0)),
            ("facoffwin",  "Offshore wind",  "GW", lambda v: v * ref_gw.get("FACOFFWIN", 0)),
            ("facutilpv",  "Utility PV",     "GW", lambda v: v * ref_gw.get("FACUTILPV", 0)),
            ("facrespv",   "Res. PV",        "GW", lambda v: v * ref_gw.get("FACRESPV",  0)),
            ("cspturbfac", "CSP",            "GW", lambda v: v * ref_gw.get("CSPTURBFAC",0)),
            ("batdisch",   "Battery (pow.)", "GW", lambda v: v * 1000),
            ("fcdisch",    "Fuel Cell",       "GW", lambda v: v * 1000),
            ("fccharg",    "Electrolyser",   "GW", lambda v: v * 1000),
        ]

        def _fval(rec, key):
            if rec is None:
                return 0.0
            v = rec.get(key, rec.get(key.upper(), 0.0))
            if v is None:
                return 0.0
            try:
                f = float(v)
                return 0.0 if (f != f) else f   # NaN check
            except (ValueError, TypeError):
                return 0.0

        # Build display arrays
        param_labels = []
        bl_gw, gabl_gw, lp_gw, ff_gw, best_gw = [], [], [], [], []
        unit_labels = []
        for fkey, lbl, unit, conv in DISP_PARAMS:
            raw_bl   = _bl_fval(fkey)
            raw_gabl = _fval(best_rec_gabl, fkey) if best_rec_gabl else 0.0
            raw_lp   = _fval(lp_rec, fkey)
            raw_ff   = _fval(first_feas_rec, fkey)
            raw_best = _fval(best_rec, fkey)
            blv  = conv(raw_bl)
            gblv = conv(raw_gabl)
            lv   = conv(raw_lp)
            fv   = conv(raw_ff)
            bv   = conv(raw_best)
            if (max(abs(blv), abs(gblv), abs(lv), abs(fv), abs(bv)) < 0.1
                    and max(abs(raw_bl), abs(raw_gabl), abs(raw_lp),
                            abs(raw_ff), abs(raw_best)) < 0.01):
                continue
            param_labels.append(lbl)
            bl_gw.append(blv);  gabl_gw.append(gblv)
            lp_gw.append(lv);   ff_gw.append(fv); best_gw.append(bv)
            unit_labels.append(unit)

        bl_arr   = np.array(bl_gw)
        gabl_arr = np.array(gabl_gw)
        lp_arr   = np.array(lp_gw)
        ff_arr   = np.array(ff_gw)
        best_arr = np.array(best_gw)
        n_params = len(param_labels)

        # Three-step colors — used consistently across all three panels.
        # vermillion (#D55E00) is CVD-safe and distinct from LP-blue and GA-green.
        _CLR_LP   = C["LP"]        # blue:      LP (raw, hourly)
        _CLR_FF   = "#D55E00"      # vermillion: first LOADMATCH-feasible (30-s)
        _CLR_BEST = C["GA (LP)"]   # green:     GA(LP) optimal

        # ── Figure layout ─────────────────────────────────────────────────────
        fig = plt.figure(figsize=(8.5, max(5.5, 1.5 + n_params * 0.42)),
                         layout="constrained")
        gs      = fig.add_gridspec(1, 3, width_ratios=[0.9, 1.5, 2.2])
        ax_cost  = fig.add_subplot(gs[0])
        ax_trail = fig.add_subplot(gs[1])
        # Split column C: upper = storage TWh, lower = generation/power capacities
        gs_c    = gs[2].subgridspec(2, 1, height_ratios=[1, 2.5])
        ax_twh  = fig.add_subplot(gs_c[0])
        ax_fac  = fig.add_subplot(gs_c[1])

        # ── Panel A: cost milestones — LP → first-feasible → GA(LP) optimal ──
        # LP row uses the LP solver's own objective (proxy CAPEX, B$/yr) from
        # lp_solution.json.  First-feasible and GA(LP) use LOADMATCH cost.
        # Both metrics are in B$/yr but are not directly comparable — the LP
        # proxy covers annualised CAPEX only; LOADMATCH covers full system cost.
        lp_sol_data   = _load_lp_solution(lp_dir, region)
        lp_proxy_cost = (lp_sol_data.get("cost_proxy", {}).get("total_B_usd_per_yr")
                         if lp_sol_data else None)
        lp_bar_cost   = float(lp_proxy_cost) if lp_proxy_cost is not None else None

        # Order bottom→top: GA(LP) y=0, first feasible y=1, LP y=2, GA(bl) y=3, Baseline y=4
        STEP_LABELS = ["GA (LP)\noptimal",
                       "First LOADMATCH-\nfeasible",
                       "LP (raw)\n(hourly opt.)",
                       "GA (bl)\noptimal",
                       "Baseline"]
        STEP_COLORS = [_CLR_BEST, _CLR_FF, _CLR_LP, _CLR_GABL, _CLR_BL]
        STEP_COSTS  = [best_cost, ff_cost, lp_bar_cost, gabl_cost, bl_cost]

        all_finite = [c for c in STEP_COSTS if c is not None and not np.isinf(c)]
        xmax = max(all_finite) * 1.30 if all_finite else 1.0

        for i, (lbl, clr, cost) in enumerate(
                zip(STEP_LABELS, STEP_COLORS, STEP_COSTS)):
            if cost is None or np.isinf(cost):
                ax_cost.barh(i, xmax, color=clr, alpha=0.07, height=0.55,
                             edgecolor=clr, linewidth=0.8, linestyle="--",
                             hatch="///", zorder=1)
                ax_cost.text(xmax * 0.50, i, "cost unavailable",
                             ha="center", va="center", fontsize=6,
                             color=clr, style="italic")
            else:
                ax_cost.barh(i, cost, color=clr, alpha=0.82, height=0.55,
                             zorder=3, edgecolor=clr, lw=0.6)
                ax_cost.text(cost * 0.50, i, f"${cost:.1f}B",
                             ha="center", va="center", fontsize=6.5,
                             color="white", fontweight="bold")

        if not lp_feasible and lp_bar_cost is not None:
            ax_cost.text(xmax * 0.02, 2.28,
                         "LP proxy cost (CAPEX only);\nLOADMATCH-infeasible",
                         va="bottom", fontsize=5.0, color=_CLR_LP,
                         style="italic")
        ax_cost.set_xlim(0, xmax)
        ax_cost.set_yticks(range(5))
        ax_cost.set_yticklabels(STEP_LABELS, fontsize=6.5)
        ax_cost.set_xlabel("Cost ($B/yr)")
        ax_cost.set_title("A)", loc="left", fontweight="bold")
        ax_cost.set_ylim(-0.6, 4.6)
        ax_cost.grid(axis="x", color="#EEEEEE", lw=0.4)
        ax_cost.set_axisbelow(True)

        # ── Panel B: trial cost scatter (feasible trials only) ───────────────
        n_trials = len(df_hist)
        trial_x  = df_hist["trial"].values
        trial_c  = df_hist["cost_mn_bil_per_year"].values
        trial_f  = df_hist["feasible"].values.astype(bool)

        inf_mask = np.isinf(trial_c) | (trial_c > 1e6)
        fin_mask = ~inf_mask & trial_f      # feasible + finite cost

        # GA(bl) feasible trials
        bl_fin_mask = None
        if df_hist_bl is not None and not df_hist_bl.empty:
            bl_trial_x = df_hist_bl["trial"].values
            bl_trial_c = df_hist_bl["cost_mn_bil_per_year"].values
            bl_trial_f = df_hist_bl["feasible"].values.astype(bool)
            bl_inf     = np.isinf(bl_trial_c) | (bl_trial_c > 1e6)
            bl_fin_mask = ~bl_inf & bl_trial_f

        all_feas_costs = list(trial_c[fin_mask]) + (
            list(bl_trial_c[bl_fin_mask]) if bl_fin_mask is not None and bl_fin_mask.any() else [])
        y_top = float(max(all_feas_costs)) * 1.08 if all_feas_costs else None

        # GA(LP) feasible scatter
        ax_trail.scatter(trial_x[fin_mask], trial_c[fin_mask],
                         color=_CLR_BEST, s=6, alpha=0.60, zorder=3,
                         linewidths=0,
                         label=f"GA(LP) feasible ({fin_mask.sum()})")

        # GA(bl) feasible scatter
        if bl_fin_mask is not None and bl_fin_mask.any():
            ax_trail.scatter(bl_trial_x[bl_fin_mask], bl_trial_c[bl_fin_mask],
                             color=_CLR_GABL, s=6, alpha=0.60, zorder=3,
                             linewidths=0,
                             label=f"GA(bl) feasible ({bl_fin_mask.sum()})")

        def _mark_trial(label_str, cost_val, color, marker, ms, zord, mk_label=None,
                        src_df=None):
            df_src = src_df if src_df is not None else df_hist
            y_val = y_top if (np.isinf(cost_val) and y_top is not None) else (
                None if np.isinf(cost_val) else cost_val)
            if y_val is None:
                return
            match = df_src[df_src["label"] == label_str]
            tx = int(match.index[0]) if not match.empty else 0
            ax_trail.scatter(tx, y_val, color=color, s=ms, marker=marker,
                             zorder=zord + 2, edgecolors="white", linewidths=0.8,
                             label=mk_label)

        if first_feas_rec:
            ff_mk_lbl = ("First feasible (= LP)"
                         if ff_is_lp else "First LOADMATCH-feasible")
            _mark_trial(ff_label, ff_cost, _CLR_FF, "*", 90, 7,
                        mk_label=ff_mk_lbl)
        _mark_trial(best_label, best_cost, _CLR_BEST, "o", 60, 6,
                    mk_label="GA(LP) optimal")

        # GA(bl) best marker
        if best_rec_gabl is not None and gabl_cost is not None:
            gabl_best_label = best_rec_gabl.get("label", "?")
            _mark_trial(gabl_best_label, gabl_cost, _CLR_GABL, "D", 55, 6,
                        mk_label="GA(bl) optimal", src_df=df_hist_bl)

        if bl_cost is not None:
            ax_trail.axhline(bl_cost, color=_CLR_BL, lw=1.0, ls="--",
                             alpha=0.8, label=f"Baseline (${bl_cost:.0f}B)",
                             zorder=2)
        ax_trail.set_xlabel("GA trial index")
        ax_trail.set_ylabel("LOADMATCH cost ($B/yr)")
        ax_trail.set_title("B)", loc="left", fontweight="bold")
        ax_trail.legend(frameon=False, fontsize=5.5, loc="upper right",
                        markerscale=0.8, handlelength=1.0,
                        borderpad=0.6, labelspacing=0.7)
        ax_trail.grid(color="#EEEEEE", lw=0.4)
        ax_trail.set_axisbelow(True)

        if not lp_feasible and first_feas_trial is not None:
            ax_trail.axvline(first_feas_trial, color=_CLR_FF, lw=0.8,
                             ls="--", zorder=5)
            ax_trail.text(first_feas_trial + n_trials * 0.01,
                          ax_trail.get_ylim()[1],
                          f"trial {first_feas_trial}", fontsize=6, color=_CLR_FF,
                          va="top")

        # ── Panel C: storage energy (TWh) ────────────────────────────────────
        stor_bl   = np.array([
            _bl_fval("batdisch") * _bl_fval("storhbat"),
            _bl_fval("fcdisch")  * _bl_fval("dayh2stor") * 24,
        ])
        stor_gabl = np.array([
            _fval(best_rec_gabl,  "batdisch") * _fval(best_rec_gabl,  "storhbat"),
            _fval(best_rec_gabl,  "fcdisch")  * _fval(best_rec_gabl,  "dayh2stor") * 24,
        ])
        stor_lp   = np.array([
            _fval(lp_rec,         "batdisch") * _fval(lp_rec,         "storhbat"),
            _fval(lp_rec,         "fcdisch")  * _fval(lp_rec,         "dayh2stor") * 24,
        ])
        stor_ff   = np.array([
            _fval(first_feas_rec, "batdisch") * _fval(first_feas_rec, "storhbat"),
            _fval(first_feas_rec, "fcdisch")  * _fval(first_feas_rec, "dayh2stor") * 24,
        ])
        stor_best = np.array([
            _fval(best_rec, "batdisch") * _fval(best_rec, "storhbat"),
            _fval(best_rec, "fcdisch")  * _fval(best_rec, "dayh2stor") * 24,
        ])
        stor_labels = ["Battery", "Hydrogen"]

        y_s   = np.arange(2)
        h_s   = 0.14
        off_s = h_s + 0.02
        ax_twh.barh(y_s + 2*off_s, stor_bl,   h_s, color=_CLR_BL,   alpha=0.85,
                    label="Baseline",        edgecolor="#444", lw=0.3)
        ax_twh.barh(y_s + 1*off_s, stor_gabl, h_s, color=_CLR_GABL, alpha=0.85,
                    label="GA (bl) optimal", edgecolor="#444", lw=0.3)
        ax_twh.barh(y_s,           stor_lp,   h_s, color=_CLR_LP,   alpha=0.85,
                    label="LP (raw)",        edgecolor="#444", lw=0.3)
        ax_twh.barh(y_s - 1*off_s, stor_ff,   h_s, color=_CLR_FF,   alpha=0.82,
                    label="First feasible",  edgecolor="#444", lw=0.3)
        ax_twh.barh(y_s - 2*off_s, stor_best, h_s, color=_CLR_BEST, alpha=0.85,
                    label="GA (LP) optimal", edgecolor="#444", lw=0.3)

        xmax_twh = max(stor_bl.max(), stor_gabl.max(), stor_lp.max(),
                       stor_ff.max(), stor_best.max(), 1.0)
        pad_twh  = xmax_twh * 0.012
        for i, (blv, gblv, lv, fv, bv) in enumerate(
                zip(stor_bl, stor_gabl, stor_lp, stor_ff, stor_best)):
            for val, yi, clr in [
                    (blv,  i+2*off_s, _CLR_BL),
                    (gblv, i+1*off_s, _CLR_GABL),
                    (lv,   i,         None),
                    (fv,   i-1*off_s, _CLR_FF),
                    (bv,   i-2*off_s, None)]:
                if val > xmax_twh * 0.01:
                    kw = dict(color=clr) if clr else {}
                    ax_twh.text(val + pad_twh, yi, f"{val:.0f}",
                                va="center", fontsize=5.0, **kw)

        ax_twh.set_xlim(0, xmax_twh * 1.22)
        ax_twh.set_yticks(y_s)
        ax_twh.set_yticklabels(stor_labels, fontsize=6.5)
        ax_twh.set_xlabel("Storage energy (TWh)")
        ax_twh.legend(frameon=False, fontsize=6, loc="lower right")
        ax_twh.grid(axis="x", color="#EEEEEE", lw=0.4)
        ax_twh.set_axisbelow(True)
        ax_twh.set_title("C)", loc="left", fontweight="bold")

        # ── Panel D: generation & power capacities ────────────────────────────
        y   = np.arange(n_params)
        h   = 0.14
        off = h + 0.02

        ax_fac.barh(y + 2*off, bl_arr,   h, color=_CLR_BL,   alpha=0.85,
                    label="Baseline",        edgecolor="#444", lw=0.3)
        ax_fac.barh(y + 1*off, gabl_arr, h, color=_CLR_GABL, alpha=0.85,
                    label="GA (bl) optimal", edgecolor="#444", lw=0.3)
        ax_fac.barh(y,         lp_arr,   h, color=_CLR_LP,   alpha=0.85,
                    label="LP (raw)",        edgecolor="#444", lw=0.3)
        ax_fac.barh(y - 1*off, ff_arr,   h, color=_CLR_FF,   alpha=0.82,
                    label="First feasible",  edgecolor="#444", lw=0.3)
        ax_fac.barh(y - 2*off, best_arr, h, color=_CLR_BEST, alpha=0.85,
                    label="GA (LP) optimal", edgecolor="#444", lw=0.3)

        xmax_fac = max(np.concatenate([bl_arr, gabl_arr, lp_arr, ff_arr, best_arr]).max(), 1.0)
        for i, (blv, gblv, lv, fv, bv) in enumerate(
                zip(bl_arr, gabl_arr, lp_arr, ff_arr, best_arr)):
            pad = xmax_fac * 0.012
            for val, yi, clr in [
                    (blv,  i+2*off, _CLR_BL),
                    (gblv, i+1*off, _CLR_GABL),
                    (lv,   i,       None),
                    (fv,   i-1*off, _CLR_FF),
                    (bv,   i-2*off, None)]:
                if val > xmax_fac * 0.01:
                    kw = dict(color=clr) if clr else {}
                    ax_fac.text(val + pad, yi, f"{val:.0f}",
                                va="center", fontsize=5.0, **kw)

        ax_fac.set_xlim(0, xmax_fac * 1.22)
        ax_fac.set_yticks(y)
        ax_fac.set_yticklabels(param_labels, fontsize=6.5)
        ax_fac.set_xlabel("Capacity (GW)")
        ax_fac.grid(axis="x", color="#EEEEEE", lw=0.4)
        ax_fac.set_axisbelow(True)
        ax_fac.set_title("D)", loc="left", fontweight="bold")

        slug = region.lower().replace("-", "_")
        _save(fig, out / f"fig11_lp_feasibility_path_{slug}")


def feas_empty(df):
    return df[df["feasible"] == True].empty


# ── Figure 12: Capacity boxplots — all regions, normalised to baseline ────────

def plot_capacity_boxplots(
    lp_dir: Path,
    results_dir: Path,
    out: Path,
    df: pd.DataFrame | None = None,
) -> None:
    """Grouped boxplots of capacity values relative to each region's baseline.

    One group per capacity parameter; within each group: LP (raw), First
    LOADMATCH-feasible, GA(bl) optimal, GA(LP) optimal.  Each boxplot shows
    the spread across all available regions.
    Saved as fig12_capacity_boxplots.pdf/png.
    """
    if df is None:
        print("  [skip fig12] No results CSV loaded.")
        return

    _CLR_FF = "#D55E00"   # vermillion — first LOADMATCH-feasible

    # 4 cases left → right: GA(bl), LP (raw), First feasible, GA(LP)
    # GA(bl) / LP / GA(LP) come from the CSV (all 29 regions).
    # First-feasible comes from lp_ga_factor_history.log (regions with log only).
    CASE_LABELS = ["GA (bl)\noptimal", "LP\n(raw)", "First\nfeasible", "GA (LP)\noptimal"]
    CASE_COLORS = [C["GA (bl)"], C["LP"], _CLR_FF, C["GA (LP)"]]
    N_CASES = len(CASE_LABELS)

    # Compute derived TWh columns (product of two factor cols) for every row.
    dfw = df.copy()
    dfw["BAT_TWH"] = dfw["BATDISCH"] * dfw["STORHBAT"]
    dfw["H2_TWH"]  = dfw["FCDISCH"]  * dfw["DAYH2STOR"]

    # Parameter specs: (csv_col, x_label)
    GEN_SPECS  = [
        ("FACONWIN",  "Onshore\nwind"),
        ("FACOFFWIN", "Offshore\nwind"),
        ("FACUTILPV", "Utility\nPV"),
        ("FACRESPV",  "Res. PV"),
        ("CSPTURBFAC","CSP"),
    ]
    STOR_SPECS = [
        ("BATDISCH",  "Battery\npower"),
        ("FCDISCH",   "Fuel Cell"),
        ("FCCHARG",   "Electrolyser"),
        ("BAT_TWH",   "Battery\n(TWh)"),
        ("H2_TWH",    "Hydrogen\n(TWh)"),
    ]
    ALL_SPECS = GEN_SPECS + STOR_SPECS
    N_GEN  = len(GEN_SPECS)

    # ── Collect ratios ─────────────────────────────────────────────────────────
    # ratios[param_idx][case_idx] = list of per-region ratio values
    ratios = [[[] for _ in range(N_CASES)] for _ in range(len(ALL_SPECS))]

    bl_df = dfw[dfw["case"] == "Baseline"].set_index("region")

    # All four cases sourced from CSV
    for ci, csv_case in [(0, "GA (bl)"), (1, "LP"), (2, "First feasible"), (3, "GA (LP)")]:
        case_df = dfw[dfw["case"] == csv_case].set_index("region")
        for region in case_df.index:
            if region not in bl_df.index:
                continue
            for pi, (col, _) in enumerate(ALL_SPECS):
                try:
                    bl_val   = float(bl_df.loc[region, col])
                    case_val = float(case_df.loc[region, col])
                except (KeyError, ValueError, TypeError):
                    continue
                if bl_val > 0 and case_val > 0 and not np.isnan(bl_val) and not np.isnan(case_val):
                    ratios[pi][ci].append(case_val / bl_val)

    # ── Shared box geometry ────────────────────────────────────────────────────
    BOX_W   = 0.60
    PITCH   = BOX_W + 0.25   # centre-to-centre within a group
    GROUP_W = N_CASES * PITCH + 1.20   # group span incl. gutter

    def _draw_panel(ax, specs, ratio_offset, yscale="linear"):
        """Draw one row of grouped boxplots onto ax."""
        rng = np.random.RandomState(42)
        n   = len(specs)
        for pi in range(n):
            x_group = pi * GROUP_W
            for ci in range(N_CASES):
                x_pos = x_group + ci * PITCH
                data  = ratios[ratio_offset + pi][ci]
                if not data:
                    continue
                ax.boxplot(
                    data, positions=[x_pos], widths=BOX_W,
                    patch_artist=True,
                    medianprops=dict(color="white", lw=1.8, solid_capstyle="round"),
                    boxprops=dict(facecolor=CASE_COLORS[ci], alpha=0.72,
                                  edgecolor=CASE_COLORS[ci]),
                    whiskerprops=dict(color=CASE_COLORS[ci], lw=0.9),
                    capprops=dict(color=CASE_COLORS[ci], lw=0.9),
                    flierprops=dict(marker="o", markersize=3.5,
                                    markerfacecolor=CASE_COLORS[ci],
                                    markeredgewidth=0, alpha=0.55),
                    showfliers=True,
                )
                jitter = rng.uniform(-0.14, 0.14, len(data))
                ax.scatter(np.array([x_pos] * len(data)) + jitter, data,
                           color=CASE_COLORS[ci], s=14, alpha=0.70,
                           zorder=5, linewidths=0)

        centres = [pi * GROUP_W + (N_CASES - 1) * PITCH / 2 for pi in range(n)]
        ax.set_xticks(centres)
        ax.set_xticklabels([s[1] for s in specs], fontsize=7)
        ax.set_xlim(-0.7, (n - 1) * GROUP_W + (N_CASES - 1) * PITCH + 0.7)
        ax.axhline(1.0, color="#888888", lw=0.9, ls="--", zorder=1)
        ax.set_ylabel("Ratio to baseline")
        ax.grid(axis="y", color="#EEEEEE", lw=0.4, zorder=0)
        ax.set_axisbelow(True)

    # ── Figure: two rows — double-column journal size ─────────────────────────
    fig, (ax_gen, ax_stor) = plt.subplots(
        2, 1, figsize=(7.0, 5.25), layout="constrained"
    )

    _draw_panel(ax_gen,  GEN_SPECS,  ratio_offset=0,     yscale="log")
    _draw_panel(ax_stor, STOR_SPECS, ratio_offset=N_GEN, yscale="log")

    for ax, panel_letter, subtitle, ylo in [
        (ax_gen,  "A", "Generation capacities",       0.05),
        (ax_stor, "B", "Storage & power capacities",  0.1),
    ]:
        ax.set_yscale("log")
        ax.set_ylim(bottom=ylo)
        ax.yaxis.set_major_locator(mticker.LogLocator(base=10, numticks=8))
        ax.yaxis.set_major_formatter(mticker.FuncFormatter(
            lambda v, _: (f"{v:.0f}" if v >= 1 else f"{v:.1f}") if v > 0 else ""
        ))
        ax.yaxis.set_minor_locator(
            mticker.LogLocator(base=10, subs=np.arange(2, 10), numticks=50))
        ax.yaxis.set_minor_formatter(mticker.NullFormatter())
        ax.set_title(f"{panel_letter})  {subtitle}", loc="left", fontweight="bold")

    legend_handles = [
        mpl.patches.Patch(facecolor=CASE_COLORS[ci], alpha=0.72,
                          edgecolor=CASE_COLORS[ci], label=CASE_LABELS[ci])
        for ci in range(N_CASES)
    ] + [mpl.lines.Line2D([0], [0], color="#888888", lw=1.0, ls="--",
                           label="Baseline")]
    ax_gen.legend(handles=legend_handles, frameon=False, fontsize=7,
                  ncol=N_CASES + 1, loc="upper left",
                  handlelength=1.2, handletextpad=0.4, columnspacing=1.0,
                  bbox_to_anchor=(0.0, 1.0))

    _save(fig, out / "fig12_capacity_boxplots")


# ── Figure 9: LP storage deep-dive — December storage state ──────────────────

def plot_lp_storage(regions: list[str], lp_dir: Path, out: Path) -> None:
    """Three-panel December storage deep-dive — one figure per region.

    Highlights where the hourly LP solution is 'tight' (battery near empty/full),
    the exact hours where 30-second sub-hourly variability can cause LOADMATCH
    infeasibility that the hourly LP cannot anticipate.
    """
    for region in regions:
        df  = _load_lp_dispatch(lp_dir, region)
        sol = _load_lp_solution(lp_dir, region)
        if df is None or sol is None:
            print(f"  [skip fig9] No LP dispatch data for {region}.")
            continue

        GW         = 1e-3
        hrs        = np.arange(len(df))
        month_name = sol["exemplary_month"]["month_name"]

        bat_cap_mwh = sol["storage_gwh"]["battery"] * 1e3
        h2_cap_mwh  = sol["storage_gwh"]["h2"] * 1e3
        bat_pow_mw  = sol["capacities_gw"]["battery_power"] * 1e3
        h2_fc_mw    = sol["capacities_gw"]["h2_fc_power"]  * 1e3
        h2_chg_mw   = sol["capacities_gw"]["h2_chg_power"] * 1e3

        net_elec_gw = (
            df["elec_load_mw"]
            - df["gen_wind_on_mw"] - df["gen_wind_off_mw"]
            - df["gen_pv_res_mw"]  - df["gen_pv_com_mw"] - df["gen_pv_util_mw"]
            - df["fixed_elec_mw"]  - df["gen_csp_mw"]
        ).values * GW

        soc_bat_pct = df["soc_bat_mwh"].values / bat_cap_mwh * 100
        soc_h2_pct  = (df["soc_h2_mwh"].values / h2_cap_mwh * 100
                       if h2_cap_mwh > 0 else np.zeros(len(df)))

        fig, axes = plt.subplots(3, 1, figsize=(7.0, 7.2),
                                 layout="constrained", sharex=True)
        ax_net, ax_bat, ax_h2 = axes

        tick_h = np.arange(0, len(hrs), 168)

        # ── Panel A: Net residual electric load ───────────────────────────────
        ax_net.fill_between(hrs, net_elec_gw, 0,
                            where=(net_elec_gw >= 0),
                            color="#CC6677", alpha=0.72, label="Storage deficit")
        ax_net.fill_between(hrs, net_elec_gw, 0,
                            where=(net_elec_gw < 0),
                            color="#56B4E9", alpha=0.60, label="Storage surplus")
        ax_net.axhline(0, color="#666666", lw=0.8, zorder=3)
        ax_net.set_ylabel("Net residual\nelectric load (GW)")
        ax_net.set_title("A)  Net residual load  (electric demand − VRE − fixed baseload)",
                         loc="left", fontweight="bold", fontsize=7.5)
        ramp_std = float(np.diff(net_elec_gw).std())
        ax_net.text(0.99, 0.97,
                    f"Hour-to-hour ramp σ = {ramp_std:.0f} GW",
                    ha="right", va="top", transform=ax_net.transAxes,
                    fontsize=6.5, color="#444444")
        ax_net.legend(frameon=False, fontsize=6, ncol=2, loc="lower right")
        ax_net.grid(axis="y", color="#EEEEEE", lw=0.4, zorder=0)
        ax_net.set_axisbelow(True)

        # ── Panel B: Battery SOC (%) with critical-zone shading ──────────────
        LOW_PCT  = 5.0
        HIGH_PCT = 95.0
        n_low  = int((soc_bat_pct < LOW_PCT).sum())
        n_high = int((soc_bat_pct > HIGH_PCT).sum())
        n_hrs  = len(hrs)

        ax_bat.axhspan(0,        LOW_PCT,  color="#CC6677", alpha=0.13, zorder=0)
        ax_bat.axhspan(HIGH_PCT, 100,      color="#0072B2", alpha=0.10, zorder=0)
        ax_bat.plot(hrs, soc_bat_pct, color="#CC79A7", lw=1.1, zorder=4,
                    label="Battery SOC")
        ax_bat.set_ylim(0, 100)
        ax_bat.set_ylabel("Battery\nSOC (%)")
        ax_bat.set_title("B)  Battery state-of-charge  (hourly LP)",
                         loc="left", fontweight="bold", fontsize=7.5)

        ax_bat.text(0.01, 0.04,
                    f"Near-empty (<{LOW_PCT:.0f}%): {n_low}/{n_hrs} h ({n_low/n_hrs*100:.0f}%)",
                    ha="left", va="bottom", transform=ax_bat.transAxes,
                    fontsize=6.5, color="#CC6677")
        ax_bat.text(0.99, 0.97,
                    f"Near-full (>{HIGH_PCT:.0f}%): {n_high}/{n_hrs} h ({n_high/n_hrs*100:.0f}%)",
                    ha="right", va="top", transform=ax_bat.transAxes,
                    fontsize=6.5, color="#0072B2")
        ax_bat.text(0.50, 0.50,
                    "Near-empty hours: 30-s wind/solar variability\n"
                    "can cause LOADMATCH load-shed",
                    ha="center", va="center", transform=ax_bat.transAxes,
                    fontsize=6.0, color="#AA3333", style="italic",
                    bbox=dict(facecolor="white", edgecolor="none", alpha=0.75, pad=2))
        ax_bat.legend(frameon=False, fontsize=6, loc="upper right")
        ax_bat.grid(axis="y", color="#EEEEEE", lw=0.4, zorder=0)
        ax_bat.set_axisbelow(True)

        # ── Panel C: H2 storage SOC + electrolyser/fuel cell ─────────────────
        has_h2 = h2_cap_mwh > 0
        if has_h2:
            ax_h2.plot(hrs, soc_h2_pct, color="#D55E00", lw=1.1, zorder=4,
                       label="H₂ SOC")
            ax_h2.set_ylim(0, 100)
            ax_h2.set_ylabel("H₂ storage\nSOC (%)")

            ax_h2b = ax_h2.twinx()
            ax_h2b.spines["right"].set_linewidth(0.5)
            ax_h2b.spines["top"].set_visible(False)
            elec_mw = df["electrolyser_mw"].values
            fc_mw   = df["fuelcell_mw"].values
            if elec_mw.max() > 1:
                ax_h2b.fill_between(hrs, elec_mw * GW, 0,
                                    color="#882255", alpha=0.45, label="Electrolyser")
            if fc_mw.max() > 1:
                ax_h2b.fill_between(hrs, fc_mw * GW, 0,
                                    color="#D55E00", alpha=0.45, label="Fuel cell")
            max_pwr = max(elec_mw.max(), fc_mw.max(), 1.0) * GW
            ax_h2b.set_ylim(0, max_pwr * 2.2)
            ax_h2b.set_ylabel("Power (GW)", fontsize=6.5, color="#555555")
            ax_h2b.tick_params(axis="y", labelsize=6, labelcolor="#555555")

            n_elec = int((elec_mw > 1).sum())
            n_fc   = int((fc_mw   > 1).sum())
            ax_h2.text(0.99, 0.97,
                       f"Electrolyser active: {n_elec} h  |  Fuel cell active: {n_fc} h",
                       ha="right", va="top", transform=ax_h2.transAxes,
                       fontsize=6.5, color="#444444")

            soc_handles = [mpl.lines.Line2D([], [], color="#D55E00", lw=1.1,
                                            label="H₂ SOC")]
            pwr_handles = []
            if elec_mw.max() > 1:
                pwr_handles.append(mpl.patches.Patch(color="#882255", alpha=0.45,
                                                      label="Electrolyser (GW, right)"))
            if fc_mw.max() > 1:
                pwr_handles.append(mpl.patches.Patch(color="#D55E00", alpha=0.45,
                                                      label="Fuel cell (GW, right)"))
            ax_h2.legend(handles=soc_handles + pwr_handles,
                         frameon=False, fontsize=6, loc="upper left")
        else:
            ax_h2.text(0.5, 0.5, "No H₂ storage installed",
                       ha="center", va="center", transform=ax_h2.transAxes,
                       fontsize=7, color="#888888")
            ax_h2.set_ylabel("H₂ storage\nSOC (%)")

        ax_h2.set_title("C)  H₂ storage  (capacity = seasonal buffer)",
                        loc="left", fontweight="bold", fontsize=7.5)
        ax_h2.grid(axis="y", color="#EEEEEE", lw=0.4, zorder=0)
        ax_h2.set_axisbelow(True)

        for ax in axes:
            ax.set_xticks(tick_h)
        ax_h2.set_xticklabels(
            [f"{month_name[:3]} {1 + int(t) // 24}" for t in tick_h]
        )
        ax_h2.set_xlabel(f"{month_name} (hourly LP dispatch)")

        fig.suptitle(
            f"{region}  —  Storage state during peak-demand month ({month_name})\n"
            f"Hourly LP sees averages; 30-s LOADMATCH sees within-hour variability",
            fontsize=8.5, fontweight="bold", x=0.02, ha="left",
        )
        slug = region.lower().replace("-", "_")
        _save(fig, out / f"fig9_lp_storage_{slug}")


# ── Figure 10: LP warm-start effectiveness — dumbbell dot plot ───────────────

def plot_lp_effectiveness(df: pd.DataFrame, out: Path) -> None:
    """Dumbbell dot plot: LP and GA(LP) normalised costs for all regions.

    Every region with GA(LP) data appears as a row.  Where LP data is also
    available, a blue LP marker is added and a coloured connector shows whether
    the GA improved (green) or worsened (red) on the LP starting point.
    """
    mn = _wide(df, "cost_mn")
    bl = mn["Baseline"]

    lp_n   = (mn["LP"]      / bl) if "LP"      in mn.columns else pd.Series(dtype=float)
    galp_n = (mn["GA (LP)"] / bl) if "GA (LP)" in mn.columns else pd.Series(dtype=float)

    lp_avail   = lp_n.dropna()
    galp_avail = galp_n.dropna()
    both       = lp_avail.index.intersection(galp_avail.index)

    print(f"  [fig10] feasible LP: {len(lp_avail)}, feasible GA(LP): {len(galp_avail)}, "
          f"both: {len(both)}")
    if len(lp_avail) < len(galp_avail):
        missing = sorted(set(galp_avail.index) - set(lp_avail.index))
        print(f"  [fig10] GA(LP) regions without LP data: {missing}")

    if galp_avail.empty:
        print("  [skip fig10] No GA(LP) data available.")
        return

    # Sort by GA(LP)/BL descending so lowest-cost regions are at the bottom
    order = galp_avail.sort_values(ascending=False).index.tolist()
    n = len(order)
    y_pos = {r: i for i, r in enumerate(order)}

    # Axis limits: cover all LP and GA(LP) values with a little padding
    all_vals = list(galp_avail.values) + list(lp_avail.values)
    all_finite = [v for v in all_vals if np.isfinite(v)]
    xlim_right = min(max(all_finite) + 0.04, 1.40)
    xlim_left  = max(min(all_finite) - 0.02, 0.70)

    fig, ax = plt.subplots(figsize=(3.5, max(2.625, n * 0.27)))

    # ── Connecting lines (drawn first, behind dots) ───────────────────────────
    for r in both:
        lv = float(lp_avail[r])
        gv = float(galp_avail[r])
        yi = y_pos[r]
        delta = gv - lv
        line_clr = ("#009E73" if delta < -0.005
                    else "#CC6677" if delta > 0.005
                    else "#888888")
        lv_plot = min(lv, xlim_right)
        gv_plot = min(gv, xlim_right)
        ax.plot([lv_plot, gv_plot], [yi, yi],
                color=line_clr, lw=1.4, alpha=0.65, zorder=2)

    # ── GA(LP) dots — all regions ─────────────────────────────────────────────
    for r in order:
        gv = float(galp_avail[r])
        yi = y_pos[r]
        gv_plot = min(gv, xlim_right)
        ax.scatter(gv_plot, yi, color=C["GA (LP)"], marker=MK["GA (LP)"],
                   s=MKSZ["GA (LP)"], zorder=4, linewidths=0)
        if gv > xlim_right:
            ax.scatter(xlim_right, yi, marker=">", color=C["GA (LP)"],
                       s=18, zorder=6, clip_on=False)
            ax.text(xlim_right - 0.004, yi + 0.38,
                    f"GA(LP): {gv:.2f}×", ha="right", va="bottom",
                    fontsize=5.5, color=C["GA (LP)"])

    # ── LP dots — only regions with LP data ──────────────────────────────────
    for r in lp_avail.index:
        lv = float(lp_avail[r])
        yi = y_pos[r]
        lv_plot = min(lv, xlim_right)
        ax.scatter(lv_plot, yi, color=C["LP"], marker=MK["LP"],
                   s=MKSZ["LP"], zorder=5, linewidths=0)
        if lv > xlim_right:
            ax.scatter(xlim_right, yi, marker=">", color=C["LP"],
                       s=18, zorder=6, clip_on=False)

    # ── Baseline reference ────────────────────────────────────────────────────
    ax.axvline(1.0, color="#AAAAAA", lw=0.8, ls="--")
    ax.text(1.002, n - 0.5, "Baseline", ha="left", va="top",
            fontsize=6.5, color="#888888")

    ax.set_yticks(range(n))
    ax.set_yticklabels(
        [r.replace("-", "‑") for r in order], fontsize=6.5
    )
    ax.set_xlabel("Annual system cost  (relative to baseline)")
    ax.set_xlim(xlim_left, xlim_right)
    ax.set_axisbelow(True)
    ax.grid(which="major", axis="x", color="#DDDDDD", lw=0.4, zorder=0)
    ax.xaxis.set_minor_locator(mticker.AutoMinorLocator(2))
    ax.grid(which="minor", axis="x", color="#EEEEEE", lw=0.25, zorder=0)
    ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:.0%}"))

    # Count GA(LP) vs LP comparison (for regions where both exist)
    deltas = [float(galp_avail[r]) - float(lp_avail[r]) for r in both]
    n_below = sum(1 for d in deltas if d < -0.005)
    n_equal = sum(1 for d in deltas if abs(d) <= 0.005)
    n_above = sum(1 for d in deltas if d >  0.005)

    legend_handles = [
        mpl.lines.Line2D([], [], color=C["LP"], marker=MK["LP"], linestyle="",
                         markersize=5, label=f"LP warm-start ({len(lp_avail)} regions)"),
        mpl.lines.Line2D([], [], color=C["GA (LP)"], marker=MK["GA (LP)"],
                         linestyle="", markersize=5,
                         label=f"GA (LP warm-start) ({len(galp_avail)} regions)"),
        mpl.lines.Line2D([], [], color="#009E73", lw=1.4,
                         label=f"GA(LP) < LP  ({n_below})"),
        mpl.lines.Line2D([], [], color="#CC6677", lw=1.4,
                         label=f"GA(LP) > LP  ({n_above})"),
        mpl.lines.Line2D([], [], color="#888888", lw=1.4,
                         label=f"GA(LP) ≈ LP  ({n_equal})"),
    ]
    ax.legend(handles=legend_handles, loc="lower right",
              frameon=False, ncol=1, fontsize=6)

    fig.tight_layout()
    _save(fig, out / "fig10_lp_effectiveness")


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
    p.add_argument(
        "--lp-dir", type=Path,
        default=Path("data/results_python"),
        help="Directory with LP results per region (default: data/results_python)",
    )
    p.add_argument(
        "--lp-regions", nargs="*",
        default=["EUROPE", "UNITED-STATES"],
        help="Regions to show in LP figures (default: EUROPE UNITED-STATES)",
    )
    p.add_argument(
        "--results-dir", type=Path,
        default=Path("data/results_verification"),
        help="Directory with per-region result folders (default: data/results_verification)",
    )
    return p.parse_args()


def main() -> None:
    args   = parse_args()
    df     = load(args.input)                          # feasible-only (all existing figures)
    df_all = load(args.input, feasible_only=False)     # all rows incl. LP-infeasible (fig12)
    hl     = args.highlight if args.highlight is not None else [args.exemplar]

    print(
        f"Loaded {len(df)} rows · {df['region'].nunique()} regions · "
        f"{df['case'].nunique()} cases"
    )

    plot_overview(df, df_all, args.output)
    plot_pathway(df, args.output, highlight=hl)
    # fig3 and fig5 for every lp_region (plus the exemplar if not already covered)
    fig3_regions = list(dict.fromkeys([args.exemplar] + args.lp_regions))
    for r in fig3_regions:
        plot_exemplar(df, r, args.output, countrystats=args.countrystats)
    plot_energy_mix(df, args.output)
    wf_ylim = tuple(args.waterfall_ylim) if args.waterfall_ylim else None
    for r in fig3_regions:
        plot_waterfall(df, r, args.output, ylim=wf_ylim)
    plot_lp_dispatch(args.lp_regions, args.lp_dir, args.output)
    plot_lp_capacity(args.lp_regions, args.lp_dir, args.output)
    for r in args.lp_regions:
        plot_lp_comparison(df, r, args.lp_dir, args.output,
                           countrystats=args.countrystats)
    plot_lp_storage(args.lp_regions, args.lp_dir, args.output)
    plot_lp_effectiveness(df, args.output)
    for r in args.lp_regions:
        plot_lp_feasibility_path(
            [r], args.lp_dir, args.results_dir, args.output,
            df=df, countrystats=args.countrystats,
        )
    plot_capacity_boxplots(
        args.lp_dir, args.results_dir, args.output, df=df_all,
    )
    print("Done.")


if __name__ == "__main__":
    main()

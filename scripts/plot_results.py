#!/usr/bin/env python3
"""
plot_results.py — Publication figures for LoadMatch GA results.

Usage:
    python scripts/plot_results.py [--region REGION]

Figures are saved as .pdf and .png to data/results_verification/<REGION>/.
Missing data files print a warning and skip the affected figure rather than
raising an error.
"""

import argparse
import json
import re
import subprocess
import sys
import traceback
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")   # headless backend — works on HPC nodes without a display
import matplotlib as mpl
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
from matplotlib.patches import Patch
from matplotlib.lines import Line2D

# ── repo root ──────────────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.factor_history_tools import (
    parse_factor_history,
    records_to_dataframe,
    FACTOR_COLUMNS,
    CAPACITY_COLUMNS,
)
from scripts.run_full_workflow import (
    PARAM_REGISTRY,
    FACTOR_KEYS,
    CAPACITY_FACTOR_KEYS,
    DEFAULT_LOCKED,
    parse_baseline_factors,
    load_baseline_start,
    extract_fortran_region_defaults,
)
from src.io.dat_parser import read_dat as _read_dat

# ── matplotlib publication defaults (SKILL.md spec) ───────────────────────────
OKABE_ITO = [
    "#E69F00", "#56B4E9", "#009E73", "#F0E442",
    "#0072B2", "#D55E00", "#CC79A7", "#000000",
]

mpl.rcParams.update({
    "font.family":       "sans-serif",
    "font.sans-serif":   ["Helvetica", "Arial", "DejaVu Sans"],
    "font.size":         8,
    "axes.titlesize":    9,
    "axes.labelsize":    8,
    "xtick.labelsize":   7,
    "ytick.labelsize":   7,
    "legend.fontsize":   7,
    "figure.titlesize":  10,
    "lines.linewidth":   1.5,
    "lines.markersize":  4,
    "axes.linewidth":    0.8,
    "xtick.major.width": 0.8,
    "ytick.major.width": 0.8,
    "axes.spines.top":   False,
    "axes.spines.right": False,
    "axes.grid":         True,
    "grid.linewidth":    0.4,
    "grid.alpha":        0.4,
    "savefig.dpi":       300,
    "figure.dpi":        150,
})


# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════

def _save(fig, stem, save_dir):
    fig.tight_layout()
    for ext in ("pdf", "png"):
        fig.savefig(save_dir / f"{stem}.{ext}", bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved {stem}.pdf / .png")


def _skip(fig_name, reason):
    print(f"  [SKIP] {fig_name}: {reason}")


def _try_fig(label, fn, *args, **kwargs):
    """Run a figure function; print traceback and continue on failure."""
    print(f"\n── {label} ──")
    try:
        fn(*args, **kwargs)
    except Exception:
        print(f"  [ERROR] {label} failed:")
        traceback.print_exc()


# Fortran output parsers (shared by Fig 9 and Fig 13)
_COST_RE  = re.compile(
    r'COST\s+(.+?)\s+\(C/KWH\)\s+LO MN HI\s*=\s*([\d.]+)\s+([\d.]+)\s+([\d.]+)'
)
_ENERGY_RE = re.compile(r'END-ENERGY-GENERATED\(TWH/Y\)\s+([\d.]+)')


def _parse_fortran_costs(text):
    costs  = {m.group(1).strip(): float(m.group(3)) for m in _COST_RE.finditer(text)}
    em     = _ENERGY_RE.search(text)
    energy = float(em.group(1)) if em else None
    return costs, energy


# ══════════════════════════════════════════════════════════════════════════════
# Constants (from powerworld.f / countrystats.dat)
# ══════════════════════════════════════════════════════════════════════════════

BASE_CAPACITIES_USA = {
    "onshore_wind":   798_934.02,
    "offshore_wind":  526_241.51,
    "utility_pv":   1_199_905.98,
    "res_rooftop_pv": 701_942.21,
    "com_rooftop_pv": 701_942.21,
    "csp":              1_119.82,
    "solar_thermal":   18_185.00,
}

FACTOR_TO_BASE = {
    "faconwin":   ("onshore_wind",   "Onshore wind"),
    "facoffwin":  ("offshore_wind",  "Offshore wind"),
    "facutilpv":  ("utility_pv",     "Utility PV"),
    "facrespv":   ("res_rooftop_pv", "Res. PV"),
    "faccompv":   ("com_rooftop_pv", "Com. PV"),
    "cspturbfac": ("csp",            "CSP"),
    "facsht":     ("solar_thermal",  "Solar thermal"),
}

POWER_DENSITY_KM2_PER_MW = {
    "onshore_wind":   0.0505,
    "offshore_wind":  0.139,
    "res_rooftop_pv": 0.00523,
    "com_rooftop_pv": 0.00523,
    "utility_pv":     0.01222,
    "csp":            0.02935,
    "solar_thermal":  0.00143,
}

LAND_TYPE = {
    "onshore_wind":   "spacing",
    "offshore_wind":  "offshore",
    "res_rooftop_pv": "rooftop",
    "com_rooftop_pv": "rooftop",
    "utility_pv":     "footprint",
    "csp":            "footprint",
    "solar_thermal":  "footprint",
}

FIXED_FOOTPRINT_KM2 = {
    "Hydro":             43_536.25,
    "Geothermal (elec)":     21.45,
    "EGS":                  373.63,
    "Geothermal (heat)":     68.14,
}
FIXED_TOTAL_KM2 = sum(FIXED_FOOTPRINT_KM2.values())
US_LAND_AREA_KM2 = 9_147_420.0

CAP_2020_MW = {
    "onshore_wind":  147_979.00,
    "offshore_wind":      41.00,
    "res_pv":         17_077.90,
    "com_pv":         40_628.88,
    "utility_pv":     80_018.23,
    "csp":             1_480.00,
    "geo_elec":        2_674.00,
    "hydro":          86_660.00,
    "wave":                0.00,
    "tidal":               0.00,
    "egs":                 0.00,
}
TOTAL_2020_ALL_MW = 1_189_492.0

FIXED_2050_MW = {
    "geo_elec":     6_520.00,
    "hydro":       86_660.00,
    "wave":         1_655.72,
    "tidal":          350.00,
    "egs":        113_564.08,
}

STACK_ORDER = [
    ("Fossil + other",   ["fossil"],                   "#909090"),
    ("Hydro + water",    ["hydro", "wave", "tidal"],   "#70AD47"),
    ("Geothermal + EGS", ["geo_elec", "egs"],          "#C0504D"),
    ("CSP",              ["csp"],                      "#FF6600"),
    ("Utility PV",       ["utility_pv"],               "#FF9900"),
    ("Rooftop PV",       ["res_pv", "com_pv"],         "#FFD966"),
    ("Offshore wind",    ["offshore_wind"],             "#9DC3E6"),
    ("Onshore wind",     ["onshore_wind"],              "#2E75B6"),
]

COST_GROUPS = {
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

GROUP_COLORS = {
    "Electricity gen.": "tab:blue",
    "Heat gen.":        "tab:cyan",
    "Battery storage":  "tab:orange",
    "H2 electricity":   "tab:purple",
    "Other storage":    "tab:green",
    "H2 production":    "tab:red",
    "T&D":              "tab:gray",
}


# ══════════════════════════════════════════════════════════════════════════════
# Figure 1 — Cost convergence and feasibility rate
# ══════════════════════════════════════════════════════════════════════════════

def fig1_convergence(df_gen, save_dir, df_gen_lp=None, lp_cost=None):
    fig, axes = plt.subplots(2, 1, figsize=(8, 6), sharex=True,
                             gridspec_kw={"height_ratios": [3, 1]})
    ax = axes[0]
    valid = df_gen[df_gen["cum_best_cost"] < float("inf")]
    ax.plot(valid["gen"], valid["cum_best_cost"], color="#1A5276", lw=1.8,
            label="GA (baseline) — best cumulative")
    gen_valid = df_gen[df_gen["gen_best_cost"] < float("inf")]
    ax.scatter(gen_valid["gen"], gen_valid["gen_best_cost"],
               s=18, c="#1A5276", alpha=0.35, zorder=3)
    if df_gen_lp is not None:
        valid_lp = df_gen_lp[df_gen_lp["cum_best_cost"] < float("inf")]
        ax.plot(valid_lp["gen"], valid_lp["cum_best_cost"], color="#2ECC71", lw=1.8,
                ls="--", label="GA (LP) — best cumulative")
        gv_lp = df_gen_lp[df_gen_lp["gen_best_cost"] < float("inf")]
        ax.scatter(gv_lp["gen"], gv_lp["gen_best_cost"],
                   s=18, c="#2ECC71", alpha=0.35, zorder=3)
    ax.set_ylabel(r"Annual system cost (\$B yr$^{-1}$)")
    ax.set_xlim(left=0)
    ax.legend(loc="upper right", fontsize=7)
    ax.set_title("A)", loc="left", fontweight="bold")

    ax2 = axes[1]
    ax2.bar(df_gen["gen"], df_gen["feas_frac"] * 100, color="#1A5276", alpha=0.5, width=0.8,
            label="GA (baseline)")
    if df_gen_lp is not None:
        ax2.bar(df_gen_lp["gen"], df_gen_lp["feas_frac"] * 100, color="#2ECC71",
                alpha=0.5, width=0.8, label="GA (LP)")
        ax2.legend(fontsize=7)
    ax2.set_ylabel("Feasible (%)")
    ax2.set_xlabel("Generation")
    ax2.set_xlim(left=0)
    ax2.set_ylim(0, 105)
    ax2.set_title("B)", loc="left", fontweight="bold")

    _save(fig, "fig1_cost_convergence", save_dir)


# ══════════════════════════════════════════════════════════════════════════════
# Figure 2 — Capacity-factor trajectories
# ══════════════════════════════════════════════════════════════════════════════

def fig2_capacity_factors(df_best, baseline_factors, n_gens, save_dir,
                          df_best_lp=None, lp_factors=None):
    CAP_LABELS = {
        "faconwin":   "Onshore wind",
        "facoffwin":  "Offshore wind",
        "facutilpv":  "Utility PV",
        "facrespv":   "Res. rooftop PV",
        "faccompv":   "Com. rooftop PV",
        "cspturbfac": "CSP",
        "facsht":     "Solar thermal",
    }
    fig, ax = plt.subplots(figsize=(10, 4.5))
    color_cycle = plt.rcParams["axes.prop_cycle"].by_key()["color"]
    for ci, (col, label) in enumerate(CAP_LABELS.items()):
        color = color_cycle[ci % len(color_cycle)]
        ax.plot(df_best["gen"], df_best[col], lw=1.4, color=color, label=f"{label} — GA (bl)")
        if df_best_lp is not None and col in df_best_lp.columns:
            ax.plot(df_best_lp["gen"], df_best_lp[col], lw=1.2, color=color,
                    ls="--", alpha=0.7, label=f"{label} — GA (LP)")
        bval = baseline_factors.get(col.upper())
        if bval is not None:
            ax.axhline(bval, color=color, ls=":", lw=1.0, alpha=0.7)
        if lp_factors and col.upper() in lp_factors:
            ax.axhline(lp_factors[col.upper()], color=color, ls="-.", lw=1.0, alpha=0.7)
    ax.set_xlabel("Generation")
    ax.set_ylabel("Scaling factor (–)")
    handles, labels_l = ax.get_legend_handles_labels()
    # Deduplicate legend to show one entry per tech + style key
    ax.legend(bbox_to_anchor=(1.01, 1), loc="upper left", borderaxespad=0,
              frameon=False, ncol=1, fontsize=7)
    ax.set_ylim(bottom=0)
    ax.set_xlim(left=0)
    note = "Dotted = baseline"
    if lp_factors:
        note += "  |  dash-dot = LP solution"
    if df_best_lp is not None:
        note += "  |  dashed = GA (LP) trajectory"
    ax.annotate(note, xy=(0.01, 0.97), xycoords="axes fraction",
                fontsize=7, va="top", color="gray")
    _save(fig, "fig2_capacity_factors", save_dir)


# ══════════════════════════════════════════════════════════════════════════════
# Figure 3 — Non-capacity parameter trajectories (6 panels)
# ══════════════════════════════════════════════════════════════════════════════

def fig3_parameter_trajectories(df_best, baseline_factors, n_gens, save_dir,
                                df_best_lp=None, lp_factors=None):
    # Only optimised (non-fixed) parameters; fixed-category params are excluded.
    PANEL_GROUPS = {
        "A)  Storage duration (hours)": {
            "storhbat": "Battery", "storhphs": "PHS", "storhcold": "Cold TES",
            "storhhwat": "Hot-water TES", "hcharcsp": "CSP charge",
            "storhhfc": "H$_2$ elec.", "storhhbt": "Heat battery",
        },
        "B)  Storage duration (days)": {
            "storugdys": "UTES seasonal", "dayh2stor": "H$_2$ storage",
            "daybashyd": "Baseload hydro",
        },
        "C)  Power rates (TW)": {
            "batdisch": "Battery disch.", "fcdisch": "H$_2$ FC disch.",
            "fccharg": "Electrolyser", "hbtdisch": "Heat bat. disch.",
        },
        "D)  Ratios and factors": {
            "cspstorgat": "CSP stor. ratio", "ugfac": "UTES rate fac.",
            "hwfac": "HW-STES rate fac.", "hpturbrat": "Hydro turb. ratio",
            "cperform": "Heat pump COP",
        },
        "E)  Demand response": {
            "mxhrdrm": "Max DR shift (h)",
        },
    }
    fig, axes = plt.subplots(3, 2, figsize=(14, 10))
    color_cycle = plt.rcParams["axes.prop_cycle"].by_key()["color"]
    for ax, (panel_title, param_dict) in zip(axes.flatten(), PANEL_GROUPS.items()):
        for ci, (col, label) in enumerate(param_dict.items()):
            color = color_cycle[ci % len(color_cycle)]
            if col in df_best.columns:
                ax.plot(df_best["gen"], df_best[col], lw=1.3, color=color, label=label)
                if df_best_lp is not None and col in df_best_lp.columns:
                    ax.plot(df_best_lp["gen"], df_best_lp[col], lw=1.1, color=color,
                            ls="--", alpha=0.7)
                bval = baseline_factors.get(col.upper())
                if bval is not None:
                    ax.axhline(bval, color=color, ls=":", lw=1.0, alpha=0.7)
                if lp_factors and col.upper() in lp_factors:
                    ax.axhline(lp_factors[col.upper()], color=color, ls="-.", lw=1.0, alpha=0.7)
        ax.set_title(panel_title[:2], loc="left", fontweight="bold")
        ax.set_xlabel("Generation")
        if ax.get_lines():
            ax.legend(bbox_to_anchor=(1.01, 1), loc="upper left", borderaxespad=0,
                      frameon=False, fontsize=7, ncol=1)
        n_gens_plot = n_gens
        if df_best_lp is not None and "gen" in df_best_lp.columns and len(df_best_lp) > 0:
            n_gens_plot = max(n_gens, df_best_lp["gen"].max())
        ax.set_xlim(0, n_gens_plot)
    # Hide unused 6th panel
    axes.flatten()[-1].set_visible(False)
    # Add style legend
    note = "Solid = GA (bl)  |  Dotted = baseline"
    if df_best_lp is not None:
        note += "  |  Dashed = GA (LP)"
    if lp_factors:
        note += "  |  Dash-dot = LP"
    axes.flatten()[-1].set_visible(True)
    axes.flatten()[-1].axis("off")
    axes.flatten()[-1].text(0.05, 0.95, note, transform=axes.flatten()[-1].transAxes,
                             fontsize=8, va="top", color="gray")
    _save(fig, "fig3_parameter_trajectories", save_dir)


# ── Four-case colours (used across all figures) ───────────────────────────────
_CASE_COLORS = {
    "Baseline":  "#5D6D7E",
    "LP":        "#F28C28",
    "GA (bl)":   "#1A5276",
    "GA (LP)":   "#2ECC71",
}
_CASE_ALPHAS = {"Baseline": 0.7, "LP": 0.85, "GA (bl)": 0.9, "GA (LP)": 0.9}


def _fac(factors_or_row, key):
    """Get factor value from a factors dict (uppercase keys) or DataFrame row (lowercase keys)."""
    if factors_or_row is None:
        return PARAM_REGISTRY.get(key.upper(), (0.0,))[0]
    key_up, key_lo = key.upper(), key.lower()
    if hasattr(factors_or_row, "get"):  # dict
        v = factors_or_row.get(key_up, factors_or_row.get(key_lo))
        return float(v) if v is not None else PARAM_REGISTRY.get(key_up, (0.0,))[0]
    # DataFrame row
    if key_lo in factors_or_row.index:
        return float(factors_or_row[key_lo])
    return PARAM_REGISTRY.get(key_up, (0.0,))[0]


# ══════════════════════════════════════════════════════════════════════════════
# Figure 4 — Baseline vs GA-optimised: final parameter comparison
# ══════════════════════════════════════════════════════════════════════════════

def fig4_baseline_vs_ga(df_compare, save_dir, lp_factors=None, best_row_lp=None):
    """Parameter comparison across all available cases (up to 4)."""
    # Sort: unlocked first, then fixed.
    df_unlocked = df_compare[~df_compare["Locked"]].copy()
    df_locked   = df_compare[df_compare["Locked"]].copy()
    df = pd.concat([df_unlocked, df_locked], ignore_index=True)

    # Build per-case ratio columns (all relative to Baseline)
    cases_avail = [("Baseline", None), ("GA (bl)", "GA optimal")]
    if lp_factors:
        cases_avail.insert(1, ("LP", None))
    if best_row_lp is not None:
        cases_avail.append(("GA (LP)", None))

    for cname, col_src in cases_avail:
        if col_src:
            df[f"_norm_{cname}"] = df[col_src] / df["Baseline"].replace(0, np.nan)
        elif cname == "Baseline":
            df["_norm_Baseline"] = 1.0
        elif cname == "LP" and lp_factors:
            df["_norm_LP"] = pd.Series([
                (lp_factors.get(r["Parameter"], r["Baseline"]) / r["Baseline"])
                if abs(r["Baseline"]) > 1e-12 else np.nan
                for _, r in df.iterrows()
            ], index=df.index)
        elif cname == "GA (LP)" and best_row_lp is not None:
            df["_norm_GA (LP)"] = pd.Series([
                (_fac(best_row_lp, r["Parameter"]) / r["Baseline"])
                if abs(r["Baseline"]) > 1e-12 else np.nan
                for _, r in df.iterrows()
            ], index=df.index)

    n_cases = len(cases_avail)
    h_total = 0.75
    h = h_total / n_cases
    offsets = [h_total/2 - h*(i + 0.5) for i in range(n_cases)]

    fig, ax = plt.subplots(figsize=(12, max(8, len(df) * 0.30)))
    y = np.arange(len(df))

    for ci, (cname, _) in enumerate(cases_avail):
        color = _CASE_COLORS.get(cname, "gray")
        alpha_mult = 0.4 if True else 0.9  # locked rows dimmed below
        norm_col = f"_norm_{cname}"
        vals = df[norm_col].fillna(0).values
        for i, (_, r) in enumerate(df.iterrows()):
            alpha = 0.3 if r["Locked"] else _CASE_ALPHAS.get(cname, 0.8)
            ax.barh(i + offsets[ci], vals[i], h * 0.88,
                    color=color if not r["Locked"] else "lightgray",
                    edgecolor="black", lw=0.3, alpha=alpha,
                    label=cname if i == 0 else "_")

    ax.axvline(1.0, color="black", ls="--", lw=0.8, alpha=0.5)
    ax.set_yticks(y)
    ax.set_yticklabels(
        [f"{r['Parameter']}  ({r['Category']})" + ("  [fixed]" if r["Locked"] else "")
         for _, r in df.iterrows()],
        fontsize=7.5)
    ax.set_xlabel("Ratio to baseline value")
    ax.legend(
        handles=[Patch(facecolor=_CASE_COLORS.get(c, "gray"), alpha=_CASE_ALPHAS.get(c, 0.8),
                       label=c)
                 for c, _ in cases_avail]
        + [Patch(facecolor="lightgray", alpha=0.5, label="Fixed (not optimised)")],
        bbox_to_anchor=(1.01, 1), loc="upper left", borderaxespad=0, frameon=False, ncol=1)
    ax.invert_yaxis()
    # Annotate GA (bl) values for unlocked params
    ga_norm_col = "_norm_GA (bl)"
    ga_bl_case_idx = next((ci for ci, (c, _) in enumerate(cases_avail) if c == "GA (bl)"), None)
    if ga_norm_col in df.columns and ga_bl_case_idx is not None:
        for i, (_, r) in enumerate(df.iterrows()):
            if r["Locked"]:
                continue
            v = r[ga_norm_col] if ga_norm_col in r.index else np.nan
            if not (isinstance(v, float) and np.isnan(v)):
                pct = (float(v) - 1.0) * 100
                y_pos = i + offsets[ga_bl_case_idx]
                ax.annotate(f"{r['GA optimal']:.3g}  ({pct:+.0f}%)",
                            xy=(max(float(v), 0) + 0.02, y_pos),
                            fontsize=6.5, va="center", color="dimgray")
    # Annotate GA (LP) values for unlocked params
    ga_lp_norm_col = "_norm_GA (LP)"
    ga_lp_case_idx = next((ci for ci, (c, _) in enumerate(cases_avail) if c == "GA (LP)"), None)
    if ga_lp_norm_col in df.columns and ga_lp_case_idx is not None and best_row_lp is not None:
        for i, (_, r) in enumerate(df.iterrows()):
            if r["Locked"]:
                continue
            v = r[ga_lp_norm_col] if ga_lp_norm_col in r.index else np.nan
            if not (isinstance(v, float) and np.isnan(v)):
                actual = _fac(best_row_lp, r["Parameter"])
                pct = (float(v) - 1.0) * 100
                y_pos = i + offsets[ga_lp_case_idx]
                ax.annotate(f"{actual:.3g}  ({pct:+.0f}%)",
                            xy=(max(float(v), 0) + 0.02, y_pos),
                            fontsize=6.5, va="center", color=_CASE_COLORS["GA (LP)"])
    # Note: GA (LP) is warm-started from the LP solution; similar-looking bars
    # reflect convergence close to (but not identical to) the LP starting point.
    if best_row_lp is not None:
        ax.annotate("GA (LP) warm-started from LP — bars may overlap LP if convergence is close",
                    xy=(0.01, 0.01), xycoords="axes fraction",
                    fontsize=6.5, va="bottom", color="gray", style="italic")
    all_vals = np.concatenate([df[f"_norm_{c}"].fillna(0).values for c, _ in cases_avail])
    finite_pos = all_vals[np.isfinite(all_vals) & (all_vals > 0)]
    if len(finite_pos) > 0:
        # Use 95th-percentile-based clip so extreme outliers (e.g. FACSHT near-zero baseline)
        # don't collapse all other bars to invisible dots.
        xlim_right = max(np.percentile(finite_pos, 95) * 1.6, 2.0)
    else:
        xlim_right = 2.0
    ax.set_xlim(left=0, right=xlim_right)
    # Annotate bars whose true value exceeds the clipped x-axis
    for i, (_, r) in enumerate(df.iterrows()):
        for ci, (cname, _) in enumerate(cases_avail):
            norm_col = f"_norm_{cname}"
            v = r.get(norm_col, np.nan) if norm_col in r.index else np.nan
            if not np.isfinite(v) or v <= xlim_right:
                continue
            color = _CASE_COLORS.get(cname, "gray")
            ax.annotate(f"→ {v:.2g}×",
                        xy=(xlim_right, i + offsets[ci]),
                        ha="left", va="center", fontsize=6.0, color=color,
                        xycoords="data")
    _save(fig, "fig4_baseline_vs_ga", save_dir)


# ══════════════════════════════════════════════════════════════════════════════
# Figure 5 — Cost distribution and capacity evolution
# ══════════════════════════════════════════════════════════════════════════════

def _to_gw(record, factor_col, base_key):
    return record.get(factor_col, 0.0) * BASE_CAPACITIES_USA[base_key] / 1000


def fig5_cost_and_capacity(df_ga, df_gen, df_best, best_row, baseline_factors, n_gens,
                            records, save_dir, df_gen_lp=None, best_row_lp=None,
                            df_ga_lp=None, n_gens_lp=None):
    has_lp = df_gen_lp is not None and df_ga_lp is not None

    STORAGE_TWH = [
        ("Battery",           lambda r: r.get("batdisch", 0) * r.get("storhbat", 0)),
        ("H\u2082 long-term", lambda r: r.get("fcdisch", 0) * r.get("dayh2stor", 0) * 24),
        ("Heat battery",      lambda r: r.get("hbtdisch", 0) * r.get("storhhbt", 0)),
    ]
    # Discharge power (TW → GW for primary axis)
    STORAGE_DISCH_GW = [
        ("Battery disch.",    lambda r: r.get("batdisch", 0) * 1000),
        ("H\u2082 FC disch.", lambda r: r.get("fcdisch",  0) * 1000),
        ("Heat bat. disch.",  lambda r: r.get("hbtdisch", 0) * 1000),
    ]
    disch_colors = [OKABE_ITO[0], OKABE_ITO[2], OKABE_ITO[5]]
    stor_colors = [OKABE_ITO[0], OKABE_ITO[2], OKABE_ITO[5]]
    cmap_b = plt.cm.Set2(np.linspace(0, 1, len(FACTOR_TO_BASE)))

    ncols = 2 if has_lp else 1
    fig, axes = plt.subplots(2, ncols, figsize=(9 * ncols, 12),
                             gridspec_kw={"height_ratios": [1, 1]})
    if ncols == 1:
        axes = axes.reshape(2, 1)

    panel_labels = iter("ABCDEFGH")

    def _plot_trajectory(ax, df_ga_data, df_gen_data, n_gens_data, box_color,
                         line_color, label_prefix, panel_label):
        gen_costs, gen_positions = [], []
        for g in range(1, int(n_gens_data) + 1):
            feas = df_ga_data[(df_ga_data["gen"] == g) & (df_ga_data["feasible"] == True)]["cost_mn_bil_per_year"]
            feas = feas[feas < float("inf")]
            if len(feas) >= 2:
                gen_costs.append(feas.values)
                gen_positions.append(g)
        if gen_costs:
            ax.boxplot(gen_costs, positions=gen_positions, widths=0.6, patch_artist=True,
                       showfliers=False, boxprops=dict(facecolor=box_color, alpha=0.4),
                       medianprops=dict(color=line_color, lw=1.5))
        valid = df_gen_data[df_gen_data["cum_best_cost"] < float("inf")]
        ax.plot(valid["gen"], valid["cum_best_cost"], "-", color=line_color, lw=2,
                label=f"{label_prefix} cumulative best", zorder=5)
        ax.set_xlabel("Generation")
        ax.set_ylabel(r"Annual system cost (\$B yr$^{-1}$)")
        ax.set_title(f"{panel_label})", loc="left", fontweight="bold")
        ax.legend(loc="upper right", fontsize=7)
        ax.set_xlim(0, n_gens_data + 0.5)

    def _plot_capacity(ax, df_ga_data, n_gens_data, best_row_data, records_data,
                       panel_label):
        milestones = [("Baseline", records_data[0])]
        for r in records_data:
            if r.get("label", "").startswith("inflate") and r.get("feasible"):
                milestones.append(("First\nfeasible", r))
                break
        for g in range(10, int(n_gens_data) + 1, 10):
            gdf = df_ga_data[(df_ga_data["gen"] == g) & (df_ga_data["feasible"] == True)]
            if len(gdf):
                row = gdf.loc[gdf["cost_mn_bil_per_year"].idxmin()]
                milestones.append((f"Gen {g}", row.to_dict()))
        milestones.append(("Optimal", best_row_data.to_dict()))

        n_ms = len(milestones)
        x = np.arange(n_ms)
        bar_w = 0.27   # three groups side-by-side
        # Group 1 (left): generation capacity (GW) — primary axis
        bottom_gw = np.zeros(n_ms)
        for j, (fcol, (bkey, tname)) in enumerate(FACTOR_TO_BASE.items()):
            caps = np.array([_to_gw(ms[1], fcol, bkey) for ms in milestones])
            ax.bar(x - bar_w, caps, bar_w, bottom=bottom_gw, label=tname,
                   color=cmap_b[j], edgecolor="white", lw=0.4)
            bottom_gw += caps
        # Group 2 (centre): storage discharge power (TW → GW) — primary axis
        bottom_disch = np.zeros(n_ms)
        for k, (sname, sfn) in enumerate(STORAGE_DISCH_GW):
            vals = np.array([sfn(ms[1]) for ms in milestones])
            ax.bar(x, vals, bar_w, bottom=bottom_disch, label=sname,
                   color=disch_colors[k], edgecolor="white", lw=0.4, hatch="\\\\")
            bottom_disch += vals
        ax.set_ylabel("Capacity (GW)")
        gw_top = max(bottom_gw.max(), bottom_disch.max())

        # Group 3 (right): energy storage (TWh) — right axis
        ax_r = ax.twinx()
        ax_r.spines["right"].set_visible(True)
        bottom_twh = np.zeros(n_ms)
        for k, (sname, sfn) in enumerate(STORAGE_TWH):
            vals = np.array([sfn(ms[1]) for ms in milestones])
            ax_r.bar(x + bar_w, vals, bar_w, bottom=bottom_twh,
                     label=sname, color=stor_colors[k], edgecolor="white", lw=0.4, hatch="//")
            bottom_twh += vals
        ax_r.set_ylabel("Energy storage (TWh)")
        ax_r.set_ylim(0, bottom_twh.max() * 1.15 if bottom_twh.max() > 0 else 1)

        ax.set_xticks(x)
        ax.set_xticklabels([ms[0] for ms in milestones], rotation=30, ha="right")
        ax.set_title(f"{panel_label})", loc="left", fontweight="bold")
        return gw_top

    # ── Top row: trajectories ───────────────────────────────────────────────────
    _plot_trajectory(axes[0, 0], df_ga, df_gen, n_gens,
                     OKABE_ITO[1], "#1A5276", "GA (bl)", next(panel_labels))
    if has_lp:
        _plot_trajectory(axes[0, 1], df_ga_lp, df_gen_lp, n_gens_lp,
                         OKABE_ITO[3], "#2ECC71", "GA (LP)", next(panel_labels))

    # Equalise top-row y-axis limits
    y_tops = [axes[0, c].get_ylim()[1] for c in range(ncols)]
    for c in range(ncols):
        axes[0, c].set_ylim(bottom=0, top=max(y_tops))

    # ── Bottom row: capacity bars ───────────────────────────────────────────────
    gw_max_bl = _plot_capacity(axes[1, 0], df_ga, n_gens, best_row, records,
                               next(panel_labels))
    gw_max = gw_max_bl
    if has_lp and best_row_lp is not None:
        # Need records_lp — approximate by treating first df_ga_lp row as starting record
        # Extract a minimal records list from df_ga_lp
        first_lp = df_ga_lp.iloc[0].to_dict() if len(df_ga_lp) else best_row_lp.to_dict()
        records_lp_approx = [first_lp]
        gw_max_lp = _plot_capacity(axes[1, 1], df_ga_lp, n_gens_lp, best_row_lp,
                                   records_lp_approx, next(panel_labels))
        gw_max = max(gw_max_bl, gw_max_lp)

    # Equalise bottom-row left-axis y-limits
    for c in range(ncols):
        axes[1, c].set_ylim(0, gw_max * 1.15)

    # Shared legend for capacity panels
    gen_handles   = [Patch(facecolor=cmap_b[j], label=tn, edgecolor="white")
                     for j, (_, (_, tn)) in enumerate(FACTOR_TO_BASE.items())]
    disch_handles = [Patch(facecolor=disch_colors[k], label=sn, edgecolor="white", hatch="\\\\")
                     for k, (sn, _) in enumerate(STORAGE_DISCH_GW)]
    stor_handles  = [Patch(facecolor=stor_colors[k], label=sn, edgecolor="white", hatch="//")
                     for k, (sn, _) in enumerate(STORAGE_TWH)]
    axes[1, 0].legend(handles=gen_handles + disch_handles + stor_handles,
                      bbox_to_anchor=(0.0, -0.30), loc="upper left", borderaxespad=0,
                      frameon=False, ncol=3, fontsize=7)

    _save(fig, "fig5_cost_and_capacity", save_dir)


# ══════════════════════════════════════════════════════════════════════════════
# Figure 6 — Parameter sensitivity: CV across feasible GA population
# ══════════════════════════════════════════════════════════════════════════════

def fig6_parameter_cv(df_ga, n_gens, save_dir, df_ga_lp=None, n_gens_lp=None):
    def _cv_df(df_pop):
        cols = [c for c in FACTOR_COLUMNS if c in df_pop.columns and c.upper() not in DEFAULT_LOCKED]
        rows = []
        for col in cols:
            vals = df_pop[col].dropna()
            mean_ = vals.mean()
            std_  = vals.std()
            cv = std_ / abs(mean_) * 100 if abs(mean_) > 1e-12 else 0.0
            rows.append({"Parameter": col.upper(), "CV (%)": cv})
        return pd.DataFrame(rows).set_index("Parameter")

    last_gen = df_ga[(df_ga["gen"] == int(n_gens)) & (df_ga["feasible"] == True)]
    df_cv_bl = _cv_df(last_gen).rename(columns={"CV (%)": "GA (bl)"})

    has_lp = df_ga_lp is not None and n_gens_lp is not None
    if has_lp:
        last_gen_lp = df_ga_lp[(df_ga_lp["gen"] == int(n_gens_lp)) & (df_ga_lp["feasible"] == True)]
        df_cv_lp = _cv_df(last_gen_lp).rename(columns={"CV (%)": "GA (LP)"})
        df_cv = df_cv_bl.join(df_cv_lp, how="outer").fillna(0)
        df_cv = df_cv.sort_values("GA (bl)", ascending=True)
    else:
        df_cv = df_cv_bl.sort_values("GA (bl)", ascending=True)

    ncols = 2 if has_lp else 1
    fig, axes = plt.subplots(1, ncols, figsize=(7 * ncols, max(8, len(df_cv) * 0.28)),
                              sharey=True)
    if ncols == 1:
        axes = [axes]

    _panel_letters = "ABCDEFGH"
    for ax_i, (cname, col_key) in enumerate([("GA (bl)", "GA (bl)"), ("GA (LP)", "GA (LP)")] if has_lp else [("GA (bl)", "GA (bl)")]):
        ax = axes[ax_i]
        vals = df_cv[col_key].values
        colors = ["tab:red" if v > 20 else "tab:orange" if v > 5 else "tab:green" for v in vals]
        ax.barh(range(len(df_cv)), vals, color=colors, edgecolor="black", lw=0.3)
        ax.set_yticks(range(len(df_cv)))
        ax.set_yticklabels(df_cv.index.tolist(), fontsize=8)
        ax.set_xlabel("Coefficient of variation (%)")
        ax.invert_yaxis()
        ax.set_title(f"{_panel_letters[ax_i]})", loc="left", fontweight="bold")
        ax.legend(handles=[
            Patch(facecolor="tab:green", label="CV < 5%"),
            Patch(facecolor="tab:orange", label="5–20%"),
            Patch(facecolor="tab:red", label="> 20%"),
        ], loc="upper right", fontsize=7)

    _save(fig, "fig6_parameter_sensitivity", save_dir)


# ══════════════════════════════════════════════════════════════════════════════
# Figure 7 — GA-optimal vs baseline: generation and storage capacities
# ══════════════════════════════════════════════════════════════════════════════

def fig7_capacity_comparison(best_row, baseline_factors, save_dir,
                             lp_factors=None, best_row_lp=None):
    # Build all available cases
    case_specs = [("Baseline", baseline_factors), ("GA (bl)", best_row)]
    if lp_factors:
        case_specs.insert(1, ("LP", lp_factors))
    if best_row_lp is not None:
        case_specs.append(("GA (LP)", best_row_lp))
    n_cases = len(case_specs)

    gen_items = [
        ("Onshore wind",  "faconwin",   "onshore_wind"),
        ("Offshore wind", "facoffwin",  "offshore_wind"),
        ("Utility PV",    "facutilpv",  "utility_pv"),
        ("Res. PV",       "facrespv",   "res_rooftop_pv"),
        ("Com. PV",       "faccompv",   "com_rooftop_pv"),
        ("CSP",           "cspturbfac", "csp"),
        ("Solar thermal", "facsht",     "solar_thermal"),
    ]
    gen_labels = [it[0] for it in gen_items]
    stor_pow_items = [
        ("Battery",      "batdisch"),
        ("H\u2082 FC",   "fcdisch"),
        ("Electrolyser", "fccharg"),
        ("Heat battery", "hbtdisch"),
        ("PHS (min.)",   "phsmin"),
    ]
    sp_labels = [it[0] for it in stor_pow_items]
    stor_ene_items = [
        ("Battery",           "batdisch", "storhbat",   1),
        ("H\u2082 long-term", "fcdisch",  "dayh2stor", 24),
        ("Heat battery",      "hbtdisch", "storhhbt",   1),
    ]
    se_labels = [it[0] for it in stor_ene_items]

    fig, (ax1, ax2, ax3) = plt.subplots(1, 3, figsize=(16, 6))
    w_total = 0.72
    w = w_total / n_cases
    offsets = [w_total/2 - w*(i + 0.5) for i in range(n_cases)]

    def _grouped_bars(ax, labels, case_vals_list, ylabel, title):
        x = np.arange(len(labels))
        for ci, ((cname, _), vals) in enumerate(zip(case_specs, case_vals_list)):
            color = _CASE_COLORS.get(cname, "gray")
            alpha = _CASE_ALPHAS.get(cname, 0.8)
            ax.bar(x + offsets[ci], vals, w * 0.9, label=cname if ax == ax1 else "_",
                   color=color, alpha=alpha, edgecolor="black", lw=0.4)
        ax.set_xticks(x)
        ax.set_xticklabels(labels, rotation=35, ha="right", fontsize=8)
        ax.set_ylabel(ylabel)
        ax.set_title(title, loc="left", fontweight="bold")
        ax.set_ylim(bottom=0)

    gen_vals = [[_fac(fac, it[1]) * BASE_CAPACITIES_USA[it[2]] / 1000 for it in gen_items]
                for _, fac in case_specs]
    sp_vals  = [[_fac(fac, it[1]) for it in stor_pow_items] for _, fac in case_specs]
    se_vals  = [[_fac(fac, it[1]) * _fac(fac, it[2]) * it[3] for it in stor_ene_items]
                for _, fac in case_specs]

    _grouped_bars(ax1, gen_labels, gen_vals, "Capacity (GW)", "A)")
    _grouped_bars(ax2, sp_labels,  sp_vals,  "Power capacity (TW)", "B)")
    _grouped_bars(ax3, se_labels,  se_vals,  "Energy capacity (TWh)", "C)")

    ax3_r = ax3.twinx()
    x_hfc = len(se_labels)
    for ci, (cname, fac) in enumerate(case_specs):
        color = _CASE_COLORS.get(cname, "gray")
        alpha = _CASE_ALPHAS.get(cname, 0.8)
        ax3_r.bar(x_hfc + offsets[ci], _fac(fac, "storhhfc"), w * 0.9,
                  color=color, alpha=alpha * 0.6, edgecolor="black", lw=0.4, hatch="//")
    ax3_r.set_ylabel("H\u2082 FC storage duration (h)", color="gray")
    ax3_r.tick_params(axis="y", labelcolor="gray")
    ax3_r.set_ylim(bottom=0)
    ax3.set_xticks(list(range(len(se_labels) + 1)))
    ax3.set_xticklabels(se_labels + ["H\u2082 FC (h)"], rotation=35, ha="right", fontsize=8)
    ax3.set_xlim(-0.6, x_hfc + 0.6)

    leg_handles = [Patch(facecolor=_CASE_COLORS.get(c, "gray"), alpha=_CASE_ALPHAS.get(c, 0.8),
                         edgecolor="black", lw=0.4, label=c) for c, _ in case_specs]
    ax1.legend(handles=leg_handles, loc="upper right", frameon=True, fontsize=7)

    if best_row_lp is not None:
        fig.text(0.5, 0.01,
                 "GA (LP) is warm-started from the LP solution — bars may closely overlap LP "
                 "if the GA converged near its starting point.",
                 ha="center", va="bottom", fontsize=7, color="gray", style="italic")

    _save(fig, "fig7_capacity_comparison", save_dir)


# ══════════════════════════════════════════════════════════════════════════════
# Figure 8 — Land area demand
# ══════════════════════════════════════════════════════════════════════════════

def fig8_land_area(best_row, baseline_factors, save_dir, region="UNITED-STATES",
                   lp_factors=None, best_row_lp=None):
    region_label = region.replace("-", " ").title()

    case_specs = [("Baseline", baseline_factors), ("GA (bl)", best_row)]
    if lp_factors:
        case_specs.insert(1, ("LP", lp_factors))
    if best_row_lp is not None:
        case_specs.append(("GA (LP)", best_row_lp))
    n_cases = len(case_specs)

    area_items = [
        ("Onshore wind",  "faconwin",   "onshore_wind"),
        ("Offshore wind", "facoffwin",  "offshore_wind"),
        ("Res. PV",       "facrespv",   "res_rooftop_pv"),
        ("Com. PV",       "faccompv",   "com_rooftop_pv"),
        ("Utility PV",    "facutilpv",  "utility_pv"),
        ("CSP",           "cspturbfac", "csp"),
        ("Solar thermal", "facsht",     "solar_thermal"),
    ]

    def _area_km2(fac_val, base_key):
        return fac_val * BASE_CAPACITIES_USA[base_key] * POWER_DENSITY_KM2_PER_MW[base_key]

    labels = [it[0] for it in area_items]
    ltype  = [LAND_TYPE[it[2]] for it in area_items]
    LAND_TYPE_COLORS = {
        "spacing": "tab:blue", "offshore": "tab:cyan",
        "rooftop": "tab:green", "footprint": "tab:orange",
    }

    # Per-case areas: list of lists
    all_areas = [
        [_area_km2(_fac(fac, it[1]), it[2]) for it in area_items]
        for _, fac in case_specs
    ]
    # Totals for panel B annotation
    all_totals = [
        sum(a for a, lt in zip(areas, ltype) if lt == "footprint") + FIXED_TOTAL_KM2
        for areas in all_areas
    ]

    fig, (ax_a, ax_b) = plt.subplots(1, 2, figsize=(14, 6))
    w_total = 0.72
    w = w_total / n_cases
    offsets = [w_total/2 - w*(i + 0.5) for i in range(n_cases)]
    x = np.arange(len(labels))

    # A) Per-technology bars, color = land type, shading = case
    seen_lt = set()
    for ci, ((cname, _), case_areas) in enumerate(zip(case_specs, all_areas)):
        alpha = 0.35 + 0.15 * ci  # progressively darker
        alpha = min(alpha, 0.95)
        for xi, (lbl, area, lt) in enumerate(zip(labels, case_areas, ltype)):
            color = LAND_TYPE_COLORS[lt]
            ax_a.bar(xi + offsets[ci], area / US_LAND_AREA_KM2 * 100, w * 0.9,
                     color=color, alpha=alpha, edgecolor="black", lw=0.4,
                     label=lt.capitalize() if (lt not in seen_lt and ci == 0) else "_")
            seen_lt.add(lt)
    ax_a.set_xticks(x)
    ax_a.set_xticklabels(labels, rotation=30, ha="right", fontsize=9)
    ax_a.set_ylabel(f"Land area (% of {region_label} land)")
    ax_a.set_title("A)", loc="left", fontweight="bold")
    lt_handles = ax_a.get_legend_handles_labels()[0]
    case_handles = [Patch(facecolor="gray", alpha=0.35 + 0.15*i,
                          edgecolor="black", lw=0.4, label=c)
                    for i, (c, _) in enumerate(case_specs)]
    ax_a.legend(handles=lt_handles + case_handles,
                bbox_to_anchor=(1.01, 1), loc="upper left", borderaxespad=0,
                frameon=False, fontsize=8, ncol=1)

    # B) Stacked footprint totals
    FOOTPRINT_STACKS = [
        ("Fixed infra",   "#A0A0A0", lambda f: FIXED_TOTAL_KM2),
        ("Utility PV",    "#FF9900", lambda f: _area_km2(_fac(f, "facutilpv"), "utility_pv")),
        ("CSP",           "#FF6600", lambda f: _area_km2(_fac(f, "cspturbfac"), "csp")),
        ("Solar thermal", "#FFC000", lambda f: _area_km2(_fac(f, "facsht"), "solar_thermal")),
    ]
    bots = [0.0] * n_cases
    for sname, sc, sfn in FOOTPRINT_STACKS:
        vals = [sfn(fac) for _, fac in case_specs]
        for ci, v in enumerate(vals):
            ax_b.bar(ci, v / US_LAND_AREA_KM2 * 100, 0.55,
                     bottom=bots[ci] / US_LAND_AREA_KM2 * 100,
                     color=sc, alpha=0.85, edgecolor="black", lw=0.5,
                     label=sname if ci == 0 else "_")
            bots[ci] += v
    ax_b.set_xticks(range(n_cases))
    ax_b.set_xticklabels([c for c, _ in case_specs], fontsize=9)
    ax_b.set_ylabel(f"Land footprint (% of {region_label} land)")
    ax_b.set_title("B)", loc="left", fontweight="bold")
    for ci, total in enumerate(all_totals):
        ax_b.annotate(f"{total/US_LAND_AREA_KM2*100:.2f}%\n({total:,.0f} km²)",
                      xy=(ci, total/US_LAND_AREA_KM2*100), xytext=(0, 6),
                      textcoords="offset points", ha="center", va="bottom", fontsize=8)
    ax_b.legend(bbox_to_anchor=(1.01, 1), loc="upper left", borderaxespad=0,
                frameon=False, fontsize=8, ncol=1)

    _save(fig, "fig8_area_comparison", save_dir)


# ══════════════════════════════════════════════════════════════════════════════
# Figure 9 — Cost breakdown: GA-optimised vs baseline
# ══════════════════════════════════════════════════════════════════════════════

def fig9_cost_breakdown(bl_costs, opt_costs, bl_energy, opt_energy, best_row, save_dir,
                        lp_costs=None, lp_energy=None, lp_ga_costs=None, lp_ga_energy=None):
    if not opt_costs or not bl_costs:
        _skip("Fig 9", "cost data not available (need both Fortran output files)")
        return

    def _group_bil(costs, energy_twh):
        if not costs or energy_twh is None:
            return None
        return {grp: sum(costs.get(lbl, 0.0) for lbl in lbls) * energy_twh / 100
                for grp, lbls in COST_GROUPS.items()}

    groups = list(COST_GROUPS.keys())
    colors = [GROUP_COLORS[g] for g in groups]

    # Build cases: only include those with real data
    raw_cases = [
        ("Baseline",  _group_bil(bl_costs,    bl_energy)),
        ("LP",        _group_bil(lp_costs,    lp_energy)),
        ("GA (bl)",   _group_bil(opt_costs,   opt_energy)),
        ("GA (LP)",   _group_bil(lp_ga_costs, lp_ga_energy)),
    ]
    case_specs = [(cname, grp) for cname, grp in raw_cases if grp is not None]
    n_cases = len(case_specs)

    x = np.arange(len(groups))
    w_total = 0.72
    w = w_total / n_cases
    offsets = [w_total/2 - w*(i + 0.5) for i in range(n_cases)]

    fig, (ax_a, ax_b) = plt.subplots(1, 2, figsize=(14, 6))

    # Case hatches: each case gets a distinct pattern so group colour + hatch = unambiguous
    _CASE_HATCHES = {"Baseline": "", "LP": "//", "GA (bl)": "xx", "GA (LP)": ".."}

    bl_grp = dict(raw_cases)[case_specs[0][0]]
    for ci, (cname, grp) in enumerate(case_specs):
        color_case = _CASE_COLORS.get(cname, "gray")
        alpha = _CASE_ALPHAS.get(cname, 0.8)
        hatch = _CASE_HATCHES.get(cname, "")
        for i, (g, c) in enumerate(zip(groups, colors)):
            gv = grp[g]
            bv = bl_grp[g]
            ax_a.bar(i + offsets[ci], gv, w * 0.9, color=c, alpha=alpha,
                     edgecolor=color_case, lw=0.8, hatch=hatch)
            if ci > 0 and bv > 1e-3:
                pct = 100 * (gv - bv) / bv
                ax_a.annotate(f"{'+' if pct>=0 else ''}{pct:.0f}%",
                              xy=(i + offsets[ci], gv), xytext=(0, 2),
                              textcoords="offset points", ha="center", va="bottom",
                              fontsize=5.5, color=color_case)

    ax_a.set_xticks(x)
    ax_a.set_xticklabels(groups, rotation=30, ha="right", fontsize=8)
    ax_a.set_ylabel(r"Annual cost (\$BIL yr$^{-1}$)")
    ax_a.set_title("A)", loc="left", fontweight="bold")
    ax_a.set_ylim(bottom=0)
    # Legend: group colours (filled, no hatch) + case indicators (gray fill + hatch)
    group_handles = [Patch(facecolor=GROUP_COLORS[g], label=g, edgecolor="none") for g in groups]
    case_handles  = [Patch(facecolor="white", edgecolor=_CASE_COLORS.get(c, "gray"), lw=1.2,
                           hatch=_CASE_HATCHES.get(c, ""), label=c)
                     for c, _ in case_specs]
    ax_a.legend(handles=group_handles + case_handles,
                bbox_to_anchor=(1.01, 1), loc="upper left", borderaxespad=0,
                frameon=False, fontsize=8, ncol=1)

    # Panel B — stacked totals per case
    bots = np.zeros(n_cases)
    for g, c in zip(groups, colors):
        vals = np.array([grp[g] for _, grp in case_specs])
        ax_b.bar(range(n_cases), vals, 0.55, bottom=bots, color=c, alpha=0.85,
                 edgecolor="black", lw=0.5, label=g)
        bots += vals
    for ci, ((cname, _), tot) in enumerate(zip(case_specs, bots)):
        ax_b.annotate(f"${tot:.1f}B/yr", xy=(ci, tot), xytext=(0, 6),
                      textcoords="offset points", ha="center", va="bottom",
                      fontsize=9, fontweight="bold", color=_CASE_COLORS.get(cname, "black"))
    ax_b.set_ylim(0, bots.max() * 1.18)
    ax_b.set_xticks(range(n_cases))
    ax_b.set_xticklabels([c for c, _ in case_specs], fontsize=9)
    ax_b.set_ylabel(r"Annual system cost (\$BIL yr$^{-1}$)")
    ax_b.set_title("B)", loc="left", fontweight="bold")
    ax_b.legend(bbox_to_anchor=(1.01, 1), loc="upper left", borderaxespad=0,
                frameon=False, fontsize=8, ncol=1)

    _save(fig, "fig9_cost_comparison", save_dir)


# ══════════════════════════════════════════════════════════════════════════════
# Figure 10 — Generation capacity mix and ternary diagram
# ══════════════════════════════════════════════════════════════════════════════

def fig10_capacity_mix(best_row, baseline_factors, save_dir, region="UNITED-STATES",
                       lp_factors=None, best_row_lp=None):
    is_us = region == "UNITED-STATES"

    def _build_cap_2050(fac_src):
        return {
            "onshore_wind":  _fac(fac_src, "faconwin")   * BASE_CAPACITIES_USA["onshore_wind"],
            "offshore_wind": _fac(fac_src, "facoffwin")  * BASE_CAPACITIES_USA["offshore_wind"],
            "res_pv":        _fac(fac_src, "facrespv")   * BASE_CAPACITIES_USA["res_rooftop_pv"],
            "com_pv":        _fac(fac_src, "faccompv")   * BASE_CAPACITIES_USA["com_rooftop_pv"],
            "utility_pv":    _fac(fac_src, "facutilpv")  * BASE_CAPACITIES_USA["utility_pv"],
            "csp":           _fac(fac_src, "cspturbfac") * BASE_CAPACITIES_USA["csp"],
            **FIXED_2050_MW,
        }

    bl_cap_2050 = _build_cap_2050(baseline_factors)
    ga_cap_2050 = _build_cap_2050(best_row)
    lp_cap_2050 = _build_cap_2050(lp_factors) if lp_factors else None
    lp_ga_cap_2050 = _build_cap_2050(best_row_lp) if best_row_lp is not None else None
    tot_bl_2050 = sum(bl_cap_2050.values())
    tot_ga_2050 = sum(ga_cap_2050.values())

    _S3 = np.sqrt(3)

    def _t2c(wind, solar, water):
        tot = wind + solar + water
        w, s = wind / tot, solar / tot
        return s + w * 0.5, w * _S3 / 2

    fig, (ax_a, ax_b, ax_c) = plt.subplots(1, 3, figsize=(18, 6),
                                            gridspec_kw={"width_ratios": [1, 1, 1.3]})

    # A) Absolute capacity stacked bars — include all available cases
    if is_us:
        FOSSIL_2020_MW = TOTAL_2020_ALL_MW - sum(CAP_2020_MW.values())
        scenarios_bar = [
            ("2020\n(incl. fossil)", TOTAL_2020_ALL_MW, {**CAP_2020_MW, "fossil": FOSSIL_2020_MW}),
            ("Baseline\n2050",       tot_bl_2050,        bl_cap_2050),
        ]
    else:
        scenarios_bar = [("Baseline\n2050", tot_bl_2050, bl_cap_2050)]
    if lp_cap_2050:
        scenarios_bar.append(("LP\n2050",    sum(lp_cap_2050.values()),    lp_cap_2050))
    scenarios_bar.append(("GA (bl)\n2050", tot_ga_2050, ga_cap_2050))
    if lp_ga_cap_2050:
        scenarios_bar.append(("GA (LP)\n2050", sum(lp_ga_cap_2050.values()), lp_ga_cap_2050))
    for xi, (_, total, cap) in enumerate(scenarios_bar):
        bot = 0.0
        for _, keys, color in STACK_ORDER:
            val = sum(cap.get(k, 0) for k in keys) / 1e3
            if val > 0.01:
                ax_a.bar(xi, val, 0.55, bottom=bot, color=color, edgecolor="white", lw=0.4)
            bot += val
        ax_a.text(xi, bot + 30, f"{total/1e3:,.0f} GW",
                  ha="center", va="bottom", fontsize=8, color="dimgray")
    ax_a.set_xticks(np.arange(len(scenarios_bar)))
    ax_a.set_xticklabels([s[0] for s in scenarios_bar], fontsize=10)
    ax_a.set_ylabel("Installed nameplate capacity (GW)")
    if not is_us:
        ax_a.annotate("Note: GW values use US reference base capacities\n"
                      "(region-specific 2020 data not available)",
                      xy=(0.5, 0.01), xycoords="axes fraction",
                      ha="center", va="bottom", fontsize=7, color="gray", style="italic")
    ax_a.set_title("A)", loc="left", fontweight="bold")
    ax_a.legend(
        handles=[Patch(facecolor=c, label=g, edgecolor="white", lw=0.4)
                 for g, _, c in reversed(STACK_ORDER)],
        fontsize=8, bbox_to_anchor=(1.01, 1), loc="upper left",
        borderaxespad=0, frameon=False, ncol=1)

    # B) Wind% vs Solar% scatter with iso-lines
    def _wind_solar_shares(cap, total):
        wind  = (cap.get("onshore_wind", 0) + cap.get("offshore_wind", 0)) / total * 100
        solar = (cap.get("res_pv", 0) + cap.get("com_pv", 0) +
                 cap.get("utility_pv", 0) + cap.get("csp", 0)) / total * 100
        return wind, solar

    pts_scatter = []
    if is_us:
        pts_scatter.append(("2020", CAP_2020_MW, TOTAL_2020_ALL_MW, "o", "black", 80))
    pts_scatter.append(("Baseline 2050", bl_cap_2050, tot_bl_2050, "s", _CASE_COLORS["Baseline"], 90))
    if lp_cap_2050:
        pts_scatter.append(("LP 2050", lp_cap_2050, sum(lp_cap_2050.values()), "D", _CASE_COLORS["LP"], 80))
    pts_scatter.append(("GA (bl) 2050", ga_cap_2050, tot_ga_2050, "^", _CASE_COLORS["GA (bl)"], 90))
    if lp_ga_cap_2050:
        pts_scatter.append(("GA (LP) 2050", lp_ga_cap_2050, sum(lp_ga_cap_2050.values()), "v", _CASE_COLORS["GA (LP)"], 90))
    solar_vals = [_wind_solar_shares(c, t)[1] for _, c, t, *_ in pts_scatter]
    wind_vals  = [_wind_solar_shares(c, t)[0] for _, c, t, *_ in pts_scatter]
    x_max = max(solar_vals) * 1.12
    y_max = max(wind_vals)  * 1.15
    sol_arr = np.linspace(0, x_max, 200)

    for C in range(10, 110, 10):
        wind_line = C - sol_arr
        mask = (wind_line >= 0) & (wind_line <= y_max) & (sol_arr >= 0)
        if mask.sum() > 1:
            ax_b.plot(sol_arr[mask], wind_line[mask], color="lightgray", lw=0.8, zorder=1)
            if C >= 50:
                ax_b.text(sol_arr[mask][0] + 4, wind_line[mask][0] * 0.99, f"{C}%",
                          ha="right", va="center", fontsize=7, color="gray")
    ax_b.text(2, y_max * 0.97, "Wind+Solar\nshare of total",
              ha="left", va="top", fontsize=7, color="gray", style="italic")

    # Compute data coords and spread annotations to avoid overlap
    _pts_data = [(label, cap, total, marker, color, ms,
                  *_wind_solar_shares(cap, total))   # solar, wind
                 for label, cap, total, marker, color, ms in pts_scatter]

    def _spread_annots(points_xy, min_sep=2.5, iters=80):
        """Iteratively repel annotation positions (in data units) until no overlap."""
        pos = [list(p) for p in points_xy]
        for _ in range(iters):
            moved = False
            for i in range(len(pos)):
                for j in range(i + 1, len(pos)):
                    dx_ = pos[i][0] - pos[j][0]
                    dy_ = pos[i][1] - pos[j][1]
                    dist = (dx_**2 + dy_**2) ** 0.5
                    if dist < min_sep and dist > 1e-9:
                        push = (min_sep - dist) / 2
                        nx, ny = dx_ / dist * push, dy_ / dist * push
                        pos[i][0] += nx; pos[i][1] += ny
                        pos[j][0] -= nx; pos[j][1] -= ny
                        moved = True
            if not moved:
                break
        return pos

    raw_xy  = [(s_, w_) for *_, s_, w_ in _pts_data]
    # Offset seeds: alternate above/below to give the spread algo a head start
    seed_off = [(3, 5), (-3, -5), (3, 5), (-3, -5), (3, 5)]
    seed_pos = [(s + seed_off[i % len(seed_off)][0],
                 w + seed_off[i % len(seed_off)][1])
                for i, (s, w) in enumerate(raw_xy)]
    spread   = _spread_annots(seed_pos, min_sep=max(x_max, y_max) * 0.18)

    for i, (label, cap, total, marker, color, ms, s_, w_) in enumerate(_pts_data):
        ax_b.scatter(s_, w_, marker=marker, color=color, s=ms,
                     edgecolors="black", lw=0.8, zorder=5, label=label)
        ax_b.annotate(
            f"{label}\nSolar {s_:.1f}%  Wind {w_:.1f}%\nTotal {s_+w_:.1f}%",
            xy=(s_, w_), xytext=spread[i], textcoords="data", fontsize=7,
            bbox=dict(boxstyle="round,pad=0.25", fc="white", alpha=0.85, lw=0.4,
                      edgecolor=color),
            arrowprops=dict(arrowstyle="-", color=color, lw=0.8),
        )
    ax_b.set_xlabel("Solar share of total installed capacity (%)")
    ax_b.set_ylabel("Wind share of total installed capacity (%)")
    ax_b.set_xlim(0, x_max * 1.05)
    ax_b.set_ylim(0, y_max * 1.05)
    ax_b.set_title("B)", loc="left", fontweight="bold")
    ax_b.legend(fontsize=8, loc="lower right")

    # C) Ternary (2050 scenarios only)
    tri = np.array([[0, 0], [1, 0], [0.5, _S3 / 2], [0, 0]])
    ax_c.plot(tri[:, 0], tri[:, 1], "k-", lw=1.5, zorder=4)
    for f in np.arange(0.2, 1.0, 0.2):
        ax_c.plot([*_t2c(f, 1-f, 0)[:1], *_t2c(f, 0, 1-f)[:1]],
                  [*_t2c(f, 1-f, 0)[1:], *_t2c(f, 0, 1-f)[1:]],
                  color="gray", lw=0.4, alpha=0.6, zorder=1)
        ax_c.plot([*_t2c(1-f, f, 0)[:1], *_t2c(0, f, 1-f)[:1]],
                  [*_t2c(1-f, f, 0)[1:], *_t2c(0, f, 1-f)[1:]],
                  color="gray", lw=0.4, alpha=0.6, zorder=1)
        ax_c.plot([*_t2c(1-f, 0, f)[:1], *_t2c(0, 1-f, f)[:1]],
                  [*_t2c(1-f, 0, f)[1:], *_t2c(0, 1-f, f)[1:]],
                  color="gray", lw=0.4, alpha=0.6, zorder=1)
        # Wind ticks (left edge)
        x, y = _t2c(f, 0, 1-f)
        ax_c.text(x - 0.03, y, f"{int(f*100)}%", ha="right", va="center",
                  fontsize=7, color="#2E75B6")
        # Solar ticks (right edge)
        xs, ys = _t2c(1-f, f, 0)
        ax_c.text(xs + 0.03, ys, f"{int(f*100)}%", ha="left", va="center",
                  fontsize=7, color="#FF9900")
        # Water ticks (below bottom edge)
        xw = 1 - f
        ax_c.text(xw, -0.04, f"{int(f*100)}%", ha="center", va="top",
                  fontsize=7, color="#70AD47")

    ax_c.text(0.5,  _S3/2 + 0.07, "Wind",  ha="center", va="bottom",
              fontsize=12, fontweight="bold", color="#2E75B6")
    ax_c.text(-0.06, -0.02,        "Water", ha="right",  va="top",
              fontsize=12, fontweight="bold", color="#70AD47")
    ax_c.text(1.06,  -0.02,        "Solar", ha="left",   va="top",
              fontsize=12, fontweight="bold", color="#FF9900")

    ternary_pts = [("Baseline 2050", bl_cap_2050, "s", _CASE_COLORS["Baseline"], 90)]
    if lp_cap_2050:
        ternary_pts.append(("LP 2050", lp_cap_2050, "D", _CASE_COLORS["LP"], 80))
    ternary_pts.append(("GA (bl) 2050", ga_cap_2050, "^", _CASE_COLORS["GA (bl)"], 90))
    if lp_ga_cap_2050:
        ternary_pts.append(("GA (LP) 2050", lp_ga_cap_2050, "v", _CASE_COLORS["GA (LP)"], 90))

    # Compute ternary coords for each point
    tern_data = []
    for label, cap, marker, color, ms in ternary_pts:
        wind_  = cap.get("onshore_wind", 0) + cap.get("offshore_wind", 0)
        solar_ = (cap.get("res_pv", 0) + cap.get("com_pv", 0) +
                  cap.get("utility_pv", 0) + cap.get("csp", 0))
        water_ = cap.get("hydro", 0) + cap.get("wave", 0) + cap.get("tidal", 0)
        tot_   = wind_ + solar_ + water_
        xp, yp = _t2c(wind_, solar_, water_)
        tern_data.append((label, cap, marker, color, ms, xp, yp, wind_, solar_, water_, tot_))

    # Spread annotation positions in ternary coord space
    raw_txy = [(xp, yp) for *_, xp, yp, _w, _s, _wa, _t in tern_data]
    t_seed_off = [(0.06, 0.06), (-0.06, -0.06), (0.06, 0.06), (-0.06, -0.06)]
    t_seed = [(xp + t_seed_off[i % 4][0], yp + t_seed_off[i % 4][1])
              for i, (xp, yp) in enumerate(raw_txy)]
    t_spread = _spread_annots(t_seed, min_sep=0.18)

    for i, (label, cap, marker, color, ms, xp, yp, wind_, solar_, water_, tot_) in enumerate(tern_data):
        ax_c.scatter(xp, yp, marker=marker, color=color, s=ms,
                     edgecolors="black", lw=0.8, zorder=5, label=label)
        ax_c.annotate(
            f"{label}\nW {wind_/tot_*100:.1f}%  S {solar_/tot_*100:.1f}%  "
            f"Wat {water_/tot_*100:.1f}%",
            xy=(xp, yp), xytext=t_spread[i], textcoords="data", fontsize=7,
            bbox=dict(boxstyle="round,pad=0.25", fc="white", alpha=0.85, lw=0.4,
                      edgecolor=color),
            arrowprops=dict(arrowstyle="-", color=color, lw=0.8),
        )

    ax_c.set_xlim(-0.10, 1.10)
    ax_c.set_ylim(-0.16, _S3/2 + 0.14)
    ax_c.axis("off")
    ax_c.set_title("C)", loc="left", fontweight="bold")
    ax_c.legend(fontsize=8, loc="upper right", framealpha=0.9, edgecolor="lightgray")

    _save(fig, "fig10_capacity_mix", save_dir)


# ══════════════════════════════════════════════════════════════════════════════
# Figure 11 — Population diversity heatmap (final generation)
# ══════════════════════════════════════════════════════════════════════════════

def fig11_diversity_heatmap(df_ga, n_gens, best_row, save_dir,
                            df_ga_lp=None, n_gens_lp=None, best_row_lp=None):
    last_gen = df_ga[(df_ga["gen"] == int(n_gens)) & (df_ga["feasible"] == True)].copy()
    if len(last_gen) < 2:
        _skip("Fig 11", "fewer than 2 feasible individuals in final generation")
        return

    cols = [c for c in FACTOR_COLUMNS if c in last_gen.columns and c.upper() not in DEFAULT_LOCKED]
    mat = last_gen[cols].values.astype(float)  # shape: (n_individuals, n_params)

    # Normalize each column by the GA-optimal value (best_row)
    opt_vals = np.array([
        float(best_row[c]) if c in best_row.index else 1.0
        for c in cols
    ])
    # Avoid division by zero
    opt_vals = np.where(np.abs(opt_vals) < 1e-12, 1.0, opt_vals)
    mat_norm = mat / opt_vals[np.newaxis, :]  # ratio to GA-optimal

    # Sort individuals by cost (ascending)
    costs = last_gen["cost_mn_bil_per_year"].values
    order = np.argsort(costs)
    mat_sorted = mat_norm[order]

    # Sort parameters by CV (most constrained first)
    cv = np.std(mat_norm, axis=0) / np.abs(np.mean(mat_norm, axis=0) + 1e-12)
    param_order = np.argsort(cv)
    mat_final = mat_sorted[:, param_order]
    param_labels = [cols[i].upper() for i in param_order]

    def _make_panel(ax_, mat_f, param_lbls, cost_vals, title):
        im_ = ax_.imshow(mat_f, aspect="auto", cmap="RdYlGn",
                         vmin=0.5, vmax=1.5, interpolation="nearest")
        ax_.set_xticks(np.arange(len(param_lbls)))
        ax_.set_xticklabels(param_lbls, rotation=45, ha="right", fontsize=7)
        ax_.set_yticks(np.arange(len(cost_vals)))
        ax_.set_yticklabels([f"#{i+1}  ${c:.1f}B" for i, c in enumerate(cost_vals)], fontsize=7)
        ax_.set_xlabel("Parameter (sorted by CV, most constrained left)")
        ax_.set_ylabel("Individual (sorted by cost, best at top)")
        ax_.set_title(title, loc="left", fontweight="bold")
        return im_

    has_lp = (df_ga_lp is not None and n_gens_lp is not None and best_row_lp is not None)
    ncols  = 2 if has_lp else 1
    fig, axes = plt.subplots(1, ncols, figsize=(max(10, len(cols) * 0.45) * ncols,
                                                 max(7, len(last_gen) * 0.22)))
    if ncols == 1:
        axes = [axes]

    im = _make_panel(axes[0], mat_final, param_labels, costs[order], "A) GA (baseline)")

    if has_lp:
        last_lp = df_ga_lp[(df_ga_lp["gen"] == int(n_gens_lp)) & (df_ga_lp["feasible"] == True)].copy()
        cols_lp = [c for c in FACTOR_COLUMNS if c in last_lp.columns and c.upper() not in DEFAULT_LOCKED]
        if len(last_lp) >= 2 and cols_lp:
            mat_lp = last_lp[cols_lp].values.astype(float)
            opt_lp = np.array([_fac(best_row_lp, c) for c in cols_lp])
            opt_lp = np.where(np.abs(opt_lp) < 1e-12, 1.0, opt_lp)
            mat_lp_norm = mat_lp / opt_lp[np.newaxis, :]
            costs_lp = last_lp["cost_mn_bil_per_year"].values
            ord_lp   = np.argsort(costs_lp)
            cv_lp    = np.std(mat_lp_norm, axis=0) / np.abs(np.mean(mat_lp_norm, axis=0) + 1e-12)
            po_lp    = np.argsort(cv_lp)
            _make_panel(axes[1], mat_lp_norm[ord_lp][:, po_lp],
                        [cols_lp[i].upper() for i in po_lp], costs_lp[ord_lp], "B) GA (LP)")

    cbar = fig.colorbar(im, ax=axes, fraction=0.01, pad=0.01)
    cbar.set_label("Value / case-optimal", fontsize=9)
    _save(fig, "fig11_diversity_heatmap", save_dir)


# ══════════════════════════════════════════════════════════════════════════════
# Figure 12 — Waterfall chart: baseline → GA cost savings by category
# ══════════════════════════════════════════════════════════════════════════════

def fig12_cost_waterfall(bl_costs, opt_costs, bl_energy, opt_energy, save_dir,
                         lp_costs=None, lp_energy=None, lp_ga_costs=None, lp_ga_energy=None):
    if not bl_costs:
        _skip("Fig 12", "baseline cost data not available")
        return

    def _group_bil(costs, energy_twh):
        if not costs or energy_twh is None:
            return None
        return {grp: sum(costs.get(lbl, 0.0) for lbl in lbls) * energy_twh / 100
                for grp, lbls in COST_GROUPS.items()}

    bl_grp = _group_bil(bl_costs, bl_energy)
    if bl_grp is None:
        _skip("Fig 12", "baseline cost groups could not be computed")
        return

    bl_total = sum(bl_grp.values())
    groups   = list(COST_GROUPS.keys())

    # Build comparison cases (skip if data absent)
    comparison_cases = []
    lp_grp = _group_bil(lp_costs, lp_energy)
    if lp_grp is not None:
        comparison_cases.append(("LP", lp_grp, _CASE_COLORS["LP"]))
    ga_bl_grp = _group_bil(opt_costs, opt_energy)
    if ga_bl_grp is not None:
        comparison_cases.append(("GA (bl)", ga_bl_grp, _CASE_COLORS["GA (bl)"]))
    ga_lp_grp = _group_bil(lp_ga_costs, lp_ga_energy)
    if ga_lp_grp is not None:
        comparison_cases.append(("GA (LP)", ga_lp_grp, _CASE_COLORS["GA (LP)"]))

    if not comparison_cases:
        _skip("Fig 12", "no comparison case data available")
        return

    ncols = len(comparison_cases)
    # Extra bottom margin for legend; wider per panel to give bars room
    fig, axes = plt.subplots(1, ncols, figsize=(11 * ncols, 7),
                             gridspec_kw={"wspace": 0.35})
    if ncols == 1:
        axes = [axes]

    def _draw_waterfall(ax, case_grp, case_total, case_color, case_name, panel_label):
        deltas       = {g: case_grp[g] - bl_grp[g] for g in groups}
        sorted_grps  = sorted(groups, key=lambda g: deltas[g])
        # Use the case name for the final bar label instead of generic "Case total"
        labels_wf    = ["Baseline"] + sorted_grps + [case_name]
        running      = bl_total
        bottoms, heights, bar_colors = [0.0], [bl_total], [_CASE_COLORS["Baseline"]]

        for g in sorted_grps:
            d = deltas[g]
            bottoms.append(running + d if d < 0 else running)
            heights.append(abs(d))
            bar_colors.append("tab:green" if d < 0 else "tab:red")
            running += d

        bottoms.append(0.0)
        heights.append(case_total)
        bar_colors.append(case_color)

        n_bars = len(labels_wf)
        x = np.arange(n_bars)
        bar_w = min(0.55, 6.0 / n_bars)   # narrow bars when many categories
        ax.bar(x, heights, bar_w, bottom=bottoms, color=bar_colors,
               edgecolor="black", lw=0.5, alpha=0.85)
        ax.set_xlim(-0.6, n_bars - 0.4)   # padding so end bars aren't clipped

        # Connector lines
        running = bl_total
        for i, g in enumerate(sorted_grps, start=1):
            d = deltas[g]
            y_conn = running + d if d < 0 else running
            ax.plot([x[i] - bar_w / 2, x[i] + bar_w / 2], [y_conn, y_conn],
                    color="gray", lw=0.7, ls="--", zorder=5)
            running += d

        # Value labels
        for xi, (bot, ht, lbl) in enumerate(zip(bottoms, heights, labels_wf)):
            top = bot + ht
            if xi == 0 or xi == n_bars - 1:
                ax.annotate(f"${ht:.1f}B", xy=(xi, top), xytext=(0, 4),
                            textcoords="offset points", ha="center", va="bottom",
                            fontsize=9, fontweight="bold")
            else:
                g   = sorted_grps[xi - 1]
                d   = deltas[g]
                sgn = "−" if d < 0 else "+"
                y_a = top if d >= 0 else bot
                off = (0, 4) if d >= 0 else (0, -4)
                va  = "bottom" if d >= 0 else "top"
                ax.annotate(f"{sgn}${abs(d):.1f}B", xy=(xi, y_a), xytext=off,
                            textcoords="offset points", ha="center", va=va,
                            fontsize=8, color="tab:green" if d < 0 else "tab:red")

        ax.set_xticks(x)
        ax.set_xticklabels(labels_wf, rotation=30, ha="right", fontsize=8)
        ax.set_ylabel(r"Annual system cost (\$B yr$^{-1}$)")
        ax.set_ylim(0, bl_total * 1.12)
        ax.set_title(f"{panel_label})", loc="left", fontweight="bold")
        # Legend at bottom-center of each panel
        ax.legend(handles=[
            Patch(facecolor=_CASE_COLORS["Baseline"], label="Baseline"),
            Patch(facecolor="tab:green",              label="Cost reduction"),
            Patch(facecolor="tab:red",                label="Cost increase"),
            Patch(facecolor=case_color,               label=f"{case_name} total"),
        ], fontsize=8, loc="lower center", frameon=False, ncol=2,
           bbox_to_anchor=(0.5, -0.22))

    for pi, (cname, cgrp, ccolor) in enumerate(comparison_cases):
        _draw_waterfall(axes[pi], cgrp, sum(cgrp.values()), ccolor, cname, "ABC"[pi])

    fig.subplots_adjust(bottom=0.22)
    _save(fig, "fig12_cost_waterfall", save_dir)


# ══════════════════════════════════════════════════════════════════════════════
# Figure 13 — Energy flow Sankey (baseline)
# ══════════════════════════════════════════════════════════════════════════════

def _sankey_ribbon(ax, x0, x1, ly_bot, ly_top, ry_bot, ry_top, color, alpha=0.40):
    """Draw a smooth cubic-ease Sankey ribbon."""
    t = np.linspace(0, 1, 300)
    smooth = 3*t**2 - 2*t**3
    x_arr = x0 + (x1 - x0) * t
    top   = ly_top + (ry_top - ly_top) * smooth
    bot   = ly_bot + (ry_bot - ly_bot) * smooth
    ax.fill_between(x_arr, bot, top, color=color, alpha=alpha, lw=0)


def fig13_sankey(out_path, save_dir, scenario_label="baseline scenario",
                 filename="fig13_sankey_energy_flow"):
    """
    3-tier Sankey: Generation → Storage intermediary → End uses & losses.

    Middle tier has five sections (top→bottom):
      0  Direct electricity (bypass, no storage)
      1  Electrical storage  (battery + PHS + H₂ FC)  ← boxed node
      2  Thermal storage     (HW-STES, UTES, Brick, CW-STES, CSP)  ← boxed node
      3  T&D losses          (bypass)
      4  Curtailment         (bypass)

    Electrical storage throughput is estimated assuming 90 % round-trip efficiency
    for battery+PHS; H₂ throughput is read directly from the output file.
    """
    # keep old positional arg name working
    bl_out_path = out_path
    if not bl_out_path.exists():
        _skip("Fig 13", f"output not found: {bl_out_path}")
        return

    text = bl_out_path.read_text()

    def _get(pattern, default=0.0):
        m = re.search(pattern, text)
        return float(m.group(1)) if m else default

    # ── Generation sources ────────────────────────────────────────────────────
    wind       = _get(r'TWH ON\+OFFSHORE WIND SUPPLY BEFORE T&D LOSS\s+([\d.]+)')
    solar      = _get(r'TWH PV\+CSP SUPPLY BEFORE T&D LOSS\s+([\d.]+)')
    hydro      = _get(r'TWH HYDROELECTRIC SUPPLY BEFORE T&D LOSS\s+([\d.]+)')
    wave       = _get(r'TWH WAVE SUPPLY BEFORE T&D LOSS\s+([\d.]+)')
    geo_elec   = _get(r'TWH GEOTHERMAL ELEC SUPPLY BEFORE T&D LOSS\s+([\d.]+)')
    tidal      = _get(r'TWH TIDAL SUPPLY BEFORE T&D LOSS\s+([\d.]+)')
    solar_heat = _get(r'TWH SOL HOT FLUID SUPPLY BEFORE T&D LOSS\s+([\d.]+)')
    geo_heat   = _get(r'TWH GEOTHERMAL HEAT SUPPLY BEFORE T&D LOSS\s+([\d.]+)')
    src_total  = wind + solar + geo_elec + hydro + wave + tidal + solar_heat + geo_heat

    # ── T&D and curtailment ───────────────────────────────────────────────────
    td_loss = _get(r'TWH TRANSMISSION AND DISTRIBUTION LOSSES\s+([\d.]+)')
    curtail = _get(r'TWH LOSSES FROM CURTAILMENT\s+([\d.]+)')

    # ── Storage losses (charge + discharge, separately where available) ───────
    bat_loss_c = _get(r'TWH LOSSES DURING CHARGING BATTERY STORAGE\s+([\d.]+)')
    bat_loss_d = _get(r'TWH LOSSES DISCHARG BATTERY STORAGE\s+([\d.]+)')
    phs_loss_c = _get(r'TWH LOSSES DURING CHARGING PHS STORAGE\s+([\d.]+)')
    phs_loss_d = _get(r'TWH LOSSES DURING DISCHARGING PHS STORAGE\s+([\d.]+)')
    h2e_loss_d = _get(r'TWH LOSSES DISCHARG H2 ELEC STORAGE\s+([\d.]+)')
    csp_loss   = _get(r'TWH LOSSES CHARG\+DISCHARGING CSP STORAGE\s+([\d.]+)')
    cw_loss    = _get(r'TWH LOSSES CHARG\+DISCH CW-STES\+PCM-ICE STOR\s+([\d.]+)')
    hw_loss    = _get(r'TWH LOSSES CHARG\+DISCH HW-STES STORAGE\s+([\d.]+)')
    utes_loss  = _get(r'TWH LOSSES CHARG\+DISCHARGING UTES STORAGE\s+([\d.]+)')
    brick_loss = _get(r'TWH LOSSES CHARG\+DISCHARGING BRICK HT STOR\s+([\d.]+)')

    elec_stor_losses  = bat_loss_c + bat_loss_d + phs_loss_c + phs_loss_d + h2e_loss_d
    therm_stor_losses = csp_loss + cw_loss + hw_loss + utes_loss + brick_loss

    # ── Net storage changes (negative = added to storage; take abs for balance) ──
    bat_net  = abs(_get(r'TWH USED FROM\(\+\) ADDED TO\(-\) BAT STORAGE\s+([-\d.]+)'))
    phs_net  = abs(_get(r'TWH USED FROM\(\+\) ADDED TO\(-\) PHS STORAGE\s+([-\d.]+)'))
    cw_net   = abs(_get(r'TWH USED FROM\(\+\) ADDED TO\(-\) CW-STES\+PCMICE\s+([-\d.]+)'))
    hw_net   = abs(_get(r'TWH USED FROM\(\+\) ADDED TO\(-\) HW-STES STOR\s+([-\d.]+)'))
    utes_net = abs(_get(r'TWH USED FROM\(\+\) ADDED TO\(-\) UTES STORAGE\s+([-\d.]+)'))
    brick_net= abs(_get(r'TWH USED FROM\(\+\) ADDED TO\(-\) BRICK STORAGE\s+([-\d.]+)'))
    csp_net  = abs(_get(r'TWH USED FROM\(\+\) ADDED TO\(-\) CSP STORAGE\s+([-\d.]+)'))

    # ── End uses ──────────────────────────────────────────────────────────────
    h2_elec   = _get(r'TWH ELECTRICITY FOR H2 DURING SIMULATION\s+([\d.]+)')
    heat_stor = _get(r'TWH END USE HEAT LOAD MET BY STORAGE\s+([\d.]+)')
    cold_stor = _get(r'TWH END USE COLD LOAD MET BY STORAGE\s+([\d.]+)')
    hitemp    = _get(r'TWH END USE HI-T LOAD MET BY BRICK STORAGE\s+([\d.]+)')
    elec_total= _get(r'TWH END USE ELECTRICITY LOAD MET DURING SIM\s+([\d.]+)')

    # ── Compute storage throughput ────────────────────────────────────────────
    # Thermal storage: output is exactly known from the end-use lines
    therm_out     = heat_stor + cold_stor + hitemp
    therm_net_chg = csp_net + cw_net + hw_net + utes_net + brick_net
    therm_in      = therm_out + therm_stor_losses + therm_net_chg

    # H₂ electrical storage: input = electricity to electrolysers (exactly known)
    h2_in  = h2_elec
    h2_out = h2_elec - h2e_loss_d   # electricity equivalent at fuel-cell output

    # Battery + PHS: throughput estimated with 90 % round-trip efficiency assumption
    bp_losses  = bat_loss_c + bat_loss_d + phs_loss_c + phs_loss_d
    bp_net_chg = bat_net + phs_net
    rt_eff     = 0.90
    bp_out = (bp_losses + bp_net_chg) / (1.0 / rt_eff - 1.0)
    bp_in  = bp_out + bp_losses + bp_net_chg

    elec_in  = h2_in  + bp_in
    elec_out = h2_out + bp_out

    # Direct electricity: generation remaining after removing T&D, curtailment,
    # and both storage inputs
    net_after_losses = src_total - curtail - td_loss
    direct_elec      = net_after_losses - elec_in - therm_in

    # ── Node definitions ──────────────────────────────────────────────────────
    sources = [
        ("Wind\n(on+offshore)",     wind,                  "#2E75B6"),
        ("Solar PV\n& CSP",         solar,                 "#FF9900"),
        ("Geothermal\n(electric)",  geo_elec,              "#C0504D"),
        ("Hydro / Wave\n/ Tidal",   hydro + wave + tidal,  "#70AD47"),
        ("Solar &\ngeo thermal",    solar_heat + geo_heat,  "#FFC000"),
    ]

    # Middle tier — only storage nodes (boxed); direct/T&D/curtailment bypass
    mid_nodes = [
        ("Electrical\nstorage", elec_in,  "#7030A0", True),
        ("Thermal\nstorage",    therm_in, "#E36C09", True),
    ]

    destinations = [
        ("Direct electricity",           direct_elec,       "#FFD700"),   # 0 — yellow
        ("Electricity\nfrom storage",    elec_out,          "#FFC200"),   # 1 — amber
        ("Hi-T heat\n(industry)",        hitemp,            "#E63900"),   # 2 — deep orange
        ("Heat\n(buildings)",            heat_stor,         "#FF8C00"),   # 3 — orange
        ("Cold\n(buildings)",            cold_stor,         "#00B0F0"),   # 4 — turquoise
        ("Elec. storage\nlosses*",       elec_stor_losses,  "#FFE566"),   # 5 — light yellow
        ("Thermal storage\nlosses",      therm_stor_losses, "#FFBB77"),   # 6 — light orange
        ("T&D losses",                   td_loss,           "#808080"),   # 7 — grey
        ("Curtailment\n(excess)",        curtail,           "#E74C3C"),   # 8 — red
    ]

    # Flows that bypass the storage column: (flow_value, color, dst_idx)
    bypass_flows = [
        (direct_elec, "#FFD700", 0),   # yellow — direct electricity
        (td_loss,     "#808080", 7),   # grey   — T&D losses
        (curtail,     "#E74C3C", 8),   # red    — curtailment
    ]

    # Storage routing: mid_idx → [(dst_idx, flow_val), ...]
    mid_to_dst = {
        0: [(1, elec_out), (5, elec_stor_losses)],
        1: [(2, hitemp), (3, heat_stor), (4, cold_stor), (6, therm_stor_losses)],
    }

    # ── Scaling helpers ───────────────────────────────────────────────────────
    gap_frac = 0.010

    def _stack(items_vals_colors, ref):
        gap = gap_frac * ref
        pos, y = [], 0.0
        for _, v, *_ in items_vals_colors:
            pos.append((y, y + v))
            y += v + gap
        return pos, y - gap   # positions, total height

    src_pos, src_h = _stack(sources,      src_total)
    mid_pos, mid_h = _stack(mid_nodes,    src_total)
    dst_pos, dst_h = _stack(destinations, src_total)

    # Destination column scaled to match source height; storage column stays
    # proportional (s_mid=1) and is centered vertically.
    s_mid = 1.0
    s_dst = src_h / dst_h
    mid_offset = (src_h - mid_h) / 2
    mid_pos = [(b + mid_offset, t + mid_offset) for b, t in mid_pos]
    dst_pos = [(b * s_dst, t * s_dst) for b, t in dst_pos]

    # ── Figure setup ──────────────────────────────────────────────────────────
    bar_w = 0.045
    x_sl, x_sr = 0.00, bar_w
    x_ml, x_mr = 0.42, 0.42 + bar_w
    x_dl, x_dr = 0.87, 0.87 + bar_w
    lpad = 0.010

    fig, ax = plt.subplots(figsize=(20, 9))
    ax.set_xlim(-0.32, 1.22)
    ax.set_ylim(-0.06 * src_h, 1.10 * src_h)
    ax.axis("off")

    # Initialise ribbon cursors
    src_cur = [b for b, _ in src_pos]
    dst_cur = [b for b, _ in dst_pos]

    # ── Bypass ribbons first (drawn behind storage column) ────────────────────
    # Direct electricity, T&D losses, and curtailment go straight from the
    # source column to the destination column, bypassing the storage boxes.
    # Ribbons are colored by their source energy type.
    for bp_val, _bp_color, dst_idx in bypass_flows:
        for i, ((_sn, src_val, src_color), _) in enumerate(zip(sources, src_pos)):
            rw = src_val * bp_val / src_total
            l_bot, l_top = src_cur[i],       src_cur[i] + rw
            d_bot, d_top = dst_cur[dst_idx], dst_cur[dst_idx] + rw * s_dst
            src_cur[i]       = l_top
            dst_cur[dst_idx] = d_top
            _sankey_ribbon(ax, x_sr, x_dl, l_bot, l_top, d_bot, d_top,
                           color=src_color, alpha=0.28)

    # ── Draw bar fills (on top of bypass ribbons) ─────────────────────────────
    for (name, val, color), (y_bot, y_top) in zip(sources, src_pos):
        ax.fill_between([x_sl, x_sr], [y_bot]*2, [y_top]*2, color=color, alpha=0.9, lw=0)

    for (name, val, color, boxed), (y_bot, y_top) in zip(mid_nodes, mid_pos):
        ax.fill_between([x_ml, x_mr], [y_bot]*2, [y_top]*2, color=color, alpha=0.85, lw=0)
        if boxed:
            from matplotlib.patches import FancyBboxPatch
            ax.add_patch(FancyBboxPatch(
                (x_ml, y_bot), bar_w, y_top - y_bot,
                boxstyle="square,pad=0", fill=False,
                edgecolor="black", lw=2.0, zorder=10))
        mid_y = (y_bot + y_top) / 2
        ax.text((x_ml + x_mr) / 2, mid_y, name, ha="center", va="center",
                fontsize=8, color="white", fontweight="bold", zorder=11)
        ax.text(x_mr + lpad, mid_y, f"{val:,.0f}", ha="left", va="center",
                fontsize=7, color="#666")

    for (name, val, color), (y_bot, y_top) in zip(destinations, dst_pos):
        ax.fill_between([x_dl, x_dr], [y_bot]*2, [y_top]*2, color=color, alpha=0.9, lw=0)

    # ── Bar labels: auto-spaced with connector arrows for small/cramped bars ──
    # Iteratively spread label y-positions until no two are closer than _min_gap.
    _min_gap = 0.034 * src_h

    def _spread(centers):
        adj = list(centers)
        for _ in range(60):
            changed = False
            for k in range(1, len(adj)):
                if adj[k] - adj[k - 1] < _min_gap:
                    m = (adj[k] + adj[k - 1]) / 2
                    adj[k - 1] = m - _min_gap / 2
                    adj[k]     = m + _min_gap / 2
                    changed    = True
            if not changed:
                break
        return adj

    # Source labels (left side of source column)
    src_centers = [(b + t) / 2 for b, t in src_pos]
    src_adj     = _spread(src_centers)
    for k, ((name, val, color), (y_bot, y_top)) in enumerate(zip(sources, src_pos)):
        c, ly = src_centers[k], src_adj[k]
        if abs(ly - c) > 0.004 * src_h:
            ax.annotate(
                f"{name}  ({val:,.0f} TWh)",
                xy=(x_sl, c), xytext=(x_sl - 0.11, ly),
                ha="right", va="center", fontsize=8, color="#222222", fontweight="bold",
                arrowprops=dict(arrowstyle="-", color="#aaa", lw=0.7,
                                shrinkA=1, shrinkB=2),
            )
        else:
            ax.text(x_sl - lpad, ly, name, ha="right", va="center",
                    fontsize=9, color="#222222", fontweight="bold")
            ax.text(x_sr + lpad, ly, f"{val:,.0f}", ha="left", va="center",
                    fontsize=7.5, color="#666")

    # Destination labels (right side of destination column)
    dst_centers = [(b + t) / 2 for b, t in dst_pos]
    dst_adj     = _spread(dst_centers)
    for k, ((name, val, color), (y_bot, y_top)) in enumerate(zip(destinations, dst_pos)):
        c, ly = dst_centers[k], dst_adj[k]
        if abs(ly - c) > 0.004 * src_h:
            ax.annotate(
                f"{name}  ({val:,.0f} TWh)",
                xy=(x_dr, c), xytext=(x_dr + 0.11, ly),
                ha="left", va="center", fontsize=8, color="#222222", fontweight="bold",
                arrowprops=dict(arrowstyle="-", color="#aaa", lw=0.7,
                                shrinkA=1, shrinkB=2),
            )
        else:
            ax.text(x_dr + lpad, ly, name, ha="left", va="center",
                    fontsize=8.5, color="#222222", fontweight="bold")
            ax.text(x_dl - lpad, ly, f"{val:,.0f}", ha="right", va="center",
                    fontsize=7.5, color="#666")

    # ── Source → storage ribbons (uniform mixing) ─────────────────────────────
    mid_l_cur = [b for b, _ in mid_pos]

    for j, ((_mn, mid_val, _mc, _bx), _) in enumerate(zip(mid_nodes, mid_pos)):
        for i, ((_sn, src_val, src_color), _) in enumerate(zip(sources, src_pos)):
            rw = src_val * mid_val / src_total
            l_bot, l_top = src_cur[i],    src_cur[i] + rw
            r_bot, r_top = mid_l_cur[j],  mid_l_cur[j] + rw * s_mid
            src_cur[i]   = l_top
            mid_l_cur[j] = r_top
            _sankey_ribbon(ax, x_sr, x_ml, l_bot, l_top, r_bot, r_top,
                           color=src_color, alpha=0.28)

    # ── Storage → destination ribbons (fixed routing, correct width formula) ──
    mid_r_cur = [b for b, _ in mid_pos]

    for mid_idx, connections in mid_to_dst.items():
        _, _mv, _mc, _ = mid_nodes[mid_idx]
        for dst_idx, flow in connections:
            _, _dv, dst_color = destinations[dst_idx]
            rw_mid = flow * s_mid   # correct: flow in raw units × scale factor
            rw_dst = flow * s_dst
            m_bot, m_top = mid_r_cur[mid_idx], mid_r_cur[mid_idx] + rw_mid
            d_bot, d_top = dst_cur[dst_idx],   dst_cur[dst_idx]   + rw_dst
            mid_r_cur[mid_idx] = m_top
            dst_cur[dst_idx]   = d_top
            _sankey_ribbon(ax, x_mr, x_dl, m_bot, m_top, d_bot, d_top,
                           color=dst_color, alpha=0.40)

    # ── Column labels ─────────────────────────────────────────────────────────
    for x_ctr, label in [
        ((x_sl + x_sr) / 2, "Generation\nsources"),
        ((x_ml + x_mr) / 2, "Storage\n(intermediary)"),
        ((x_dl + x_dr) / 2, "End uses\n& losses"),
    ]:
        ax.text(x_ctr, -0.05 * src_h, label, ha="center", va="top",
                fontsize=10, color="#444", style="italic")

    ax.text(x_sr + lpad, src_h * 1.01,
            f"Total generation: {src_total:,.0f} TWh/yr",
            ha="left", va="bottom", fontsize=8.5, color="#333",
            bbox=dict(boxstyle="round,pad=0.2", fc="white", alpha=0.7, lw=0))

    elec_mid_y  = (mid_pos[0][0] + mid_pos[0][1]) / 2
    therm_mid_y = (mid_pos[1][0] + mid_pos[1][1]) / 2
    ax.text(x_mr + lpad, therm_mid_y,
            f"Thermal storage\nin:  {therm_in:,.0f} TWh\nout: {therm_out:,.0f} TWh\n"
            f"loss:{therm_stor_losses:,.0f} TWh",
            ha="left", va="center", fontsize=7, color="#333",
            bbox=dict(boxstyle="round,pad=0.25", fc="white", alpha=0.85, lw=0.5))

    ax.text(x_mr + lpad, elec_mid_y,
            f"Elec. storage\nin:  {elec_in:,.0f} TWh\nout: {elec_out:,.0f} TWh\n"
            f"loss:{elec_stor_losses:,.0f} TWh\n(*bat+PHS throughput est.)",
            ha="left", va="center", fontsize=7, color="#333",
            bbox=dict(boxstyle="round,pad=0.25", fc="white", alpha=0.85, lw=0.5))

    _save(fig, filename, save_dir)
    print(f"  Thermal storage: in={therm_in:,.0f} TWh  out={therm_out:,.0f} TWh  "
          f"losses={therm_stor_losses:,.0f} TWh")
    print(f"  Elec. storage:   in={elec_in:,.0f} TWh  out={elec_out:,.0f} TWh  "
          f"losses={elec_stor_losses:,.0f} TWh  (bat+PHS est. at 90% RT eff.)")
    print(f"  30-second dispatch resolution: storage cycling losses = "
          f"{elec_stor_losses + therm_stor_losses:,.0f} TWh "
          f"({100*(elec_stor_losses+therm_stor_losses)/src_total:.1f}% of generation)")


# ══════════════════════════════════════════════════════════════════════════════
# Multi-region overview figures
# ══════════════════════════════════════════════════════════════════════════════

_REGION_SHORT = {
    "UNITED-STATES": "US",   "CANADA":       "CA",   "EUROPE":       "EU",
    "CHINA":         "CN",   "INDIA":        "IN",   "JAPAN":        "JP",
    "AUSTRALIA":     "AU",   "RUSSIA":       "RU",   "SOUTHEAST-ASIA": "SEA",
    "AFRICA-EAST":   "AFR-E","AFRICA-NORTH": "AFR-N","AFRICA-SOUTH": "AFR-S",
    "AFRICA-WEST":   "AFR-W","MIDEAST":      "ME",   "SOUTH-KOREA":  "KR",
    "TAIWAN":        "TW",   "PHILIPPINES":  "PH",   "NEW-ZEALAND":  "NZ",
    "CENTRAL-AMERIC":"CAMR", "CENTRAL-ASIA": "CASA", "SOUTHAM-NW":   "SA-NW",
    "SOUTHAM-SE":    "SA-SE","MADAGASCAR":   "MDG",  "MAURITIUS":    "MUS",
    "ICELAND":       "ISL",  "ISRAEL":       "IL",   "JAMAICA":      "JAM",
    "CUBA":          "CUB",  "HAITI":        "HTI",
}
_S3_TERNARY = np.sqrt(3)

# 32-colour palette built from tab20 (20) + 12 hand-picked from tab20b that are
# visually distinct from the tab20 set.  Supports all 29 world regions with room
# to spare; colours cycle if more than 32 regions are ever added.
_PALETTE = (
    list(plt.cm.tab20.colors) +          # indices 0-19
    [plt.cm.tab20b.colors[i] for i in    # indices 20-31 (12 from tab20b)
     [0, 4, 8, 12, 16, 1, 5, 9, 13, 17, 2, 6]]
)


def _region_color(idx):
    return _PALETTE[idx % len(_PALETTE)]


def _rshort(region):
    return _REGION_SHORT.get(region, region[:4])


def _cap_shares(cap):
    """Return (wind_frac, solar_frac, water_frac) of renewable-only total."""
    wind  = cap.get("onshore_wind", 0) + cap.get("offshore_wind", 0)
    solar = (cap.get("res_pv", 0) + cap.get("com_pv", 0) +
             cap.get("utility_pv", 0) + cap.get("csp", 0))
    water = cap.get("hydro", 0) + cap.get("wave", 0) + cap.get("tidal", 0)
    tot = wind + solar + water
    if tot < 1:
        return 0., 0., 0.
    return wind / tot, solar / tot, water / tot


def _t2c_mod(wind, solar, water):
    tot = wind + solar + water
    if tot < 1e-9:
        return 0.5, 0.0
    w, s = wind / tot, solar / tot
    return s + w * 0.5, w * _S3_TERNARY / 2


def _build_cap(fac_fn):
    return {
        "onshore_wind":  fac_fn("faconwin")   * BASE_CAPACITIES_USA["onshore_wind"],
        "offshore_wind": fac_fn("facoffwin")  * BASE_CAPACITIES_USA["offshore_wind"],
        "res_pv":        fac_fn("facrespv")   * BASE_CAPACITIES_USA["res_rooftop_pv"],
        "com_pv":        fac_fn("faccompv")   * BASE_CAPACITIES_USA["com_rooftop_pv"],
        "utility_pv":    fac_fn("facutilpv")  * BASE_CAPACITIES_USA["utility_pv"],
        "csp":           fac_fn("cspturbfac") * BASE_CAPACITIES_USA["csp"],
        **FIXED_2050_MW,
    }


def fig_all_convergence(region_data, overview_dir):
    """Relative cost reduction over GA generations for all regions."""
    fig, ax = plt.subplots(figsize=(13, 6))

    _LINESTYLES = ["-", "--", "-.", ":"]
    for idx, (region, rd) in enumerate(region_data.items()):
        color = _region_color(idx)
        ls = _LINESTYLES[idx // len(_PALETTE) % len(_LINESTYLES)]
        df_gen = rd["df_gen"]
        baseline_cost = rd["baseline_cost"]
        if baseline_cost is None or baseline_cost <= 0:
            continue
        valid = df_gen[df_gen["cum_best_cost"] < float("inf")].copy()
        if valid.empty:
            continue
        reduction = (1 - valid["cum_best_cost"] / baseline_cost) * 100
        ax.plot(valid["gen"], reduction, lw=1.8, color=color, ls=ls,
                label=f"{_rshort(region)}  ({baseline_cost:.1f} → {rd['optimal_cost']:.1f} $B/yr)")

    ax.axhline(0, color="gray", lw=0.8, ls="--", alpha=0.5)
    ax.set_xlabel("Generation")
    ax.set_ylabel("Cost reduction vs baseline (%)")
    ax.legend(bbox_to_anchor=(1.01, 1), loc="upper left", borderaxespad=0,
              frameon=False, ncol=2, fontsize=8)
    ax.set_xlim(left=1)
    ax.set_ylim(bottom=0)
    _save(fig, "figA1_all_regions_convergence", overview_dir)


def fig_all_wind_solar(region_data, overview_dir):
    """Wind vs solar share scatter for all regions (baseline + GA-optimal)."""
    fig, ax = plt.subplots(figsize=(11, 6))

    # Collect axis range
    all_s, all_w = [], []
    plot_pts = []
    for idx, (region, rd) in enumerate(region_data.items()):
        color = _region_color(idx)
        bl_cap  = rd["bl_cap"]
        ga_cap  = rd["ga_cap"]
        wbl, sbl, _ = _cap_shares(bl_cap)
        wga, sga, _ = _cap_shares(ga_cap)
        all_s += [sbl * 100, sga * 100]
        all_w += [wbl * 100, wga * 100]
        plot_pts.append((region, color, sbl * 100, wbl * 100, sga * 100, wga * 100))

    x_max = max(all_s) * 1.15
    y_max = max(all_w) * 1.20
    sol_arr = np.linspace(0, x_max, 200)
    for C in range(10, 110, 10):
        wind_line = C - sol_arr
        mask = (wind_line >= 0) & (wind_line <= y_max) & (sol_arr >= 0)
        if mask.sum() > 1:
            if C == 100:
                ax.plot(sol_arr[mask], wind_line[mask], color="#888888", lw=1.2, zorder=1)
            else:
                ax.plot(sol_arr[mask], wind_line[mask], color="lightgray", lw=0.8, zorder=1)

    legend_handles = []
    for idx, (region, color, sbl, wbl, sga, wga) in enumerate(plot_pts):
        ax.scatter(sbl, wbl, marker="s", color=color, s=80,
                   edgecolors="black", lw=0.7, zorder=5)
        ax.scatter(sga, wga, marker="^", color=color, s=80,
                   edgecolors="black", lw=0.7, zorder=5)
        legend_handles.append(Patch(facecolor=color, label=_rshort(region)))

    legend_handles += [
        Line2D([0], [0], marker="s", color="gray", ms=7, lw=0,
               markeredgecolor="black", label="Baseline"),
        Line2D([0], [0], marker="^", color="gray", ms=7, lw=0,
               markeredgecolor="black", label="GA optimal"),
    ]
    ax.set_xlabel("Solar share of 2050 installed capacity (%)")
    ax.set_ylabel("Wind share of 2050 installed capacity (%)")
    ax.set_xlim(0, x_max)
    ax.set_ylim(0, y_max)
    ax.legend(handles=legend_handles, bbox_to_anchor=(1.01, 1), loc="upper left",
              borderaxespad=0, frameon=False, ncol=2, fontsize=8)
    _save(fig, "figA2_all_regions_wind_solar", overview_dir)


def fig_all_ternary(region_data, overview_dir):
    """Wind–Solar–Water ternary for all regions (baseline + GA-optimal)."""
    fig, ax = plt.subplots(figsize=(11, 7))

    # Draw triangle and grid
    tri = np.array([[0, 0], [1, 0], [0.5, _S3_TERNARY / 2], [0, 0]])
    ax.plot(tri[:, 0], tri[:, 1], "k-", lw=1.5, zorder=4)
    for f in np.arange(0.2, 1.0, 0.2):
        for p0, p1 in [
            (_t2c_mod(f, 1-f, 0), _t2c_mod(f, 0, 1-f)),
            (_t2c_mod(1-f, f, 0), _t2c_mod(0, f, 1-f)),
            (_t2c_mod(1-f, 0, f), _t2c_mod(0, 1-f, f)),
        ]:
            ax.plot([p0[0], p1[0]], [p0[1], p1[1]],
                    color="lightgray", lw=0.5, alpha=0.7, zorder=1)
        x_w, y_w = _t2c_mod(f, 0, 1-f)
        ax.text(x_w - 0.03, y_w, f"{int(f*100)}%", ha="right", va="center",
                fontsize=6.5, color="#2E75B6")
        xs, ys = _t2c_mod(1-f, f, 0)
        ax.text(xs + 0.03, ys, f"{int(f*100)}%", ha="left", va="center",
                fontsize=6.5, color="#FF9900")
        ax.text(1 - f, -0.04, f"{int(f*100)}%", ha="center", va="top",
                fontsize=6.5, color="#70AD47")

    ax.text(0.5,  _S3_TERNARY/2 + 0.07, "Wind",  ha="center", va="bottom",
            fontsize=12, fontweight="bold", color="#2E75B6")
    ax.text(-0.06, -0.02, "Water", ha="right", va="top",
            fontsize=12, fontweight="bold", color="#70AD47")
    ax.text(1.06,  -0.02, "Solar", ha="left",  va="top",
            fontsize=12, fontweight="bold", color="#FF9900")

    legend_handles = []
    for idx, (region, rd) in enumerate(region_data.items()):
        color = _region_color(idx)
        for cap, marker in [(rd["bl_cap"], "s"), (rd["ga_cap"], "^")]:
            wf, sf, watf = _cap_shares(cap)
            xp, yp = _t2c_mod(wf, sf, watf)
            ax.scatter(xp, yp, marker=marker, color=color, s=70,
                       edgecolors="black", lw=0.7, zorder=5)
        legend_handles.append(Patch(facecolor=color, label=_rshort(region)))

    legend_handles += [
        Line2D([0], [0], marker="s", color="gray", ms=7, lw=0,
               markeredgecolor="black", label="Baseline"),
        Line2D([0], [0], marker="^", color="gray", ms=7, lw=0,
               markeredgecolor="black", label="GA optimal"),
    ]
    ax.set_xlim(-0.18, 1.18)
    ax.set_ylim(-0.12, _S3_TERNARY / 2 + 0.16)
    ax.set_aspect("equal")
    ax.axis("off")
    ax.legend(handles=legend_handles, bbox_to_anchor=(1.01, 1), loc="upper left",
              borderaxespad=0, frameon=False, ncol=2, fontsize=8)
    _save(fig, "figA3_all_regions_ternary", overview_dir)


def plot_all_regions(repo_root):
    """Load results for all available regions and generate overview figures."""
    results_root = repo_root / "data" / "results_verification"
    overview_dir = results_root  # save directly here, not in a region subfolder

    # Collect all regions with completed results
    region_data = {}
    for region_dir in sorted(results_root.iterdir()):
        if not region_dir.is_dir():
            continue
        if not (region_dir / "optimal_summary.json").exists():
            continue  # run_ga not finished yet for this region
        if not (region_dir / "factor_history.log").exists():
            continue
        region = region_dir.name
        print(f"\n  Loading {region} for overview figures...")
        result = _load_ga_data(region, repo_root)
        if result[0] is None:
            continue
        df_ga, df_gen, df_best, best_row, n_gens, records, save_dir = result

        # Baseline cost from first log record; optimal from best GA row
        baseline_cost = None
        for rec in records:
            c = rec.get("cost_mn_bil_per_year", float("inf"))
            if c < float("inf"):
                baseline_cost = c
                break
        # Also check canonical_baseline_summary.json if available
        cbl_json = save_dir / "canonical_baseline_summary.json"
        if cbl_json.exists():
            try:
                d = json.loads(cbl_json.read_text())
                baseline_cost = d.get("cost_bn_per_yr", d.get("cost", baseline_cost))
            except Exception:
                pass

        optimal_cost = float(best_row["cost_mn_bil_per_year"])

        # Baseline and optimal capacity dicts
        try:
            bl_factors = extract_fortran_region_defaults(region)
        except Exception:
            bl_factors = {}

        def _ga_fac(col):
            return float(best_row[col]) if col in best_row.index else PARAM_REGISTRY[col.upper()][0]
        def _bl_fac(col):
            return bl_factors.get(col.upper(), PARAM_REGISTRY[col.upper()][0])

        bl_cap = _build_cap(_bl_fac)
        ga_cap = _build_cap(_ga_fac)

        region_data[region] = {
            "df_gen": df_gen,
            "baseline_cost": baseline_cost,
            "optimal_cost":  optimal_cost,
            "bl_cap": bl_cap,
            "ga_cap": ga_cap,
        }

    if len(region_data) < 2:
        print(f"\n  [SKIP] Overview figures: need ≥2 regions with results "
              f"(found {len(region_data)}).")
        return

    print(f"\n  Generating overview figures for {list(region_data.keys())}...")
    _try_fig("FigA1 — All-region cost convergence",
             fig_all_convergence, region_data, overview_dir)
    _try_fig("FigA2 — All-region wind/solar scatter",
             fig_all_wind_solar, region_data, overview_dir)
    _try_fig("FigA3 — All-region ternary",
             fig_all_ternary, region_data, overview_dir)


# ══════════════════════════════════════════════════════════════════════════════
# Data loading
# ══════════════════════════════════════════════════════════════════════════════

def _load_ga_data(region, repo_root, log_name="factor_history.log"):
    save_dir  = repo_root / "data" / "results_verification" / region
    log_path  = save_dir / log_name

    if not log_path.exists():
        print(f"  [ERROR] factor_history.log not found: {log_path}")
        return None, None, None, None, None, None, None

    records = parse_factor_history(log_path)
    df = records_to_dataframe(records)
    print(f"Loaded {len(df)} trials from {log_path}")

    def _parse_label(label):
        m = re.match(r"GA-gen(\d+)-ind(\d+)", label)
        return (int(m.group(1)), int(m.group(2))) if m else (None, None)

    df["gen"], df["ind"] = zip(*df["label"].map(_parse_label))
    ga_mask = df["gen"].notna()
    df_ga = df[ga_mask].copy()
    df_ga["gen"] = df_ga["gen"].astype(int)

    n_gens   = df_ga["gen"].max()
    pop_size = df_ga.groupby("gen").size().median()
    if pd.isna(n_gens) or pd.isna(pop_size):
        print(f"  [SKIP] Not enough GA data to plot (only {len(df_ga)} GA trials)")
        return (None,) * 7
    print(f"GA: {int(n_gens)} generations, population ≈ {int(pop_size)}")

    # Per-generation aggregates
    gen_best = []
    cum_best = float("inf")
    for g in range(1, int(n_gens) + 1):
        gen_df  = df_ga[df_ga["gen"] == g]
        feasible = gen_df[gen_df["feasible"] == True]
        if len(feasible):
            gen_min  = feasible["cost_mn_bil_per_year"].min()
            cum_best = min(cum_best, gen_min)
        gen_best.append({
            "gen": g,
            "gen_best_cost": feasible["cost_mn_bil_per_year"].min() if len(feasible) else float("inf"),
            "cum_best_cost": cum_best,
            "n_feasible": len(feasible),
            "n_total": len(gen_df),
            "feas_frac": len(feasible) / len(gen_df) if len(gen_df) else 0,
        })
    df_gen = pd.DataFrame(gen_best)

    # Restrict to GA-iteration rows only so warm-start seed entries (e.g. "LP-eval")
    # never masquerade as the GA-optimal solution.
    best_row = df_ga[(df_ga["feasible"] == True) & (df_ga["cost_mn_bil_per_year"] < float("inf"))]
    if best_row.empty:
        # Fallback: accept any feasible row (covers edge cases like a single inflate candidate)
        best_row = df[(df["feasible"] == True) & (df["cost_mn_bil_per_year"] < float("inf"))]
    if best_row.empty:
        print("  [WARN] No feasible row found in history log")
        return (None,) * 7
    best_row = best_row.loc[best_row["cost_mn_bil_per_year"].idxmin()]
    print(f"Best feasible: {best_row['label']}  cost=${best_row['cost_mn_bil_per_year']:.2f}B/yr")

    best_per_gen = []
    for g in range(1, int(n_gens) + 1):
        gen_df = df_ga[(df_ga["gen"] == g) & (df_ga["feasible"] == True)]
        if len(gen_df):
            best_per_gen.append(gen_df.loc[gen_df["cost_mn_bil_per_year"].idxmin()])
    df_best = pd.DataFrame(best_per_gen)

    return df_ga, df_gen, df_best, best_row, n_gens, records, save_dir


def _load_fortran_costs(region, save_dir, repo_root, best_row):
    """
    Load / generate Fortran output files for cost analysis.
    Returns (bl_costs, opt_costs, bl_energy, opt_energy).
    Missing files → None values with a printed warning.
    """
    optimal_out  = save_dir / "fortran_optimal_run.out"
    xxegs_out    = repo_root / "data" / "raw" / f"xxEGS.{region}"
    baseline_out = xxegs_out if xxegs_out.exists() else save_dir / "fortran_baseline_run.out"

    opt_costs, opt_energy = {}, None
    bl_costs,  bl_energy  = {}, None

    # Try to run Fortran for GA-optimal if not cached
    if not optimal_out.exists() and best_row is not None:
        try:
            factor_file = repo_root / "fortran" / "fortran_factors.dat"
            lines = []
            for k in FACTOR_KEYS:
                col = k.lower()
                val = float(best_row[col]) if col in best_row.index else PARAM_REGISTRY[k][0]
                lines.append(f"{k} = {val:.8f}\n")
            factor_file.write_text("".join(lines))
            fortran_bin = repo_root / "fortran" / "bin" / "powerworld"
            result = subprocess.run([str(fortran_bin), region], cwd=repo_root,
                                    capture_output=True, text=True, timeout=300)
            if result.returncode == 0 and result.stdout.strip():
                optimal_out.write_text(result.stdout)
                print(f"  Generated {optimal_out.name}")
            else:
                print(f"  Fortran run failed (code {result.returncode})")
        except Exception as e:
            print(f"  Could not run Fortran: {e}")

    if optimal_out.exists():
        opt_costs, opt_energy = _parse_fortran_costs(optimal_out.read_text())
    else:
        print(f"  [WARN] GA optimal Fortran output not found: {optimal_out}")

    if baseline_out.exists():
        bl_costs, bl_energy = _parse_fortran_costs(baseline_out.read_text())
    else:
        print(f"  [WARN] No baseline Fortran output found for {region}")
        print(f"         Tried: {xxegs_out}")
        print(f"         Tried: {save_dir / 'fortran_baseline_run.out'}")

    return bl_costs, opt_costs, bl_energy, opt_energy, baseline_out


# ══════════════════════════════════════════════════════════════════════════════
# LP input-data & system figures
# ══════════════════════════════════════════════════════════════════════════════

def fig_input_data(region: str, save_dir: Path):
    """Three-panel figure showing the LP input data for one region.

    A) Monthly-mean electric / heat / cold load profiles (MW).
    B) Capacity-factor profiles for a sample summer week (wind + PV).
    C) Fixed baseload breakdown (hydro, tidal, wave, geo_elec, geo_heat).
    """
    from src.io.data_loader import load_inputs
    try:
        inputs = load_inputs(region)
    except Exception as exc:
        print(f"  [SKIP] fig_input_data: could not load inputs for {region}: {exc}")
        return

    elec = np.asarray(inputs["electric_load_mw"])
    heat = np.asarray(inputs["heat_load_mw"])
    cold = np.asarray(inputs["cold_load_mw"])
    avail = inputs["availability"]
    fixed = inputs.get("fixed_baseload_mw", {})
    n = len(elec)

    # Monthly means (8760 hours → 12 months of ~730 h each)
    hrs_per_month = n / 12
    months = np.arange(12)
    def monthly(arr):
        return [float(np.mean(arr[int(i*hrs_per_month):int((i+1)*hrs_per_month)]))
                for i in range(12)]

    # Sample week: mid-July (hour ~4380 ± 84)
    mid = min(int(4380), n - 168)
    week_slice = slice(mid, mid + 168)
    week_h = np.arange(168)

    fig, axes = plt.subplots(1, 3, figsize=(15, 4))

    # Panel A — monthly loads
    ax = axes[0]
    month_labels = ["Jan","Feb","Mar","Apr","May","Jun",
                    "Jul","Aug","Sep","Oct","Nov","Dec"]
    ax.plot(months, monthly(elec), "-o", ms=4, label="Electric", color="steelblue")
    ax.plot(months, monthly(heat), "-s", ms=4, label="Heat",     color="firebrick")
    ax.plot(months, monthly(cold), "-^", ms=4, label="Cold",     color="teal")
    ax.set_xticks(months)
    ax.set_xticklabels(month_labels, rotation=45, ha="right", fontsize=8)
    ax.set_ylabel("Average load (MW)")
    ax.set_title("A)", loc="left", fontweight="bold")
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3)

    # Panel B — sample week capacity factors
    ax = axes[1]
    cf_colors = {
        "onshore_wind":  "steelblue",
        "offshore_wind": "royalblue",
        "utility_pv":    "gold",
        "rooftop_pv":    "orange",
        "csp":           "darkorange",
    }
    for tech, color in cf_colors.items():
        cf = avail.get(tech)
        if cf is not None and len(cf) >= mid + 168:
            ax.plot(week_h, cf[week_slice], lw=0.8, color=color,
                    label=tech.replace("_", " "), alpha=0.85)
    ax.set_xlabel("Hour of sample week (mid-July)")
    ax.set_ylabel("Capacity factor (0–1)")
    ax.set_title("B)", loc="left", fontweight="bold")
    ax.legend(fontsize=7, ncol=2)
    ax.set_ylim(0, 1.05)
    ax.grid(True, alpha=0.3)

    # Panel C — fixed baseload breakdown
    ax = axes[2]
    labels = ["Hydro", "Tidal", "Wave", "Geo\nElec", "Geo\nHeat"]
    keys   = ["hydro", "tidal", "wave", "geo_elec", "geo_heat"]
    colors = ["steelblue", "cadetblue", "slategray", "saddlebrown", "coral"]
    values = [fixed.get(k, 0.0) for k in keys]
    bars = ax.bar(labels, values, color=colors, edgecolor="white")
    for bar, val in zip(bars, values):
        if val > 0:
            ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + max(values)*0.01,
                    f"{val/1000:.1f} GW", ha="center", va="bottom", fontsize=7)
    ax.set_ylabel("Average dispatch (MW)")
    ax.set_title("C)", loc="left", fontweight="bold")
    ax.grid(True, alpha=0.3, axis="y")

    fig.tight_layout()
    _save(fig, "fig_input_data", save_dir)


def fig_lp_system_diagram(save_dir: Path):
    """Static schematic of the LP energy system model.

    Shows four sectors (Electric, Heat, Cold, H2) with technologies and
    storage connected by annotated arrows.  One-time output, not per-region.
    """
    from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
    import matplotlib.patheffects as pe

    fig, ax = plt.subplots(figsize=(14, 9))
    ax.set_xlim(0, 14)
    ax.set_ylim(0, 9)
    ax.axis("off")

    C = {
        "elec":  "#AED6F1",
        "heat":  "#F9E79F",
        "cold":  "#A9DFBF",
        "h2":    "#D2B4DE",
        "stor":  "#F0F0F0",
        "fixed": "#D5DBDB",
    }

    def box(ax, x, y, w, h, color, label, fontsize=9, bold=False):
        patch = FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.1",
                               facecolor=color, edgecolor="gray", linewidth=1.2)
        ax.add_patch(patch)
        ax.text(x + w/2, y + h/2, label, ha="center", va="center",
                fontsize=fontsize, fontweight="bold" if bold else "normal",
                wrap=True)

    def arrow(ax, x1, y1, x2, y2, label="", color="gray", lw=1.5, style="-|>"):
        ax.annotate("", xy=(x2, y2), xytext=(x1, y1),
                    arrowprops=dict(arrowstyle=style, color=color, lw=lw))
        if label:
            mx, my = (x1+x2)/2, (y1+y2)/2
            ax.text(mx, my, label, ha="center", va="center", fontsize=7,
                    color=color, bbox=dict(fc="white", ec="none", pad=1))

    # ── Sector boxes ───────────────────────────────────────────────────────────
    box(ax,  0.3, 5.5, 13.4, 3.2, C["elec"],  "ELECTRIC SECTOR", 11, True)
    box(ax,  0.3, 2.5,  5.8, 2.7, C["heat"],  "HEAT SECTOR",     11, True)
    box(ax,  6.4, 2.5,  3.5, 2.7, C["cold"],  "COLD SECTOR",     11, True)
    box(ax, 10.2, 2.5,  3.5, 2.7, C["h2"],    "HYDROGEN SECTOR", 11, True)
    box(ax,  0.3, 0.1, 13.4, 2.1, C["fixed"], "FIXED BASELOAD (constant dispatch)", 10, True)

    # ── Variable generation (electric) ─────────────────────────────────────────
    gen_labels = ["Onshore\nWind", "Offshore\nWind", "Rooftop\nPV (res+com)",
                  "Utility\nPV", "CSP\nturbine", "Solar\nThermal →"]
    gen_x = [1.0, 2.7, 4.4, 6.1, 7.8, 9.5]
    for lbl, gx in zip(gen_labels, gen_x):
        color = C["heat"] if "Thermal" in lbl else C["elec"]
        box(ax, gx, 7.5, 1.4, 0.9, color, lbl, 7)

    # ── Electric storage ───────────────────────────────────────────────────────
    box(ax, 1.0, 5.8, 1.8, 1.4, C["stor"], "Battery\n(BATDISCH,\nSTORHBAT)", 7)
    box(ax, 3.1, 5.8, 1.8, 1.4, C["stor"], "PHS\n(STORHPHS)", 7)
    box(ax, 5.2, 5.8, 1.8, 1.4, C["stor"], "H2\nFuel Cell\n(FCDISCH)", 7)
    box(ax, 7.3, 5.8, 2.5, 1.4, C["stor"], "CSP thermal\nstorage\n(HCHARCSP – fixed)", 7)

    # ── Heat storage ───────────────────────────────────────────────────────────
    box(ax, 0.6, 2.8, 1.6, 1.2, C["stor"], "HW-STES\n(STORHHWAT)", 7)
    box(ax, 2.4, 2.8, 1.6, 1.2, C["stor"], "UTES\n(STORUGDYS)", 7)
    box(ax, 4.2, 2.8, 1.6, 1.2, C["stor"], "Heat Bat.\n(HBTDISCH,\nSTORHHBT)", 7)

    # ── Cold storage ───────────────────────────────────────────────────────────
    box(ax, 6.7, 2.8, 2.8, 1.2, C["stor"], "Cold TES\n(STORHCOLD)", 7)

    # ── H2 storage ─────────────────────────────────────────────────────────────
    box(ax, 10.5, 2.8, 2.8, 1.2, C["stor"], "H2 storage\n(STORHHFC,\nDAYH2STOR)", 7)

    # ── Fixed baseload technologies ────────────────────────────────────────────
    bl_labels = ["Hydro\n(SUPHYD2050)", "Tidal\n(SUPTID2050)",
                 "Wave\n(SUPWAV2050)", "Geo Electric\n(SUPGEL2050)",
                 "Geo Heat\n(SUPGHT2050)"]
    bl_x = [1.0, 3.4, 5.8, 8.2, 10.6]
    for lbl, bx in zip(bl_labels, bl_x):
        color = C["heat"] if "Heat" in lbl else C["fixed"]
        box(ax, bx, 0.3, 2.0, 1.6, color, lbl, 7)

    # ── Cross-sector arrows ────────────────────────────────────────────────────
    # Heat pump: Electric → Heat
    arrow(ax, 3.5, 5.5, 3.5, 5.2, "Heat pump\n(COP=4)", "firebrick")
    # AC: Electric → Cold
    arrow(ax, 8.0, 5.5, 7.7, 5.2, "AC\n(COP=3)", "teal")
    # Electrolyser: Electric → H2
    arrow(ax, 11.0, 5.5, 11.5, 5.2, "Electrolyser\n(FCCHARG)", "purple")
    # Fuel cell: H2 → Electric
    arrow(ax, 6.1, 6.5, 5.2, 6.5, "FC→elec", "purple")
    # Hydro etc → Electric
    for bx in bl_x[:4]:
        arrow(ax, bx+1.0, 2.0, bx+1.0, 5.5, "", "dimgray", lw=1)
    # Geo Heat → Heat
    arrow(ax, 11.6, 2.0, 3.0, 4.0, "Geo heat", "saddlebrown")

    fig.tight_layout()
    _save(fig, "fig_lp_system_diagram", save_dir)


def fig14_four_case_comparison(region: str, save_dir: Path):
    """Four-case comparison per region: baseline / LP-eval / GA-from-baseline / GA-from-LP.

    Reads the four JSON summary files.  Missing cases are shown as empty bars
    with a 'not yet run' annotation so the figure is still useful when only
    some cases exist.
    """
    cases = {
        "Baseline":         save_dir / "baseline_summary.json",
        "LP":               save_dir / "lp_summary.json",
        "GA (baseline)":    save_dir / "optimal_summary.json",
        "GA (LP)":          save_dir / "lp_ga_summary.json",
    }
    colors = ["#5D6D7E", "#A9CCE3", "#1A5276", "#2ECC71"]

    def _load(path):
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text())
        except Exception:
            return None

    data = {label: _load(path) for label, path in cases.items()}
    labels  = list(cases.keys())
    present = [d is not None for d in data.values()]
    if not any(present):
        print(f"  [SKIP] fig_four_case_comparison: no summary files found for {region}")
        return

    def _val(d, key, default=np.nan):
        if d is None:
            return np.nan
        return float(d.get(key, default) or default)

    costs   = [_val(data[l], "annual_cost_mn_bil_per_yr") for l in labels]
    wind    = [_val(data[l], "wind_twh")    for l in labels]
    solar   = [_val(data[l], "solar_twh")   for l in labels]
    hydro   = [_val(data[l], "hydro_twh")   for l in labels]
    curtail = [_val(data[l], "curtailment_twh") for l in labels]
    td_loss = [_val(data[l], "td_loss_twh") for l in labels]
    feasible= [bool((data[l] or {}).get("feasible", False)) for l in labels]

    x  = np.arange(len(labels))
    bw = 0.6

    fig, axes = plt.subplots(1, 3, figsize=(14, 5))

    # Panel A — system cost
    ax = axes[0]
    bars = ax.bar(x, costs, width=bw, color=colors, edgecolor="white")
    for i, (bar, c, feas) in enumerate(zip(bars, costs, feasible)):
        if np.isnan(c):
            ax.text(bar.get_x() + bw/2, 0.5, "not run", ha="center",
                    va="bottom", fontsize=7, color="gray", rotation=90)
        else:
            marker = "" if feas else " ✗"
            ax.text(bar.get_x() + bw/2, c + max([v for v in costs if not np.isnan(v)], default=1)*0.01,
                    f"${c:.0f}B{marker}", ha="center", va="bottom", fontsize=8)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=8)
    ax.set_ylabel("Annual system cost ($B/yr)")
    ax.set_title("A)", loc="left", fontweight="bold")
    ax.grid(True, alpha=0.3, axis="y")

    # Panel B — generation mix (stacked)
    ax = axes[1]
    bar_wind  = ax.bar(x, wind,  width=bw, label="Wind",  color="#5DADE2", edgecolor="white")
    bar_solar = ax.bar(x, solar, width=bw, label="Solar", color="#F4D03F", edgecolor="white",
                       bottom=wind)
    hydro_bot = [w + s for w, s in zip(
        [v if not np.isnan(v) else 0 for v in wind],
        [v if not np.isnan(v) else 0 for v in solar])]
    ax.bar(x, hydro, width=bw, label="Hydro+Geo", color="#27AE60", edgecolor="white",
           bottom=hydro_bot)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=8)
    ax.set_ylabel("Annual generation (TWh/yr)")
    ax.set_title("B)", loc="left", fontweight="bold")
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3, axis="y")

    # Panel C — losses
    ax = axes[2]
    bar_curt = ax.bar(x, curtail, width=bw, label="Curtailment", color="#E74C3C", edgecolor="white")
    td_bot = [v if not np.isnan(v) else 0 for v in curtail]
    ax.bar(x, td_loss, width=bw, label="T&D losses", color="#F0B27A", edgecolor="white",
           bottom=td_bot)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=8)
    ax.set_ylabel("Annual losses (TWh/yr)")
    ax.set_title("C)", loc="left", fontweight="bold")
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3, axis="y")

    fig.tight_layout()
    _save(fig, "fig14_four_case_comparison", save_dir)


# ══════════════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════════════

def main(region=None):
    all_regions = False
    lp_figures  = False
    if region is None:
        parser = argparse.ArgumentParser(description="Generate LoadMatch publication figures.")
        parser.add_argument("--region", default="UNITED-STATES",
                            help="Region name (default: UNITED-STATES)")
        parser.add_argument(
            "--all-regions", action="store_true", default=False,
            help="Only regenerate cross-region overview figures (figA1–A3) "
                 "from all completed runs. Skip per-region figures.",
        )
        parser.add_argument(
            "--lp-figures", action="store_true", default=False,
            help="Generate LP input data figure, LP system diagram, and "
                 "four-case comparison (baseline / LP / GA-baseline / GA-LP). "
                 "Used by the Snakemake plot_four_cases rule.",
        )
        args        = parser.parse_args()
        region      = args.region
        all_regions = args.all_regions
        lp_figures  = args.lp_figures

    if all_regions:
        print("Regenerating cross-region overview figures (figA1–A3)...")
        plot_all_regions(REPO_ROOT)
        return

    if lp_figures:
        save_dir = REPO_ROOT / "data" / "results_verification" / region
        save_dir.mkdir(parents=True, exist_ok=True)
        print(f"LP figures → {save_dir}\n")
        _try_fig("Input data",         fig_input_data,         region, save_dir)
        _try_fig("LP system diagram",  fig_lp_system_diagram,  save_dir)
        _try_fig("Fig 14 — Four-case comparison", fig14_four_case_comparison, region, save_dir)
        return

    print(f"Region    : {region}")
    print(f"Repo root : {REPO_ROOT}")

    # ── Load baseline GA data ─────────────────────────────────────────────────
    result = _load_ga_data(region, REPO_ROOT)
    if result[0] is None:
        print("Cannot proceed without factor_history.log — exiting.")
        sys.exit(1)
    df_ga, df_gen, df_best, best_row, n_gens, records, save_dir = result
    print(f"Figures → {save_dir}\n")

    # ── Load LP-GA data if available ──────────────────────────────────────────
    lp_ga_log = save_dir / "lp_ga_factor_history.log"
    df_ga_lp = df_gen_lp = df_best_lp = best_row_lp = n_gens_lp = None
    if lp_ga_log.exists():
        print("Loading LP-GA history...")
        res_lp = _load_ga_data(region, REPO_ROOT, log_name="lp_ga_factor_history.log")
        if res_lp[0] is not None:
            df_ga_lp, df_gen_lp, df_best_lp, best_row_lp, n_gens_lp = res_lp[:5]
            print(f"  LP-GA: {int(n_gens_lp)} gens, best=${best_row_lp['cost_mn_bil_per_year']:.2f}B/yr")
    else:
        print("  lp_ga_factor_history.log not found — LP-GA overlays skipped.")

    # ── Load LP factors if available ──────────────────────────────────────────
    lp_factors_path = REPO_ROOT / "data" / "results_python" / region / "fortran_factors.dat"
    lp_factors = {}
    if lp_factors_path.exists():
        raw = _read_dat(str(lp_factors_path))
        lp_factors = {k.upper(): float(v) for k, v in raw.items()}
        print(f"  LP factors loaded from {lp_factors_path.name} ({len(lp_factors)} params)")

    # ── Load baseline factors ─────────────────────────────────────────────────
    region_bl_path = REPO_ROOT / "data" / "raw" / f"baseline_results.{region}.dat"
    legacy_bl_path = REPO_ROOT / "data" / "raw" / "baseline_results.dat"
    if region_bl_path.exists():
        baseline_factors = load_baseline_start(region_bl_path)
        print(f"Baseline factors: loaded from {region_bl_path.name}")
    elif legacy_bl_path.exists() and region == "UNITED-STATES":
        baseline_factors = parse_baseline_factors(legacy_bl_path)
        print(f"Baseline factors: loaded from {legacy_bl_path.name}")
    else:
        baseline_factors = extract_fortran_region_defaults(region)
        print(f"Baseline factors: extracted from powerworld.f for '{region}'")

    # ── Build comparison table (needed by Fig 4) ──────────────────────────────
    def _ga(col):
        return float(best_row[col]) if col in best_row.index else PARAM_REGISTRY[col.upper()][0]
    def _bl(key):
        return baseline_factors.get(key, PARAM_REGISTRY[key][0])

    rows = []
    for key, (default_val, category, desc) in PARAM_REGISTRY.items():
        col    = key.lower()
        locked = category == "fixed"
        ga_val = default_val if locked else _ga(col)
        bl_val = _bl(key)
        rows.append({
            "Parameter": key, "Category": category, "Default": default_val,
            "Baseline": bl_val, "GA optimal": ga_val,
            "Change vs baseline (%)": 100 * (ga_val - bl_val) / max(abs(bl_val), 1e-12),
            "Locked": locked, "Description": desc,
        })
    df_compare = pd.DataFrame(rows)

    # ── Load Fortran cost data for all 4 cases ────────────────────────────────
    bl_costs, opt_costs, bl_energy, opt_energy, baseline_out = \
        _load_fortran_costs(region, save_dir, REPO_ROOT, best_row)

    lp_out     = save_dir / "fortran_lp_run.out"
    lp_ga_out  = save_dir / "fortran_lp_ga_run.out"
    lp_costs, lp_energy = {}, None
    lp_ga_costs, lp_ga_energy = {}, None
    if lp_out.exists():
        lp_costs, lp_energy = _parse_fortran_costs(lp_out.read_text())
        print(f"  LP Fortran output loaded: {lp_out.name}")
    if lp_ga_out.exists():
        lp_ga_costs, lp_ga_energy = _parse_fortran_costs(lp_ga_out.read_text())
        print(f"  LP-GA Fortran output loaded: {lp_ga_out.name}")

    # LP cost (for fig1 horizontal line)
    lp_total_cost = None
    _lp_json = save_dir / "lp_summary.json"
    if _lp_json.exists():
        try:
            _d = json.loads(_lp_json.read_text())
            lp_total_cost = _d.get("annual_cost_mn_bil_per_yr") or _d.get("cost_bn_per_yr")
        except Exception:
            pass

    # ── Generate figures ──────────────────────────────────────────────────────
    _try_fig("Fig  1 — Cost convergence",
             fig1_convergence, df_gen, save_dir,
             df_gen_lp=df_gen_lp, lp_cost=lp_total_cost)

    _try_fig("Fig  2 — Capacity-factor trajectories",
             fig2_capacity_factors, df_best, baseline_factors, n_gens, save_dir,
             df_best_lp=df_best_lp, lp_factors=lp_factors or None)

    _try_fig("Fig  3 — Non-capacity parameter trajectories",
             fig3_parameter_trajectories, df_best, baseline_factors, n_gens, save_dir,
             df_best_lp=df_best_lp, lp_factors=lp_factors or None)

    _try_fig("Fig  4 — Baseline vs GA comparison",
             fig4_baseline_vs_ga, df_compare, save_dir,
             lp_factors=lp_factors or None, best_row_lp=best_row_lp)

    _try_fig("Fig  5 — Cost distribution and capacity evolution",
             fig5_cost_and_capacity, df_ga, df_gen, df_best, best_row,
             baseline_factors, n_gens, records, save_dir,
             df_gen_lp=df_gen_lp, best_row_lp=best_row_lp,
             df_ga_lp=df_ga_lp, n_gens_lp=n_gens_lp)

    _try_fig("Fig  6 — Parameter CV (sensitivity)",
             fig6_parameter_cv, df_ga, n_gens, save_dir,
             df_ga_lp=df_ga_lp, n_gens_lp=n_gens_lp)

    _try_fig("Fig  7 — Generation and storage capacities",
             fig7_capacity_comparison, best_row, baseline_factors, save_dir,
             lp_factors=lp_factors or None, best_row_lp=best_row_lp)

    _try_fig("Fig  8 — Land area demand",
             fig8_land_area, best_row, baseline_factors, save_dir, region=region,
             lp_factors=lp_factors or None, best_row_lp=best_row_lp)

    _try_fig("Fig  9 — Cost breakdown",
             fig9_cost_breakdown, bl_costs, opt_costs, bl_energy, opt_energy,
             best_row, save_dir,
             lp_costs=lp_costs or None, lp_energy=lp_energy,
             lp_ga_costs=lp_ga_costs or None, lp_ga_energy=lp_ga_energy)

    _try_fig("Fig 10 — Capacity mix and ternary",
             fig10_capacity_mix, best_row, baseline_factors, save_dir, region,
             lp_factors=lp_factors or None, best_row_lp=best_row_lp)

    _try_fig("Fig 12 — Cost waterfall",
             fig12_cost_waterfall, bl_costs, opt_costs, bl_energy, opt_energy, save_dir,
             lp_costs=lp_costs or None, lp_energy=lp_energy,
             lp_ga_costs=lp_ga_costs or None, lp_ga_energy=lp_ga_energy)

    _try_fig("Fig 13a — Energy flow Sankey (baseline)",
             fig13_sankey, baseline_out, save_dir,
             scenario_label="baseline scenario",
             filename="fig13a_sankey_energy_flow_baseline")

    optimal_out = save_dir / "fortran_optimal_run.out"
    _try_fig("Fig 13b — Energy flow Sankey (GA-optimal)",
             fig13_sankey, optimal_out, save_dir,
             scenario_label="GA-optimised scenario",
             filename="fig13b_sankey_energy_flow_optimal")

    if lp_out.exists():
        _try_fig("Fig 13c — Energy flow Sankey (LP)",
                 fig13_sankey, lp_out, save_dir,
                 scenario_label="LP solution",
                 filename="fig13c_sankey_energy_flow_lp")

    if lp_ga_out.exists():
        _try_fig("Fig 13d — Energy flow Sankey (GA-from-LP)",
                 fig13_sankey, lp_ga_out, save_dir,
                 scenario_label="GA from LP solution",
                 filename="fig13d_sankey_energy_flow_ga_lp")

    # ── Overview figures across all regions ───────────────────────────────────
    plot_all_regions(REPO_ROOT)

    print(f"\nDone. All figures in: {save_dir}")


if __name__ == "__main__":
    main()

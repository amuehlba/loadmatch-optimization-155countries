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

# ── matplotlib publication defaults ───────────────────────────────────────────
plt.rcParams.update({
    "figure.dpi": 150,
    "savefig.dpi": 300,
    "font.size": 10,
    "axes.titlesize": 12,
    "axes.labelsize": 11,
    "xtick.labelsize": 9,
    "ytick.labelsize": 9,
    "legend.fontsize": 8,
    "figure.constrained_layout.use": True,
})


# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════

def _save(fig, stem, save_dir):
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

def fig1_convergence(df_gen, save_dir):
    fig, axes = plt.subplots(2, 1, figsize=(8, 6), sharex=True,
                             gridspec_kw={"height_ratios": [3, 1]})
    ax = axes[0]
    valid = df_gen[df_gen["cum_best_cost"] < float("inf")]
    ax.plot(valid["gen"], valid["cum_best_cost"], "k-", lw=1.8,
            label="Best feasible (cumulative)")
    gen_valid = df_gen[df_gen["gen_best_cost"] < float("inf")]
    ax.scatter(gen_valid["gen"], gen_valid["gen_best_cost"],
               s=18, c="tab:blue", alpha=0.5, zorder=3, label="Generation best")
    ax.set_ylabel(r"Annual system cost (\$B yr$^{-1}$)")
    ax.legend(loc="upper right")
    ax.set_title("(a)  Cost convergence")

    ax2 = axes[1]
    ax2.bar(df_gen["gen"], df_gen["feas_frac"] * 100, color="tab:green", alpha=0.7, width=0.8)
    ax2.set_ylabel("Feasible (%)")
    ax2.set_xlabel("Generation")
    ax2.set_ylim(0, 105)
    ax2.set_title("(b)  Feasibility rate per generation")

    _save(fig, "fig1_cost_convergence", save_dir)


# ══════════════════════════════════════════════════════════════════════════════
# Figure 2 — Capacity-factor trajectories
# ══════════════════════════════════════════════════════════════════════════════

def fig2_capacity_factors(df_best, baseline_factors, n_gens, save_dir):
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
    for col, label in CAP_LABELS.items():
        ax.plot(df_best["gen"], df_best[col], lw=1.4, label=label)
        bval = baseline_factors.get(col.upper())
        if bval is not None:
            ax.axhline(bval, color=ax.get_lines()[-1].get_color(), ls=":", lw=1.5, alpha=0.9)
    ax.set_xlabel("Generation")
    ax.set_ylabel("Scaling factor (–)")
    ax.legend(bbox_to_anchor=(1.01, 1), loc="upper left", borderaxespad=0,
              frameon=False, ncol=1)
    ax.set_title("Capacity-factor trajectories (best feasible per generation)")
    ax.set_ylim(bottom=0)
    ax.annotate("Dotted = baseline", xy=(0.01, 0.97), xycoords="axes fraction",
                fontsize=8, va="top", color="gray")
    _save(fig, "fig2_capacity_factors", save_dir)


# ══════════════════════════════════════════════════════════════════════════════
# Figure 3 — Non-capacity parameter trajectories (6 panels)
# ══════════════════════════════════════════════════════════════════════════════

def fig3_parameter_trajectories(df_best, baseline_factors, n_gens, save_dir):
    PANEL_GROUPS = {
        "(a) Storage duration (hours)": {
            "storhbat": "Battery", "storhphs": "PHS", "storhcold": "Cold TES",
            "storhhwat": "Hot-water TES", "hcharcsp": "CSP charge",
            "storhhfc": "H$_2$ elec.", "storhhbt": "Heat battery",
        },
        "(b) Storage duration (days)": {
            "storugdys": "UTES seasonal", "dayh2stor": "H$_2$ storage",
            "daybashyd": "Baseload hydro",
        },
        "(c) Power rates (TW)": {
            "batdisch": "Battery disch.", "fcdisch": "H$_2$ FC disch.",
            "fccharg": "Electrolyser", "hbtdisch": "Heat bat. disch.", "phsmin": "PHS minimum",
        },
        "(d) Fractions (0–1)": {
            "coolstes": "AC from CW-STES", "fheatflx": "Flex. heat",
            "fcoldflx": "Flex. cold", "frstorinit": "Init. storage fill",
            "fdistheat": "District heating", "frcihflex": "Flex. ind. heat",
        },
        "(e) Ratios and factors": {
            "cspstorgat": "CSP stor. ratio", "ugfac": "UTES rate fac.",
            "hwfac": "HW-STES rate fac.", "hpturbrat": "Hydro turb. ratio",
            "damcaprat": "Dam cap. ratio", "cperform": "Heat pump COP",
        },
        "(f) Demand response": {
            "mxhrdrm": "Max DR shift (h)",
        },
    }
    fig, axes = plt.subplots(3, 2, figsize=(18, 12))
    for ax, (panel_title, param_dict) in zip(axes.flatten(), PANEL_GROUPS.items()):
        for col, label in param_dict.items():
            if col in df_best.columns:
                ax.plot(df_best["gen"], df_best[col], lw=1.3, label=label)
                bval = baseline_factors.get(col.upper())
                if bval is not None:
                    ax.axhline(bval, color=ax.get_lines()[-1].get_color(),
                               ls=":", lw=1.2, alpha=0.9)
        ax.set_title(panel_title, fontsize=11, fontweight="bold")
        ax.set_xlabel("Generation")
        if ax.get_lines():
            ax.legend(bbox_to_anchor=(1.01, 1), loc="upper left", borderaxespad=0,
                      frameon=False, fontsize=7, ncol=1)
        ax.set_xlim(1, n_gens)
    fig.suptitle("Non-capacity parameter trajectories (best feasible per generation)\n"
                 "Dotted lines = baseline values", fontsize=13, fontweight="bold")
    _save(fig, "fig3_parameter_trajectories", save_dir)


# ══════════════════════════════════════════════════════════════════════════════
# Figure 4 — Baseline vs GA-optimised: final parameter comparison
# ══════════════════════════════════════════════════════════════════════════════

def fig4_baseline_vs_ga(df_compare, save_dir):
    df = df_compare.copy()
    df["bl_norm"] = 1.0
    df["ga_norm"] = df["GA optimal"] / df["Baseline"].replace(0, np.nan)
    zero_bl = df["Baseline"].abs() < 1e-12
    df.loc[zero_bl, "ga_norm"] = np.nan

    fig, ax = plt.subplots(figsize=(9, max(8, len(df) * 0.28)))
    y = np.arange(len(df))
    h = 0.35
    for i, (_, r) in enumerate(df.iterrows()):
        alpha_bl = 0.35 if r["Locked"] else 0.8
        alpha_ga = 0.35 if r["Locked"] else 0.8
        ax.barh(i + h/2, 1.0, h, color="steelblue", edgecolor="black",
                lw=0.4, alpha=alpha_bl,
                label="Baseline" if i == 0 else "_")
        ga_v = r["ga_norm"] if not np.isnan(r["ga_norm"]) else 0
        col = "lightgray" if r["Locked"] else ("coral" if ga_v <= 1.0 else "salmon")
        ax.barh(i - h/2, ga_v, h, color=col, edgecolor="black",
                lw=0.4, alpha=alpha_ga,
                label="GA optimised" if i == 0 else
                      ("Fixed (not optimised)" if r["Locked"] and i == next(
                          (j for j, (_, rr) in enumerate(df.iterrows()) if rr["Locked"]), -1)
                       else "_"))
    ax.axvline(1.0, color="black", ls="--", lw=0.8, alpha=0.5)
    ax.set_yticks(y)
    ax.set_yticklabels(
        [f"{r['Parameter']}  ({r['Category']})" + ("  [fixed]" if r["Locked"] else "")
         for _, r in df.iterrows()],
        fontsize=7.5)
    ax.set_xlabel("Ratio to baseline value")
    ax.set_title("Parameter values: GA-optimised / baseline\n"
                 "(1.0 = unchanged; gray = fixed, not optimised)")
    ax.legend(
        handles=[
            Patch(facecolor="steelblue", alpha=0.8, label="Baseline"),
            Patch(facecolor="coral",     alpha=0.8, label="GA optimised"),
            Patch(facecolor="lightgray", alpha=0.8, label="Fixed (not optimised)"),
        ],
        bbox_to_anchor=(1.01, 1), loc="upper left", borderaxespad=0, frameon=False, ncol=1)
    ax.invert_yaxis()
    ga_vals = df["ga_norm"].fillna(0).values
    for i, (_, r) in enumerate(df.iterrows()):
        if r["Locked"]:
            continue
        ga_str = f"{r['GA optimal']:.3g}"
        pct = r["Change vs baseline (%)"]
        ax.annotate(f"{ga_str}  ({pct:+.0f}%)",
                    xy=(max(ga_vals[i], 0) + 0.02, i - h/2),
                    fontsize=7, va="center", color="dimgray")
    _save(fig, "fig4_baseline_vs_ga", save_dir)


# ══════════════════════════════════════════════════════════════════════════════
# Figure 5 — Cost distribution and capacity evolution
# ══════════════════════════════════════════════════════════════════════════════

def _to_gw(record, factor_col, base_key):
    return record.get(factor_col, 0.0) * BASE_CAPACITIES_USA[base_key] / 1000


def fig5_cost_and_capacity(df_ga, df_gen, df_best, best_row, baseline_factors, n_gens,
                            records, save_dir):
    fig, axes = plt.subplots(2, 2, figsize=(15, 11))
    ax_a, ax_b = axes[0, 0], axes[0, 1]
    ax_c, ax_d = axes[1, 0], axes[1, 1]

    # A) Population cost distribution per generation
    gen_costs, gen_positions = [], []
    for g in range(1, int(n_gens) + 1):
        feas = df_ga[(df_ga["gen"] == g) & (df_ga["feasible"] == True)]["cost_mn_bil_per_year"]
        feas = feas[feas < float("inf")]
        if len(feas) >= 2:
            gen_costs.append(feas.values)
            gen_positions.append(g)
    ax_a.boxplot(gen_costs, positions=gen_positions, widths=0.6, patch_artist=True,
                 showfliers=False, boxprops=dict(facecolor="lightblue", alpha=0.7),
                 medianprops=dict(color="navy", lw=1.5))
    valid = df_gen[df_gen["cum_best_cost"] < float("inf")]
    ax_a.plot(valid["gen"], valid["cum_best_cost"], "r-", lw=2, label="Cumulative best", zorder=5)
    ax_a.set_xlabel("Generation")
    ax_a.set_ylabel(r"Annual system cost (\$B yr$^{-1}$)")
    ax_a.set_title("A)", loc="left", fontweight="bold")
    ax_a.legend(loc="upper right")
    ax_a.set_xlim(0.5, n_gens + 0.5)
    tick_gens = list(range(10, int(n_gens) + 1, 10))
    ax_a.set_xticks(tick_gens)
    ax_a.set_xticklabels([str(g) for g in tick_gens])

    # B) Capacity evolution stacked bar at milestones
    milestones = [("LP (initial)", records[0])]
    for r in records:
        if r.get("label", "").startswith("inflate") and r.get("feasible"):
            milestones.append(("First feasible", r))
            break
    for g in range(10, int(n_gens) + 1, 10):
        gen_df = df_ga[(df_ga["gen"] == g) & (df_ga["feasible"] == True)]
        if len(gen_df):
            row = gen_df.loc[gen_df["cost_mn_bil_per_year"].idxmin()]
            milestones.append((f"Gen {g}", row.to_dict()))
    milestones.append(("GA optimal", best_row.to_dict()))

    n_ms = len(milestones)
    x = np.arange(n_ms)
    cmap_b = plt.cm.Set2(np.linspace(0, 1, len(FACTOR_TO_BASE)))
    bottom = np.zeros(n_ms)
    for j, (fcol, (bkey, tname)) in enumerate(FACTOR_TO_BASE.items()):
        caps = np.array([_to_gw(ms[1], fcol, bkey) for ms in milestones])
        ax_b.bar(x, caps, 0.55, bottom=bottom, label=tname,
                 color=cmap_b[j], edgecolor="white", lw=0.4)
        bottom += caps
    ax_b.set_ylabel("Total capacity (GW)")
    ax_b.set_xticks(x)
    ax_b.set_xticklabels([ms[0] for ms in milestones], rotation=30, ha="right")
    ax_b.legend(bbox_to_anchor=(1.05, 1), loc="upper left", borderaxespad=0,
                frameon=False, ncol=1, fontsize=7)
    ax_b.set_ylim(bottom=0)
    ax_b2 = ax_b.twinx()
    costs_ms = [ms[1].get("cost_mn_bil_per_year", float("inf")) for ms in milestones]
    vi = [i for i, c in enumerate(costs_ms) if c < float("inf")]
    vc = [costs_ms[i] for i in vi]
    ax_b2.plot(vi, vc, "rD-", lw=2, ms=8, zorder=10)
    for i, c in zip(vi, vc):
        ax_b2.annotate(f"${c:.0f}B", (i, c), textcoords="offset points",
                       xytext=(0, 7), ha="center", fontsize=7, color="red", fontweight="bold")
    ax_b2.set_ylabel(r"Annual cost (\$B yr$^{-1}$)", color="red")
    ax_b2.tick_params(axis="y", labelcolor="red")
    ax_b.set_title("B)", loc="left", fontweight="bold")

    # C) Storage power capacity trajectories
    STORAGE_POWER_COLS = {
        "batdisch": "Battery", "fcdisch": "H\u2082 FC",
        "fccharg": "Electrolyser", "hbtdisch": "Heat battery",
    }
    for col, label in STORAGE_POWER_COLS.items():
        if col in df_best.columns:
            (line,) = ax_c.plot(df_best["gen"], df_best[col], lw=1.4, label=label)
            bval = baseline_factors.get(col.upper())
            if bval is not None:
                ax_c.axhline(bval, color=line.get_color(), ls=":", lw=1.5, alpha=0.9)
    ax_c.set_xlabel("Generation")
    ax_c.set_ylabel("Power capacity (TW)")
    ax_c.set_title("C)", loc="left", fontweight="bold")
    ax_c.legend(bbox_to_anchor=(1.01, 1), loc="upper left", borderaxespad=0,
                frameon=False, fontsize=8, ncol=1)
    ax_c.set_xlim(1, n_gens)
    ax_c.set_ylim(bottom=0)
    ax_c.annotate("Dotted = baseline", xy=(0.01, 0.97), xycoords="axes fraction",
                  fontsize=8, va="top", color="gray")

    # D) Storage energy capacity trajectories
    STORAGE_ENERGY_FNS = {
        "Battery":           ("batdisch", "storhbat",   1),
        "H\u2082 long-term": ("fcdisch",  "dayh2stor", 24),
        "Heat battery":      ("hbtdisch", "storhhbt",   1),
    }
    for label, (col_pow, col_dur, scale) in STORAGE_ENERGY_FNS.items():
        if col_pow in df_best.columns and col_dur in df_best.columns:
            vals = df_best[col_pow] * df_best[col_dur] * scale
            (line,) = ax_d.plot(df_best["gen"], vals, lw=1.4, label=label)
            bval_e = (baseline_factors.get(col_pow.upper(), PARAM_REGISTRY[col_pow.upper()][0]) *
                      baseline_factors.get(col_dur.upper(), PARAM_REGISTRY[col_dur.upper()][0]) * scale)
            if bval_e > 0:
                ax_d.axhline(bval_e, color=line.get_color(), ls=":", lw=1.5, alpha=0.9)
    ax_d.set_xlabel("Generation")
    ax_d.set_ylabel("Energy capacity (TWh)")
    ax_d.set_xlim(1, n_gens)
    ax_d.set_ylim(bottom=0)
    ax_d.set_title("D)", loc="left", fontweight="bold")
    ax_d2 = ax_d.twinx()
    if "storhhfc" in df_best.columns:
        (line_hfc,) = ax_d2.plot(df_best["gen"], df_best["storhhfc"],
                                  color="gray", ls="--", lw=1.3,
                                  label="H\u2082 FC stor. (h)")
        bl_hfc = baseline_factors.get("STORHHFC", PARAM_REGISTRY["STORHHFC"][0])
        if bl_hfc > 0:
            ax_d2.axhline(bl_hfc, color="gray", ls=":", lw=1.5, alpha=0.9)
        ax_d2.set_ylabel("H\u2082 FC storage duration (h)", color="gray")
        ax_d2.tick_params(axis="y", labelcolor="gray")
        ax_d2.set_ylim(bottom=0)
    h1, l1 = ax_d.get_legend_handles_labels()
    h2, l2 = ax_d2.get_legend_handles_labels()
    ax_d.legend(h1 + h2, l1 + l2, bbox_to_anchor=(1.08, 1), loc="upper left",
                borderaxespad=0, frameon=False, fontsize=8, ncol=1)
    ax_d.annotate("Dotted = baseline", xy=(0.01, 0.97), xycoords="axes fraction",
                  fontsize=8, va="top", color="gray")

    _save(fig, "fig5_cost_and_capacity", save_dir)


# ══════════════════════════════════════════════════════════════════════════════
# Figure 6 — Parameter sensitivity: CV across feasible GA population
# ══════════════════════════════════════════════════════════════════════════════

def fig6_parameter_cv(df_ga, n_gens, save_dir):
    last_gen = df_ga[(df_ga["gen"] == int(n_gens)) & (df_ga["feasible"] == True)]
    cols = [c for c in FACTOR_COLUMNS if c in last_gen.columns and c.upper() not in DEFAULT_LOCKED]
    cv_data = []
    for col in cols:
        vals = last_gen[col].dropna()
        mean_ = vals.mean()
        std_  = vals.std()
        cv = std_ / abs(mean_) * 100 if abs(mean_) > 1e-12 else 0.0
        cv_data.append({"Parameter": col.upper(), "CV (%)": cv, "Mean": mean_, "Std": std_})
    df_cv = pd.DataFrame(cv_data).sort_values("CV (%)", ascending=True)

    fig, ax = plt.subplots(figsize=(7, 9))
    colors = ["tab:red" if cv > 20 else "tab:orange" if cv > 5 else "tab:green"
              for cv in df_cv["CV (%)"].values]
    ax.barh(range(len(df_cv)), df_cv["CV (%)"].values, color=colors, edgecolor="black", lw=0.3)
    ax.set_yticks(range(len(df_cv)))
    ax.set_yticklabels(df_cv["Parameter"].values, fontsize=8)
    ax.set_xlabel("Coefficient of variation (%)")
    ax.set_title(f"Parameter spread in final generation (gen {int(n_gens)}, "
                 f"n={len(last_gen)} feasible)")
    ax.invert_yaxis()
    ax.legend(handles=[
        Patch(facecolor="tab:green", label="CV < 5% (well constrained)"),
        Patch(facecolor="tab:orange", label="5% ≤ CV ≤ 20%"),
        Patch(facecolor="tab:red", label="CV > 20% (loosely constrained)"),
    ], loc="lower right", fontsize=8)
    _save(fig, "fig6_parameter_sensitivity", save_dir)


# ══════════════════════════════════════════════════════════════════════════════
# Figure 7 — GA-optimal vs baseline: generation and storage capacities
# ══════════════════════════════════════════════════════════════════════════════

def fig7_capacity_comparison(best_row, baseline_factors, save_dir):
    def _ga(col):
        return float(best_row[col]) if col in best_row.index else PARAM_REGISTRY[col.upper()][0]
    def _bl(key):
        return baseline_factors.get(key, PARAM_REGISTRY[key][0])

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
    gen_ga_gw  = [_ga(it[1]) * BASE_CAPACITIES_USA[it[2]] / 1000 for it in gen_items]
    gen_bl_gw  = [_bl(it[1].upper()) * BASE_CAPACITIES_USA[it[2]] / 1000 for it in gen_items]

    stor_pow_items = [
        ("Battery",      "BATDISCH"),
        ("H\u2082 FC",   "FCDISCH"),
        ("Electrolyser", "FCCHARG"),
        ("Heat battery", "HBTDISCH"),
        ("PHS (min.)",   "PHSMIN"),
    ]
    sp_labels = [it[0] for it in stor_pow_items]
    sp_ga     = [_ga(it[1].lower()) for it in stor_pow_items]
    sp_bl     = [_bl(it[1])         for it in stor_pow_items]

    stor_ene_items = [
        ("Battery",           "batdisch", "storhbat",   1),
        ("H\u2082 long-term", "fcdisch",  "dayh2stor", 24),
        ("Heat battery",      "hbtdisch", "storhhbt",   1),
    ]
    se_labels = [it[0] for it in stor_ene_items]
    se_ga = [_ga(it[1]) * _ga(it[2]) * it[3] for it in stor_ene_items]
    se_bl = [_bl(it[1].upper()) * _bl(it[2].upper()) * it[3] for it in stor_ene_items]

    fig, (ax1, ax2, ax3) = plt.subplots(1, 3, figsize=(16, 6))
    w = 0.38
    col_bl = "steelblue"
    col_ga = "coral"

    def _grouped_bars(ax, labels, bl_vals, ga_vals, ylabel, title):
        x = np.arange(len(labels))
        ax.bar(x - w/2, bl_vals, w, label="Baseline",   color=col_bl, alpha=0.85,
               edgecolor="black", lw=0.5)
        ax.bar(x + w/2, ga_vals, w, label="GA optimal", color=col_ga, alpha=0.85,
               edgecolor="black", lw=0.5)
        ax.set_xticks(x)
        ax.set_xticklabels(labels, rotation=35, ha="right", fontsize=8)
        ax.set_ylabel(ylabel)
        ax.set_title(title, loc="left", fontweight="bold")
        ax.set_ylim(bottom=0)
        for i, (bv, gv) in enumerate(zip(bl_vals, ga_vals)):
            if bv > 1e-9:
                pct = 100 * (gv - bv) / bv
                ax.annotate(f"{'+' if pct>=0 else ''}{pct:.0f}%",
                            xy=(i + w/2, gv), xytext=(0, 3), textcoords="offset points",
                            ha="center", va="bottom", fontsize=7, color="dimgray")

    _grouped_bars(ax1, gen_labels, gen_bl_gw, gen_ga_gw, "Capacity (GW)", "A)  Generation")
    _grouped_bars(ax2, sp_labels, sp_bl, sp_ga, "Power capacity (TW)", "B)  Storage power")
    _grouped_bars(ax3, se_labels, se_bl, se_ga, "Energy capacity (TWh)", "C)  Storage energy")

    ax3_r = ax3.twinx()
    x_hfc = len(se_labels)
    bl_hfc_h = _bl("STORHHFC")
    ga_hfc_h = _ga("storhhfc")
    ax3_r.bar(x_hfc - w/2, bl_hfc_h, w, color=col_bl, alpha=0.5, edgecolor="black", lw=0.5, hatch="//")
    ax3_r.bar(x_hfc + w/2, ga_hfc_h, w, color=col_ga, alpha=0.5, edgecolor="black", lw=0.5, hatch="//")
    ax3_r.set_ylabel("H\u2082 FC storage duration (h)", color="gray")
    ax3_r.tick_params(axis="y", labelcolor="gray")
    ax3_r.set_ylim(bottom=0)
    ax3.set_xticks(list(range(len(se_labels) + 1)))
    ax3.set_xticklabels(se_labels + ["H\u2082 FC (h)"], rotation=35, ha="right", fontsize=8)
    ax3.set_xlim(-0.6, x_hfc + 0.6)

    # Single shared legend beneath all subplots
    leg_handles = [
        Patch(facecolor=col_bl, alpha=0.85, edgecolor="black", lw=0.5, label="Baseline"),
        Patch(facecolor=col_ga, alpha=0.85, edgecolor="black", lw=0.5, label="GA optimal"),
    ]
    fig.legend(handles=leg_handles, loc="lower center", ncol=2, frameon=False,
               fontsize=10, bbox_to_anchor=(0.5, -0.02))

    fig.suptitle("GA-optimal vs baseline: generation and storage capacities",
                 fontsize=13, fontweight="bold")
    _save(fig, "fig7_capacity_comparison", save_dir)


# ══════════════════════════════════════════════════════════════════════════════
# Figure 8 — Land area demand
# ══════════════════════════════════════════════════════════════════════════════

def fig8_land_area(best_row, baseline_factors, save_dir, region="UNITED-STATES"):
    region_label = region.replace("-", " ").title()
    def _ga(col):
        return float(best_row[col]) if col in best_row.index else PARAM_REGISTRY[col.upper()][0]
    def _bl(key):
        return baseline_factors.get(key, PARAM_REGISTRY[key][0])

    area_items = [
        ("Onshore wind",  "faconwin",   "onshore_wind"),
        ("Offshore wind", "facoffwin",  "offshore_wind"),
        ("Res. PV",       "facrespv",   "res_rooftop_pv"),
        ("Com. PV",       "faccompv",   "com_rooftop_pv"),
        ("Utility PV",    "facutilpv",  "utility_pv"),
        ("CSP",           "cspturbfac", "csp"),
        ("Solar thermal", "facsht",     "solar_thermal"),
    ]

    def _area_km2(factor_val, base_key):
        return factor_val * BASE_CAPACITIES_USA[base_key] * POWER_DENSITY_KM2_PER_MW[base_key]

    labels    = [it[0] for it in area_items]
    base_keys = [it[2] for it in area_items]
    ga_areas  = [_area_km2(_ga(it[1]), it[2]) for it in area_items]
    bl_areas  = [_area_km2(_bl(it[1].upper()), it[2]) for it in area_items]
    ltype     = [LAND_TYPE[it[2]] for it in area_items]

    ga_footprint = sum(a for a, lt in zip(ga_areas, ltype) if lt == "footprint")
    bl_footprint = sum(a for a, lt in zip(bl_areas, ltype) if lt == "footprint")
    ga_total = ga_footprint + FIXED_TOTAL_KM2
    bl_total = bl_footprint + FIXED_TOTAL_KM2
    ga_spacing = sum(a for a, lt in zip(ga_areas, ltype) if lt == "spacing")
    bl_spacing = sum(a for a, lt in zip(bl_areas, ltype) if lt == "spacing")

    LAND_TYPE_COLORS = {
        "spacing": "tab:blue", "offshore": "tab:cyan",
        "rooftop": "tab:green", "footprint": "tab:orange",
    }

    fig, (ax_a, ax_b) = plt.subplots(1, 2, figsize=(14, 6))
    w = 0.38
    x = np.arange(len(labels))

    # A) Per-technology (% of land area)
    seen_lt = set()
    for xi, (lbl, bla, gaa, lt) in enumerate(zip(labels, bl_areas, ga_areas, ltype)):
        color = LAND_TYPE_COLORS[lt]
        ax_a.bar(xi - w/2, bla / US_LAND_AREA_KM2 * 100, w, color=color,
                 alpha=0.5, edgecolor="black", lw=0.5,
                 label=lt.capitalize() if lt not in seen_lt else "_")
        ax_a.bar(xi + w/2, gaa / US_LAND_AREA_KM2 * 100, w, color=color,
                 alpha=0.95, edgecolor="black", lw=0.5)
        seen_lt.add(lt)
    ax_a.set_xticks(x)
    ax_a.set_xticklabels(labels, rotation=30, ha="right", fontsize=9)
    ax_a.set_ylabel(f"Land area (% of {region_label} land)")
    ax_a.set_title(f"A)  Per-technology land use (% of {region_label} land)",
                   loc="left", fontweight="bold")
    land_type_handles = [h for h in ax_a.get_legend_handles_labels()[0]]
    land_type_handles += [
        Patch(facecolor="gray", alpha=0.5,  edgecolor="black", lw=0.5, label="Baseline"),
        Patch(facecolor="gray", alpha=0.95, edgecolor="black", lw=0.5, label="GA optimal"),
    ]
    ax_a.legend(handles=land_type_handles,
                bbox_to_anchor=(1.01, 1), loc="upper left", borderaxespad=0,
                frameon=False, fontsize=8, ncol=1)

    # B) Stacked totals (% of land area) — footprint only (LANDALLTECH convention)
    stacks = [
        ("Fixed infra", FIXED_TOTAL_KM2, FIXED_TOTAL_KM2, "#A0A0A0"),
        ("Utility PV",
         _area_km2(_bl("FACUTILPV"), "utility_pv"),
         _area_km2(_ga("facutilpv"), "utility_pv"), "#FF9900"),
        ("CSP",
         _area_km2(_bl("CSPTURBFAC"), "csp"),
         _area_km2(_ga("cspturbfac"), "csp"), "#FF6600"),
        ("Solar thermal",
         _area_km2(_bl("FACSHT"), "solar_thermal"),
         _area_km2(_ga("facsht"), "solar_thermal"), "#FFC000"),
    ]
    bot_b, bot_g = 0.0, 0.0
    for name, bv, gv, c in stacks:
        ax_b.bar(0, bv / US_LAND_AREA_KM2 * 100, 0.5, bottom=bot_b / US_LAND_AREA_KM2 * 100,
                 color=c, alpha=0.6, edgecolor="black", lw=0.5, label=name)
        ax_b.bar(1, gv / US_LAND_AREA_KM2 * 100, 0.5, bottom=bot_g / US_LAND_AREA_KM2 * 100,
                 color=c, alpha=0.95, edgecolor="black", lw=0.5)
        bot_b += bv
        bot_g += gv
    ax_b.set_xticks([0, 1])
    ax_b.set_xticklabels(["Baseline", "GA optimal"], fontsize=10)
    ax_b.set_ylabel(f"Land footprint (% of {region_label} land)")
    ax_b.set_title(f"B)  Total LANDALLTECH footprint (% of {region_label} land)",
                   loc="left", fontweight="bold")
    ax_b.annotate(f"BL: {bl_total/US_LAND_AREA_KM2*100:.2f}%\n({bl_total:,.0f} km²)",
                  xy=(0, bl_total/US_LAND_AREA_KM2*100), xytext=(0.15, 0.2),
                  textcoords=("axes fraction", "axes fraction"),
                  ha="left", fontsize=9)
    ax_b.annotate(f"GA: {ga_total/US_LAND_AREA_KM2*100:.2f}%\n({ga_total:,.0f} km²)",
                  xy=(1, ga_total/US_LAND_AREA_KM2*100), xytext=(0.75, 0.2),
                  textcoords=("axes fraction", "axes fraction"),
                  ha="left", fontsize=9)
    ax_b.legend(bbox_to_anchor=(1.01, 1), loc="upper left", borderaxespad=0,
                frameon=False, fontsize=8, ncol=1)

    fig.suptitle(f"Land area demand: baseline vs GA-optimal (LANDALLTECH convention)",
                 fontsize=12, fontweight="bold")
    _save(fig, "fig8_area_comparison", save_dir)


# ══════════════════════════════════════════════════════════════════════════════
# Figure 9 — Cost breakdown: GA-optimised vs baseline
# ══════════════════════════════════════════════════════════════════════════════

def fig9_cost_breakdown(bl_costs, opt_costs, bl_energy, opt_energy, best_row, save_dir):
    if not opt_costs or not bl_costs:
        _skip("Fig 9", "cost data not available (need both Fortran output files)")
        return

    def _group_bil(costs, energy_twh):
        return {grp: sum(costs.get(lbl, 0.0) for lbl in lbls) * energy_twh / 100
                for grp, lbls in COST_GROUPS.items()}

    bl_grp  = _group_bil(bl_costs,  bl_energy)
    opt_grp = _group_bil(opt_costs, opt_energy)
    groups  = list(COST_GROUPS.keys())
    bl_vals = [bl_grp[g]  for g in groups]
    opt_vals = [opt_grp[g] for g in groups]
    colors   = [GROUP_COLORS[g] for g in groups]
    x = np.arange(len(groups))
    w = 0.38

    bl_sum  = sum(bl_vals)
    opt_sum = sum(opt_vals)
    savings = bl_sum - opt_sum

    fig, (ax_a, ax_b) = plt.subplots(1, 2, figsize=(14, 6))

    for i, (bv, ov, c) in enumerate(zip(bl_vals, opt_vals, colors)):
        ax_a.bar(i - w/2, bv, w, color=c, alpha=0.55, edgecolor="black", lw=0.5)
        ax_a.bar(i + w/2, ov, w, color=c, alpha=0.95, edgecolor="black", lw=0.5)
        if bv > 1e-3:
            pct = 100 * (ov - bv) / bv
            ax_a.annotate(f"{'+' if pct>=0 else ''}{pct:.1f}%",
                          xy=(i + w/2, ov), xytext=(4, 3), textcoords="offset points",
                          ha="left", va="bottom", fontsize=6, color="dimgray")
    ax_a.set_xticks(x)
    ax_a.set_xticklabels(groups, rotation=30, ha="right", fontsize=9)
    ax_a.set_ylabel(r"Annual cost (\$BIL yr$^{-1}$)")
    ax_a.set_title("A)  Cost breakdown per category", loc="left", fontweight="bold")
    ax_a.set_ylim(bottom=0)
    legend_elems = (
        [Patch(facecolor=GROUP_COLORS[g], label=g) for g in groups]
        + [Patch(facecolor="gray", alpha=0.55, edgecolor="black", lw=0.5, label="Baseline"),
           Patch(facecolor="gray", alpha=0.95, edgecolor="black", lw=0.5, label="GA optimal")]
    )
    ax_a.legend(handles=legend_elems,
                bbox_to_anchor=(1.01, 1), loc="upper left", borderaxespad=0,
                frameon=False, fontsize=8, ncol=1)

    bot = np.zeros(2)
    for g, c in zip(groups, colors):
        vals = np.array([bl_grp[g], opt_grp[g]])
        ax_b.bar([0, 1], vals, 0.5, bottom=bot, color=c, alpha=0.85,
                 edgecolor="black", lw=0.5, label=g)
        bot += vals
    for xi, tot in enumerate([bl_sum, opt_sum]):
        ax_b.annotate(f"${tot:.1f}B/yr", xy=(xi, tot), xytext=(0, 6),
                      textcoords="offset points", ha="center", va="bottom",
                      fontsize=10, fontweight="bold")
    ax_b.set_ylim(0, max(bl_sum, opt_sum) * 1.18)
    ax_b.annotate(f"Savings: ${savings:.1f}B/yr\n({100*savings/bl_sum:.1f}%)",
                  xy=(0.5, 0.97), xycoords="axes fraction", ha="center", va="top",
                  fontsize=9, color="dimgray",
                  bbox=dict(boxstyle="round,pad=0.3", fc="white", alpha=0.8))
    ax_b.set_xticks([0, 1])
    ax_b.set_xticklabels(["Baseline", "GA optimal"], fontsize=10)
    ax_b.set_ylabel(r"Annual system cost (\$BIL yr$^{-1}$)")
    ax_b.set_title("B)  Total cost by category", loc="left", fontweight="bold")
    ax_b.legend(bbox_to_anchor=(1.01, 1), loc="upper left", borderaxespad=0,
                frameon=False, fontsize=8, ncol=1)

    fig.suptitle("Annual system cost: GA-optimised vs baseline",
                 fontsize=12, fontweight="bold")
    _save(fig, "fig9_cost_comparison", save_dir)


# ══════════════════════════════════════════════════════════════════════════════
# Figure 10 — Generation capacity mix and ternary diagram
# ══════════════════════════════════════════════════════════════════════════════

def fig10_capacity_mix(best_row, baseline_factors, save_dir):
    def _ga(col):
        return float(best_row[col]) if col in best_row.index else PARAM_REGISTRY[col.upper()][0]
    def _bl(key):
        return baseline_factors.get(key, PARAM_REGISTRY[key][0])

    def _build_cap_2050(fac_fn):
        return {
            "onshore_wind":  fac_fn("faconwin")   * BASE_CAPACITIES_USA["onshore_wind"],
            "offshore_wind": fac_fn("facoffwin")  * BASE_CAPACITIES_USA["offshore_wind"],
            "res_pv":        fac_fn("facrespv")   * BASE_CAPACITIES_USA["res_rooftop_pv"],
            "com_pv":        fac_fn("faccompv")   * BASE_CAPACITIES_USA["com_rooftop_pv"],
            "utility_pv":    fac_fn("facutilpv")  * BASE_CAPACITIES_USA["utility_pv"],
            "csp":           fac_fn("cspturbfac") * BASE_CAPACITIES_USA["csp"],
            **FIXED_2050_MW,
        }

    FOSSIL_2020_MW = TOTAL_2020_ALL_MW - sum(CAP_2020_MW.values())
    bl_cap_2050 = _build_cap_2050(lambda k: _bl(k.upper()))
    ga_cap_2050 = _build_cap_2050(lambda k: _ga(k))
    tot_bl_2050 = sum(bl_cap_2050.values())
    tot_ga_2050 = sum(ga_cap_2050.values())

    _S3 = np.sqrt(3)

    def _t2c(wind, solar, water):
        tot = wind + solar + water
        w, s = wind / tot, solar / tot
        return s + w * 0.5, w * _S3 / 2

    fig, (ax_a, ax_b, ax_c) = plt.subplots(1, 3, figsize=(21, 7))

    # A) Absolute capacity stacked bars
    scenarios_bar = [
        ("2020\n(incl. fossil)",  TOTAL_2020_ALL_MW, {**CAP_2020_MW, "fossil": FOSSIL_2020_MW}),
        ("Baseline\n2050",        tot_bl_2050,        bl_cap_2050),
        ("GA optimal\n2050",      tot_ga_2050,        ga_cap_2050),
    ]
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
    ax_a.set_title("A)  Generation capacity mix", loc="left", fontweight="bold")
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

    pts_scatter = [
        ("2020",            CAP_2020_MW, TOTAL_2020_ALL_MW, "o", "black",     80),
        ("Baseline 2050",   bl_cap_2050, tot_bl_2050,       "s", "steelblue", 90),
        ("GA optimal 2050", ga_cap_2050, tot_ga_2050,       "^", "coral",     90),
    ]
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

    annot_offsets = [(5, 8), (5, 8), (5, 8)]
    for (label, cap, total, marker, color, ms), (dx, dy) in zip(pts_scatter, annot_offsets):
        w_, s_ = _wind_solar_shares(cap, total)
        ax_b.scatter(s_, w_, marker=marker, color=color, s=ms,
                     edgecolors="black", lw=0.8, zorder=5, label=label)
        ax_b.annotate(
            f"{label}\nSolar {s_:.1f}%  Wind {w_:.1f}%\nTotal {s_+w_:.1f}%",
            xy=(s_, w_), xytext=(dx, dy), textcoords="offset points", fontsize=7.5,
            bbox=dict(boxstyle="round,pad=0.25", fc="white", alpha=0.8, lw=0),
            arrowprops=dict(arrowstyle="-", color="gray", lw=0.6),
        )
    ax_b.set_xlabel("Solar share of total installed capacity (%)")
    ax_b.set_ylabel("Wind share of total installed capacity (%)")
    ax_b.set_xlim(0, x_max)
    ax_b.set_ylim(0, y_max)
    ax_b.set_title("B)  Wind vs solar share (% of total capacity)", loc="left", fontweight="bold")
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

    for label, cap, marker, color, ms, (dx, dy) in [
        ("Baseline 2050",   bl_cap_2050, "s", "steelblue", 90, ( 8,  8)),
        ("GA optimal 2050", ga_cap_2050, "^", "coral",     90, ( 8,  8)),
    ]:
        wind_  = cap.get("onshore_wind", 0) + cap.get("offshore_wind", 0)
        solar_ = (cap.get("res_pv", 0) + cap.get("com_pv", 0) +
                  cap.get("utility_pv", 0) + cap.get("csp", 0))
        water_ = cap.get("hydro", 0) + cap.get("wave", 0) + cap.get("tidal", 0)
        tot_   = wind_ + solar_ + water_
        xp, yp = _t2c(wind_, solar_, water_)
        ax_c.scatter(xp, yp, marker=marker, color=color, s=ms,
                     edgecolors="black", lw=0.8, zorder=5, label=label)
        ax_c.annotate(
            f"{label}\nW {wind_/tot_*100:.1f}%  S {solar_/tot_*100:.1f}%  "
            f"Wat {water_/tot_*100:.1f}%",
            xy=(xp, yp), xytext=(dx, dy), textcoords="offset points", fontsize=7.5,
            bbox=dict(boxstyle="round,pad=0.25", fc="white", alpha=0.8, lw=0),
            arrowprops=dict(arrowstyle="-", color="gray", lw=0.6),
        )

    ax_c.set_xlim(-0.18, 1.18)
    ax_c.set_ylim(-0.16, _S3/2 + 0.14)
    ax_c.set_aspect("equal")
    ax_c.axis("off")
    ax_c.set_title("C)  Wind–Solar–Water ternary (2050)", loc="left", fontweight="bold")
    ax_c.legend(fontsize=8, loc="upper right", framealpha=0.9, edgecolor="lightgray")

    fig.suptitle("US generation capacity mix: 2050 baseline vs GA-optimal vs 2020",
                 fontsize=12, fontweight="bold")
    _save(fig, "fig10_capacity_mix", save_dir)


# ══════════════════════════════════════════════════════════════════════════════
# Figure 11 — Population diversity heatmap (final generation)
# ══════════════════════════════════════════════════════════════════════════════

def fig11_diversity_heatmap(df_ga, n_gens, best_row, save_dir):
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

    fig, ax = plt.subplots(figsize=(max(10, len(cols) * 0.45), max(7, len(last_gen) * 0.22)))
    im = ax.imshow(mat_final, aspect="auto", cmap="RdYlGn",
                   vmin=0.5, vmax=1.5, interpolation="nearest")
    ax.set_xticks(np.arange(len(param_labels)))
    ax.set_xticklabels(param_labels, rotation=45, ha="right", fontsize=7)
    ax.set_yticks(np.arange(len(last_gen)))
    cost_labels = [f"#{i+1}  ${c:.1f}B" for i, c in enumerate(costs[order])]
    ax.set_yticklabels(cost_labels, fontsize=7)
    ax.set_xlabel("Parameter (sorted by CV, most constrained left)")
    ax.set_ylabel("Individual (sorted by cost, best at top)")
    ax.set_title(f"Population diversity — final generation (gen {int(n_gens)}, "
                 f"n={len(last_gen)} feasible)\n"
                 "Colour = value / GA-optimal  (green=match, red=diverge)",
                 fontsize=11)
    cbar = fig.colorbar(im, ax=ax, fraction=0.02, pad=0.01)
    cbar.set_label("Value / GA-optimal", fontsize=9)
    ax.axhline(-0.5, color="white", lw=0)  # padding

    _save(fig, "fig11_diversity_heatmap", save_dir)


# ══════════════════════════════════════════════════════════════════════════════
# Figure 12 — Waterfall chart: baseline → GA cost savings by category
# ══════════════════════════════════════════════════════════════════════════════

def fig12_cost_waterfall(bl_costs, opt_costs, bl_energy, opt_energy, save_dir):
    if not bl_costs or not opt_costs:
        _skip("Fig 12", "cost data not available (need both Fortran output files)")
        return

    def _group_bil(costs, energy_twh):
        return {grp: sum(costs.get(lbl, 0.0) for lbl in lbls) * energy_twh / 100
                for grp, lbls in COST_GROUPS.items()}

    bl_grp  = _group_bil(bl_costs,  bl_energy)
    opt_grp = _group_bil(opt_costs, opt_energy)

    bl_total  = sum(bl_grp.values())
    opt_total = sum(opt_grp.values())
    groups    = list(COST_GROUPS.keys())

    # Waterfall: deltas per category, sorted largest saving first
    deltas = {g: opt_grp[g] - bl_grp[g] for g in groups}
    sorted_groups = sorted(groups, key=lambda g: deltas[g])  # largest saving first

    # Build waterfall x positions: start, each category, end
    labels_wf = ["Baseline"] + sorted_groups + ["GA optimal"]
    running   = bl_total
    bottoms   = [0.0]
    heights   = [bl_total]
    bar_colors = ["steelblue"]

    for g in sorted_groups:
        d = deltas[g]
        if d < 0:
            bottoms.append(running + d)
        else:
            bottoms.append(running)
        heights.append(abs(d))
        bar_colors.append("tab:green" if d < 0 else "tab:red")
        running += d

    bottoms.append(0.0)
    heights.append(opt_total)
    bar_colors.append("coral")

    fig, ax = plt.subplots(figsize=(12, 6))
    x = np.arange(len(labels_wf))
    bars = ax.bar(x, heights, 0.55, bottom=bottoms, color=bar_colors,
                  edgecolor="black", lw=0.5, alpha=0.85)

    # Connector lines between bars
    running = bl_total
    for i, g in enumerate(sorted_groups, start=1):
        d = deltas[g]
        y_conn = running + d if d < 0 else running
        ax.plot([x[i] - 0.28, x[i] + 0.28], [y_conn, y_conn],
                color="gray", lw=0.7, ls="--", zorder=5)
        running += d

    # Value labels above/below each bar
    for xi, (bot, ht, lbl) in enumerate(zip(bottoms, heights, labels_wf)):
        top = bot + ht
        if xi == 0 or xi == len(labels_wf) - 1:
            ax.text(xi, top + 1, f"${ht:.1f}B", ha="center", va="bottom",
                    fontsize=9, fontweight="bold")
        else:
            g = sorted_groups[xi - 1]
            d = deltas[g]
            sign = "−" if d < 0 else "+"
            ax.text(xi, top + 1 if d >= 0 else bot - 4,
                    f"{sign}${abs(d):.1f}B",
                    ha="center", va="bottom" if d >= 0 else "top",
                    fontsize=8, color="tab:green" if d < 0 else "tab:red")

    ax.set_xticks(x)
    ax.set_xticklabels(labels_wf, rotation=25, ha="right", fontsize=9)
    ax.set_ylabel(r"Annual system cost (\$B yr$^{-1}$)")
    ax.set_ylim(0, bl_total * 1.12)
    ax.set_title(
        f"Cost waterfall: baseline (${bl_total:.1f}B/yr) → GA optimal (${opt_total:.1f}B/yr)\n"
        f"Total savings: ${bl_total - opt_total:.1f}B/yr  "
        f"({100*(bl_total-opt_total)/bl_total:.1f}%)",
        fontsize=11, fontweight="bold"
    )
    ax.legend(handles=[
        Patch(facecolor="steelblue", label="Baseline total"),
        Patch(facecolor="tab:green", label="Cost reduction"),
        Patch(facecolor="tab:red",   label="Cost increase"),
        Patch(facecolor="coral",     label="GA optimal total"),
    ], fontsize=9, bbox_to_anchor=(1.01, 1), loc="upper left",
       borderaxespad=0, frameon=False, ncol=1)

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

    fig, ax = plt.subplots(figsize=(22, 10))
    ax.set_xlim(-0.32, 1.22)
    ax.set_ylim(-0.06 * src_h, 1.10 * src_h)
    ax.axis("off")

    # Initialise ribbon cursors
    src_cur = [b for b, _ in src_pos]
    dst_cur = [b for b, _ in dst_pos]

    # ── Bypass ribbons first (drawn behind storage column) ────────────────────
    # Direct electricity, T&D losses, and curtailment go straight from the
    # source column to the destination column, bypassing the storage boxes.
    for bp_val, bp_color, dst_idx in bypass_flows:
        for i, ((_sn, src_val, src_color), _) in enumerate(zip(sources, src_pos)):
            rw = src_val * bp_val / src_total
            l_bot, l_top = src_cur[i],       src_cur[i] + rw
            d_bot, d_top = dst_cur[dst_idx], dst_cur[dst_idx] + rw * s_dst
            src_cur[i]       = l_top
            dst_cur[dst_idx] = d_top
            _sankey_ribbon(ax, x_sr, x_dl, l_bot, l_top, d_bot, d_top,
                           color=bp_color, alpha=0.28)

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

    ax.text(0.5 * (x_sl + x_dr + bar_w),
            1.07 * src_h,
            f"Energy flow: {scenario_label} (TWh/year)",
            ha="center", va="bottom", fontsize=14, fontweight="bold")

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
    ax.set_title("GA cost convergence — all regions", fontweight="bold")
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
    ax.set_title("Wind vs solar share — all regions", fontweight="bold")
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
    ax.set_title("Wind–Solar–Water ternary — all regions", fontweight="bold", y=1.02)
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
        log_path = region_dir / "factor_history.log"
        if not log_path.exists():
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

def _load_ga_data(region, repo_root):
    save_dir  = repo_root / "data" / "results_verification" / region
    log_path  = save_dir / "factor_history.log"

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

    best_row = df[(df["feasible"] == True) & (df["cost_mn_bil_per_year"] < float("inf"))]
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
    baseline_out = repo_root / "data" / "raw" / f"xxEGS.{region}"

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
        print(f"  [WARN] Baseline Fortran output not found: {baseline_out}")
        print(f"         Expected at: {baseline_out}")

    return bl_costs, opt_costs, bl_energy, opt_energy, baseline_out


# ══════════════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════════════

def main(region=None):
    if region is None:
        parser = argparse.ArgumentParser(description="Generate LoadMatch publication figures.")
        parser.add_argument("--region", default="UNITED-STATES",
                            help="Region name (default: UNITED-STATES)")
        args = parser.parse_args()
        region = args.region

    print(f"Region    : {region}")
    print(f"Repo root : {REPO_ROOT}")

    # ── Load GA data ──────────────────────────────────────────────────────────
    result = _load_ga_data(region, REPO_ROOT)
    if result[0] is None:
        print("Cannot proceed without factor_history.log — exiting.")
        sys.exit(1)
    df_ga, df_gen, df_best, best_row, n_gens, records, save_dir = result

    print(f"Figures → {save_dir}\n")

    # ── Load baseline factors ─────────────────────────────────────────────────
    # Prefer region-specific file; fall back to Fortran-extracted defaults so
    # fig2/fig4 always show the correct region's baseline, not the US file.
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

    # ── Load Fortran cost data (Figs 9, 12, 13) ───────────────────────────────
    bl_costs, opt_costs, bl_energy, opt_energy, baseline_out = \
        _load_fortran_costs(region, save_dir, REPO_ROOT, best_row)

    # ── Generate figures ──────────────────────────────────────────────────────
    _try_fig("Fig  1 — Cost convergence",
             fig1_convergence, df_gen, save_dir)

    _try_fig("Fig  2 — Capacity-factor trajectories",
             fig2_capacity_factors, df_best, baseline_factors, n_gens, save_dir)

    _try_fig("Fig  3 — Non-capacity parameter trajectories",
             fig3_parameter_trajectories, df_best, baseline_factors, n_gens, save_dir)

    _try_fig("Fig  4 — Baseline vs GA comparison",
             fig4_baseline_vs_ga, df_compare, save_dir)

    _try_fig("Fig  5 — Cost distribution and capacity evolution",
             fig5_cost_and_capacity, df_ga, df_gen, df_best, best_row,
             baseline_factors, n_gens, records, save_dir)

    _try_fig("Fig  6 — Parameter CV (sensitivity)",
             fig6_parameter_cv, df_ga, n_gens, save_dir)

    _try_fig("Fig  7 — Generation and storage capacities",
             fig7_capacity_comparison, best_row, baseline_factors, save_dir)

    _try_fig("Fig  8 — Land area demand",
             fig8_land_area, best_row, baseline_factors, save_dir, region=region)

    _try_fig("Fig  9 — Cost breakdown",
             fig9_cost_breakdown, bl_costs, opt_costs, bl_energy, opt_energy,
             best_row, save_dir)

    _try_fig("Fig 10 — Capacity mix and ternary",
             fig10_capacity_mix, best_row, baseline_factors, save_dir)

    _try_fig("Fig 11 — Population diversity heatmap",
             fig11_diversity_heatmap, df_ga, n_gens, best_row, save_dir)

    _try_fig("Fig 12 — Cost waterfall",
             fig12_cost_waterfall, bl_costs, opt_costs, bl_energy, opt_energy, save_dir)

    _try_fig("Fig 13a — Energy flow Sankey (baseline)",
             fig13_sankey, baseline_out, save_dir,
             scenario_label="baseline scenario",
             filename="fig13a_sankey_energy_flow_baseline")

    optimal_out = save_dir / "fortran_optimal_run.out"
    _try_fig("Fig 13b — Energy flow Sankey (GA-optimal)",
             fig13_sankey, optimal_out, save_dir,
             scenario_label="GA-optimised scenario",
             filename="fig13b_sankey_energy_flow_optimal")

    # ── Overview figures across all regions ──────────────────────────────────
    plot_all_regions(REPO_ROOT)

    print(f"\nDone. All figures in: {save_dir}")


if __name__ == "__main__":
    main()

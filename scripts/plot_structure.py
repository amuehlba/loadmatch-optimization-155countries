"""Structural-difference figures: what the optimisers actually changed.

Companion to plot_comparison.py.  Where those figures show the OUTCOME (cost,
land, solve time), these show the STRUCTURE behind it - which design levers moved
and how the system was rebuilt.  Both optimised cases are compared against the
PI trial-and-error baseline:

    GA (from trial-and-error)   - blue
    GA (from scratch, extended) - orange

Data comes from the two already-exported artefacts:

  data/results_verification/comparison_summary.csv   (region order = system cost)
  data/results_verification/results_export.xlsx      (per region x case detail)

Figures:

  fig_factor_changes            per optimised factor, the distribution across
                                regions of its relative change vs trial-and-error
                                (both cases overlaid; median marked).
  fig_cost_change_by_category   diverging heatmap, regions x cost category, of the
                                change in cost (c/kWh).  Each cell is split on the
                                anti-diagonal: upper-left triangle = GA-from-trial-
                                and-error, lower-right = GA-from-scratch.
  fig_generation_mix_change     same split-cell heatmap for the change in each
                                source's share of TOTAL generation (percentage
                                points = optimised share minus baseline share).

A cell (or point) is grey / absent where the baseline run is infeasible, so no
change can be defined (e.g. a region whose trial-and-error re-run did not
converge).  Same formatting as the other figures (landscape, regions on x, larger
fonts, colour-blind-safe palette, no on-figure titles; caption numbers to stdout).

Usage:
    python -m scripts.plot_structure
    python -m scripts.plot_structure --case-a "GA (trial-error)" --case-b "GA (scratch2)"
"""
import argparse
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.colors import Normalize
from matplotlib.collections import PolyCollection
from matplotlib.cm import ScalarMappable
import openpyxl
import pandas as pd

from scripts.plot_style import (
    apply_style, region_order, diverging_cmap,
    GRID, MUTED, INK, INK_SECONDARY, C_GA, C_SCRATCH,
    LABEL_GA, LABEL_SCRATCH,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = REPO_ROOT / "data" / "results_verification"
DEFAULT_CSV = RESULTS_DIR / "comparison_summary.csv"
DEFAULT_XLSX = RESULTS_DIR / "results_export.xlsx"

BASELINE_CASE = "Baseline"
DEFAULT_CASE_A = "GA (trial-error)"
DEFAULT_CASE_B = "GA (scratch2)"
NAN_GREY = "#d9d9d6"        # cells with no (feasible) baseline

G_FACTORS = "Optimised factors"
G_COST = "Cost by category (c/kWh, MN)"
G_GEN = "Generation (TWh/yr)"
GEN_EXCLUDE = {"Total supply", "Total gen (sum)"}

# Optimised factors that scale GENERATION capacity (wind / PV / solar-thermal /
# CSP turbine); everything else is storage duration/power + dispatch.  Used to
# group the rows of fig_factor_changes (generation on top).
GEN_FACTORS = {"FACONWIN", "FACOFFWIN", "FACUTILPV", "FACRESPV", "FACCOMPV",
               "FACSHT", "CSPTURBFAC"}
# Factors that are held fixed (not GA design variables) and so are not shown as a
# structural "change": CPERFORM (heat-pump COP) is a physical constant.
FACTOR_EXCLUDE = {"CPERFORM"}

apply_style()


def load_structure(xlsx_path: Path) -> pd.DataFrame:
    """Flatten the two-row-header 'Results' sheet into a tidy frame whose columns
    are 'Group :: Name' and rows are one region x case each."""
    wb = openpyxl.load_workbook(xlsx_path, read_only=True, data_only=True)
    ws = wb[wb.sheetnames[0]]
    rows = list(ws.iter_rows(values_only=True))
    groups, names = list(rows[0]), list(rows[1])
    cols, g = [], None
    for grp, nm in zip(groups, names):
        if grp not in (None, ""):
            g = grp
        cols.append(f"{g} :: {nm}")
    df = pd.DataFrame(rows[2:], columns=cols)
    return df[df["Identification :: Region"].notna()].copy()


def _cols_in_group(df, group, exclude=()):
    return [c for c in df.columns
            if c.startswith(group + " :: ") and c.split(" :: ", 1)[1] not in exclude]


def _case_matrix(df, cols, case, regions):
    sub = (df[df["Identification :: Case"] == case]
           .set_index("Identification :: Region").reindex(regions))
    return sub[cols].apply(pd.to_numeric, errors="coerce")


def _short(col):
    return col.split(" :: ", 1)[1]


# --------------------------------------------------------------------------- #
# Split-cell diverging heatmap: two cases per cell (anti-diagonal split)
# --------------------------------------------------------------------------- #
def _diag_heatmap(mA, mB, row_labels, regions, cbar_label, outname, outdir):
    mA, mB = np.asarray(mA, float), np.asarray(mB, float)
    nrows, ncols = mA.shape
    if nrows == 0:
        print(f"  [SKIP] {outname}: no features changed (nothing to plot).")
        return
    fig_h = max(0.42 * nrows + 2.9, 5.0)
    fig, ax = plt.subplots(figsize=(14.0, fig_h))
    cmap = diverging_cmap()
    finite = np.concatenate([mA[np.isfinite(mA)].ravel(), mB[np.isfinite(mB)].ravel()])
    hr = float(np.nanmax(np.abs(finite))) if finite.size else 1.0
    norm = Normalize(-hr, hr)

    polys, colors = [], []
    for i in range(nrows):
        for j in range(ncols):
            ul = [(j - 0.5, i - 0.5), (j + 0.5, i - 0.5), (j - 0.5, i + 0.5)]  # upper-left
            lr = [(j + 0.5, i - 0.5), (j + 0.5, i + 0.5), (j - 0.5, i + 0.5)]  # lower-right
            for tri, val in ((ul, mA[i, j]), (lr, mB[i, j])):
                polys.append(tri)
                colors.append(cmap(norm(val)) if np.isfinite(val) else NAN_GREY)
    ax.add_collection(PolyCollection(polys, facecolors=colors,
                                     edgecolors="white", linewidths=0.5))
    # thicker white separators between regions so columns read at a glance
    ax.vlines(np.arange(0.5, ncols - 0.5), -0.5, nrows - 0.5,
              color="white", lw=2.8, zorder=3)
    ax.set_xlim(-0.5, ncols - 0.5)
    ax.set_ylim(nrows - 0.5, -0.5)
    ax.set_xticks(range(ncols))
    ax.set_xticklabels(regions, rotation=45, ha="right", rotation_mode="anchor")
    ax.set_yticks(range(nrows))
    ax.set_yticklabels(row_labels)
    ax.tick_params(length=0)
    for s in ax.spines.values():
        s.set_visible(False)
    sm = ScalarMappable(norm=norm, cmap=cmap)
    sm.set_array([])
    cb = fig.colorbar(sm, ax=ax, pad=0.012, fraction=0.025)
    cb.set_label(cbar_label, fontsize=11, labelpad=4)
    cb.outline.set_visible(False)
    ax.text(0.0, 1.015,
            f"each cell split diagonally:  upper-left = {LABEL_GA}"
            f"      lower-right = {LABEL_SCRATCH}",
            transform=ax.transAxes, fontsize=10, va="bottom", color=INK_SECONDARY)
    for ext in ("pdf", "png"):
        fig.savefig(outdir / f"{outname}.{ext}")
    plt.close(fig)


def fig_factor_changes(df, outdir, regions, case_a, case_b, linthresh=20.0):
    """Overlaid distribution across regions of each optimised factor's relative
    change vs trial-and-error, for both cases (blue = trial-and-error GA, orange =
    scratch GA).  Symlog x; medians in the right margin."""
    cols = [c for c in _cols_in_group(df, G_FACTORS) if _short(c) not in FACTOR_EXCLUDE]
    base = _case_matrix(df, cols, BASELINE_CASE, regions)
    optA = _case_matrix(df, cols, case_a, regions)
    optB = _case_matrix(df, cols, case_b, regions)

    def _rel(opt):
        with np.errstate(divide="ignore", invalid="ignore"):
            r = (opt - base) / base.abs() * 100.0
        return r.replace([np.inf, -np.inf], np.nan)  # base==0 -> undefined ratio

    relA, relB = _rel(optA), _rel(optB)

    def _mv(c):  # larger of the two cases' median |change|, for ordering
        return max(np.nan_to_num(np.nanmedian(np.abs(relA[c].to_numpy()))),
                   np.nan_to_num(np.nanmedian(np.abs(relB[c].to_numpy()))))

    gen = sorted([c for c in cols if _short(c) in GEN_FACTORS], key=lambda c: -_mv(c))
    sto = sorted([c for c in cols if _short(c) not in GEN_FACTORS], key=lambda c: -_mv(c))
    order = gen + sto          # generation group on top, storage below
    n_gen = len(gen)
    ny = len(order)
    # A relative change bottoms out at -100% (a factor scaled to zero); nothing
    # goes lower, so fix the left limit there and size the right to the (large)
    # positive moves.
    allv = np.concatenate([relA.to_numpy().ravel(), relB.to_numpy().ravel()])
    pos = float(np.nanmax(allv)) if np.isfinite(allv).any() else linthresh
    pos = max(pos, linthresh * 2)
    left = -100.0 * 1.15   # small margin so -100% markers are not clipped

    fig, ax = plt.subplots(figsize=(12.8, 0.5 * ny + 1.6))
    ax.set_xscale("symlog", linthresh=linthresh)
    ax.grid(axis="x", color=GRID, lw=0.7, zorder=0)
    ax.axvline(0, color=MUTED, lw=1.0, zorder=1)
    rng = np.random.default_rng(0)
    eps = 1e-9
    ytr = ax.get_yaxis_transform()   # x in axes fraction, y in data
    for i, c in enumerate(order):
        y = ny - 1 - i
        b = base[c]
        for rel, opt, color, lo, hi, ytxt in (
                (relA, optA, C_GA, 0.04, 0.36, 0.20),
                (relB, optB, C_SCRATCH, -0.36, -0.04, -0.20)):
            vals = rel[c].dropna().to_numpy()
            med = None
            if len(vals):
                ax.scatter(vals, y + rng.uniform(lo, hi, size=len(vals)), s=22,
                           color=color, alpha=0.8, edgecolors="white",
                           linewidths=0.4, zorder=3)
                med = float(np.median(vals))
                ax.plot([med, med], [y + lo, y + hi], color=color, lw=2.4, zorder=4)
            # regions the relative metric can't place from a zero baseline: a
            # factor newly turned ON (0 -> +); switch-offs (+ -> 0) sit at -100%.
            o = opt[c]
            n_on = int(((b.abs() <= eps) & (o > eps)).sum())
            n_off = int(((b > eps) & (o.abs() <= eps)).sum())
            lbl = f"{med:+.0f}%" if med is not None else "n/a"
            if n_on:
                lbl += f"  +{n_on} on"
            if n_off:
                lbl += f"  -{n_off} off"
            ax.text(1.008, y + ytxt, lbl, transform=ytr, va="center", ha="left",
                    fontsize=8.5, color=color)
    ax.set_xlim(left, pos * 1.15)
    ax.set_yticks(range(ny))
    ax.set_yticklabels([_short(c) for c in reversed(order)])
    ax.set_ylim(-0.6, ny - 0.4)
    ax.tick_params(axis="y", length=0)
    # group the rows: generation capacity on top, storage & dispatch below
    if 0 < n_gen < ny:
        ax.axhline(ny - n_gen - 0.5, color=INK, lw=1.3, zorder=5)
    if n_gen:
        ax.text(-0.16, ny - (n_gen + 1) / 2, "Generation capacity", transform=ytr,
                rotation=90, ha="center", va="center", fontsize=11,
                fontweight="bold", color=INK)
    if n_gen < ny:
        ax.text(-0.16, (ny - 1 - n_gen) / 2, "Storage & dispatch", transform=ytr,
                rotation=90, ha="center", va="center", fontsize=11,
                fontweight="bold", color=INK)
    ax.set_xlabel("Change in optimised factor vs trial-and-error "
                  "(%, symlog; right column: median, +newly-on / -off counts)")
    handles = [plt.Line2D([], [], marker="o", ls="", color=C_GA, label=LABEL_GA),
               plt.Line2D([], [], marker="o", ls="", color=C_SCRATCH, label=LABEL_SCRATCH)]
    ax.legend(handles=handles, loc="lower center", bbox_to_anchor=(0.5, 1.005),
              ncols=2, borderaxespad=0.0)
    for ext in ("pdf", "png"):
        fig.savefig(outdir / f"fig_factor_changes.{ext}")
    plt.close(fig)
    mv = ", ".join(f"{_short(c)} {np.nanmedian(relA[c]):+.0f}%/{np.nanmedian(relB[c]):+.0f}%"
                   for c in order[:4])
    print("  wrote fig_factor_changes.pdf/.png")
    print(f"    caption stats (GA-trial/GA-scratch median): largest movers - {mv}.")


def fig_cost_change_by_category(df, outdir, regions, case_a, case_b):
    cols = _cols_in_group(df, G_COST)
    base = _case_matrix(df, cols, BASELINE_CASE, regions)
    dA = _case_matrix(df, cols, case_a, regions) - base
    dB = _case_matrix(df, cols, case_b, regions) - base
    both = pd.concat([dA.abs(), dB.abs()])
    keep = [c for c in cols if np.nanmax(both[c].to_numpy()) > 1e-4]
    if not keep:
        print("  [SKIP] fig_cost_change_by_category: no cost category changed.")
        return
    order = sorted(keep, key=lambda c: -np.nanmax(both[c].to_numpy()))
    _diag_heatmap(dA[order].to_numpy().T, dB[order].to_numpy().T,
                  [_short(c) for c in order], regions,
                  "Cost change (c/kWh)", "fig_cost_change_by_category", outdir)
    print("  wrote fig_cost_change_by_category.pdf/.png")
    print(f"    caption stats: {len(order)} cost categories change (c/kWh); blue = "
          f"cheaper than trial-and-error, red = more expensive.")


def fig_generation_mix_change(df, outdir, regions, case_a, case_b):
    cols = _cols_in_group(df, G_GEN, exclude=GEN_EXCLUDE)
    base = _case_matrix(df, cols, BASELINE_CASE, regions)
    base_sh = base.div(base.sum(axis=1), axis=0) * 100.0

    def dshare(case):
        opt = _case_matrix(df, cols, case, regions)
        return opt.div(opt.sum(axis=1), axis=0) * 100.0 - base_sh

    dA, dB = dshare(case_a), dshare(case_b)
    both = pd.concat([dA.abs(), dB.abs()])
    keep = [c for c in cols if np.nanmax(both[c].to_numpy()) > 1e-3]
    if not keep:
        print("  [SKIP] fig_generation_mix_change: no generation share changed.")
        return
    order = sorted(keep, key=lambda c: -np.nanmax(both[c].to_numpy()))
    _diag_heatmap(dA[order].to_numpy().T, dB[order].to_numpy().T,
                  [_short(c) for c in order], regions,
                  "Δ share of total generation (pp)",
                  "fig_generation_mix_change", outdir)
    print("  wrote fig_generation_mix_change.pdf/.png")
    print("    caption stats: change in each source's share of total generation "
          "(optimised share minus baseline share, percentage points).")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    ap.add_argument("--xlsx", type=Path, default=DEFAULT_XLSX)
    ap.add_argument("--outdir", type=Path, default=RESULTS_DIR)
    ap.add_argument("--case-a", default=DEFAULT_CASE_A,
                    help="Upper-left case, blue (default %(default)r).")
    ap.add_argument("--case-b", default=DEFAULT_CASE_B,
                    help="Lower-right case, orange (default %(default)r).")
    args = ap.parse_args(argv)
    args.outdir.mkdir(parents=True, exist_ok=True)

    cmp_df = pd.read_csv(args.csv)
    df = load_structure(args.xlsx)
    have_r = set(df["Identification :: Region"])
    have_c = set(df["Identification :: Case"])
    regions = [r for r in region_order(cmp_df) if r in have_r]
    if not regions:
        print("No regions found in both the CSV and the XLSX.")
        return
    for case in (args.case_a, args.case_b):
        if case not in have_c:
            print(f"  [WARN] case {case!r} not in the XLSX (available: {sorted(have_c)}).")
    # flag regions with no feasible baseline (change is undefined -> grey cells)
    bl = df[df["Identification :: Case"] == BASELINE_CASE].set_index("Identification :: Region")
    cost = pd.to_numeric(bl["Annual cost ($B/yr) :: Cost MN ($B/yr)"], errors="coerce")
    infeasible = [r for r in regions if r in cost.index and not np.isfinite(cost.get(r))]
    if infeasible:
        print(f"  [INFO] baseline infeasible (shown grey / dropped): {infeasible}")

    fig_factor_changes(df, args.outdir, regions, args.case_a, args.case_b)
    fig_cost_change_by_category(df, args.outdir, regions, args.case_a, args.case_b)
    fig_generation_mix_change(df, args.outdir, regions, args.case_a, args.case_b)


if __name__ == "__main__":
    main()

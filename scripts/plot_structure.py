"""Structural-difference figures: what the GA actually changed vs trial-and-error.

Companion to plot_comparison.py.  Where those figures show the OUTCOME (cost,
land, solve time), these show the STRUCTURE behind it - which design levers moved
and how the system was rebuilt to reach the optimum.  All data comes from the two
already-exported artefacts:

  data/results_verification/comparison_summary.csv   (region order = system cost)
  data/results_verification/results_export.xlsx      (per region x case detail)

Figures (default comparison: GA-from-trial-and-error vs trial-and-error):

  fig_factor_changes            per optimised factor, the distribution across
                                regions of its relative change (median marked) -
                                which levers the GA turns, and by how much.
  fig_cost_change_by_category   diverging heatmap, regions x cost category, of the
                                change in cost (c/kWh): where the savings come from
                                (blue) and where cost is added back (red).
  fig_generation_mix_change     diverging heatmap, regions x generation source, of
                                the change in each source's share of total
                                generation (percentage points): how supply is
                                restructured.

Same formatting as the other figures (landscape, regions on the x-axis, larger
fonts, colour-blind-safe palette, no on-figure titles; caption numbers to stdout).

Usage:
    python -m scripts.plot_structure
    python -m scripts.plot_structure --opt-case "GA (scratch2)"   # vs baseline
"""
import argparse
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.colors import CenteredNorm
import openpyxl
import pandas as pd

from scripts.plot_style import (
    apply_style, region_order, style_region_axis, diverging_cmap,
    GRID, MUTED, INK, INK_SECONDARY, C_GA,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = REPO_ROOT / "data" / "results_verification"
DEFAULT_CSV = RESULTS_DIR / "comparison_summary.csv"
DEFAULT_XLSX = RESULTS_DIR / "results_export.xlsx"

BASELINE_CASE = "Baseline"
DEFAULT_OPT_CASE = "GA (trial-error)"

G_FACTORS = "Optimised factors"
G_COST = "Cost by category (c/kWh, MN)"
G_GEN = "Generation (TWh/yr)"
GEN_EXCLUDE = {"Total supply", "Total gen (sum)"}

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


def _cols_in_group(df: pd.DataFrame, group: str, exclude=()):
    return [c for c in df.columns
            if c.startswith(group + " :: ") and c.split(" :: ", 1)[1] not in exclude]


def _case_matrix(df, cols, case, regions):
    """regions x cols numeric matrix for one case, ordered by `regions`."""
    sub = (df[df["Identification :: Case"] == case]
           .set_index("Identification :: Region")
           .reindex(regions))
    return sub[cols].apply(pd.to_numeric, errors="coerce")


def _short(col):
    return col.split(" :: ", 1)[1]


def _heatmap(values, row_labels, regions, cbar_label, outname, outdir,
             fig_h_per_row=0.34, base_h=2.4):
    """Diverging heatmap: regions on the x-axis, features on the y-axis, colour =
    signed change centred on zero (no change)."""
    m = np.asarray(values, dtype=float)
    nrows = m.shape[0]
    fig, ax = plt.subplots(figsize=(14.0, fig_h_per_row * nrows + base_h))
    norm = CenteredNorm(vcenter=0.0)
    im = ax.imshow(m, aspect="auto", cmap=diverging_cmap(), norm=norm)
    ax.set_yticks(range(nrows))
    ax.set_yticklabels(row_labels)
    ax.set_xticks(range(len(regions)))
    ax.set_xticklabels(regions, rotation=45, ha="right", rotation_mode="anchor")
    ax.tick_params(length=0)
    # thin white cell separators
    ax.set_xticks(np.arange(-0.5, len(regions), 1), minor=True)
    ax.set_yticks(np.arange(-0.5, nrows, 1), minor=True)
    ax.grid(which="minor", color="white", linewidth=1.0)
    for s in ax.spines.values():
        s.set_visible(False)
    cb = fig.colorbar(im, ax=ax, pad=0.012, fraction=0.025)
    cb.set_label(cbar_label)
    cb.outline.set_visible(False)
    for ext in ("pdf", "png"):
        fig.savefig(outdir / f"{outname}.{ext}")
    plt.close(fig)


def fig_factor_changes(df, outdir, regions, opt_case, linthresh=20.0):
    """Distribution across regions of each optimised factor's relative change
    (opt vs trial-and-error).  Strip + median on a symlog axis (linear within
    +/-linthresh %, compressed beyond) so both modest and large moves are legible
    without clipping; the median value is printed in a right-margin column."""
    cols = _cols_in_group(df, G_FACTORS)
    base = _case_matrix(df, cols, BASELINE_CASE, regions)
    opt = _case_matrix(df, cols, opt_case, regions)
    with np.errstate(divide="ignore", invalid="ignore"):
        rel = (opt - base) / base.abs() * 100.0
    rel = rel.replace([np.inf, -np.inf], np.nan)  # base==0 -> undefined ratio
    order = sorted(cols, key=lambda c: -np.nanmedian(np.abs(rel[c].to_numpy())))
    ny = len(order)
    allvals = rel.to_numpy().ravel()
    lim = float(np.nanmax(np.abs(allvals))) if np.isfinite(allvals).any() else linthresh
    lim = max(lim, linthresh * 2)

    fig, ax = plt.subplots(figsize=(12.5, 0.42 * ny + 1.4))
    ax.set_xscale("symlog", linthresh=linthresh)
    ax.grid(axis="x", color=GRID, lw=0.7, zorder=0)
    ax.axvline(0, color=MUTED, lw=1.0, zorder=1)
    rng = np.random.default_rng(0)
    for i, c in enumerate(order):
        y = ny - 1 - i
        vals = rel[c].dropna().to_numpy()
        ax.scatter(vals, y + rng.uniform(-0.22, 0.22, size=len(vals)), s=30,
                   color=C_GA, alpha=0.8, edgecolors="white", linewidths=0.5, zorder=3)
        if len(vals):
            med = float(np.median(vals))
            ax.plot([med, med], [y - 0.34, y + 0.34], color=INK, lw=2.4, zorder=4)
            ax.text(lim * 1.6, y, f"{med:+.0f}%", va="center", ha="right",
                    fontsize=9.5, color=INK)
    ax.set_xlim(-lim * 1.15, lim * 1.7)
    ax.set_yticks(range(ny))
    ax.set_yticklabels([_short(c) for c in reversed(order)])
    ax.set_ylim(-0.6, ny - 0.4)
    ax.tick_params(axis="y", length=0)
    ax.set_xlabel(f"Change in optimised factor vs trial-and-error "
                  f"(%, symlog; median at right); {opt_case}")
    for ext in ("pdf", "png"):
        fig.savefig(outdir / f"fig_factor_changes.{ext}")
    plt.close(fig)
    movers = ", ".join(f"{_short(c)} {np.nanmedian(rel[c]):+.0f}%" for c in order[:4])
    print("  wrote fig_factor_changes.pdf/.png")
    print(f"    caption stats: largest median movers - {movers}.")


def fig_cost_change_by_category(df, outdir, regions, opt_case):
    cols = _cols_in_group(df, G_COST)
    base = _case_matrix(df, cols, BASELINE_CASE, regions)
    opt = _case_matrix(df, cols, opt_case, regions)
    delta = (opt - base)
    keep = [c for c in cols if np.nanmax(np.abs(delta[c].to_numpy())) > 1e-4]
    delta = delta[keep]
    order = sorted(keep, key=lambda c: -np.nanmax(np.abs(delta[c].to_numpy())))
    m = delta[order].to_numpy().T  # rows=category, cols=region
    _heatmap(m, [_short(c) for c in order], regions,
             f"Change in cost (c/kWh): {opt_case} - trial-and-error",
             "fig_cost_change_by_category", outdir)
    tot = np.nansum(m)
    print("  wrote fig_cost_change_by_category.pdf/.png")
    print(f"    caption stats: {len(order)} categories with change; net "
          f"{tot:+.2f} c/kWh summed over cells (blue = cheaper).")


def fig_generation_mix_change(df, outdir, regions, opt_case):
    cols = _cols_in_group(df, G_GEN, exclude=GEN_EXCLUDE)
    base = _case_matrix(df, cols, BASELINE_CASE, regions)
    opt = _case_matrix(df, cols, opt_case, regions)
    base_share = base.div(base.sum(axis=1), axis=0) * 100.0
    opt_share = opt.div(opt.sum(axis=1), axis=0) * 100.0
    delta = (opt_share - base_share)
    keep = [c for c in cols if np.nanmax(np.abs(delta[c].to_numpy())) > 1e-3]
    order = sorted(keep, key=lambda c: -np.nanmax(np.abs(delta[c].to_numpy())))
    m = delta[order].to_numpy().T
    _heatmap(m, [_short(c) for c in order], regions,
             f"Change in generation share (pp): {opt_case} - trial-and-error",
             "fig_generation_mix_change", outdir)
    print("  wrote fig_generation_mix_change.pdf/.png")
    print(f"    caption stats: {len(order)} sources shift share; max |Δ| "
          f"{np.nanmax(np.abs(m)):.1f} pp.")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    ap.add_argument("--xlsx", type=Path, default=DEFAULT_XLSX)
    ap.add_argument("--outdir", type=Path, default=RESULTS_DIR)
    ap.add_argument("--opt-case", default=DEFAULT_OPT_CASE,
                    help="Optimised case to compare against %(default)r "
                         "(e.g. 'GA (scratch2)').")
    args = ap.parse_args(argv)
    args.outdir.mkdir(parents=True, exist_ok=True)

    cmp_df = pd.read_csv(args.csv)
    df = load_structure(args.xlsx)
    have = set(df["Identification :: Region"])
    regions = [r for r in region_order(cmp_df) if r in have]
    if not regions:
        print("No regions found in both the CSV and the XLSX.")
        return
    for case in (BASELINE_CASE, args.opt_case):
        if case not in set(df["Identification :: Case"]):
            print(f"  [WARN] case {case!r} not present in the XLSX; "
                  f"cases available: {sorted(have and set(df['Identification :: Case']))}")
    fig_factor_changes(df, args.outdir, regions, args.opt_case)
    fig_cost_change_by_category(df, args.outdir, regions, args.opt_case)
    fig_generation_mix_change(df, args.outdir, regions, args.opt_case)


if __name__ == "__main__":
    main()

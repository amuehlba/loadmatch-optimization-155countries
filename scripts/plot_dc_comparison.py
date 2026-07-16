"""Publication figures for the data-center cases.

Reads data/results_verification/dc_comparison_summary.csv (written by
export_dc_comparison.py) and produces two landscape, journal-style figures:

  fig_dc_cost_by_region   dodged dot plot, regions on the x-axis (largest system
                          cost first): cost increase of powering the data centers
                          vs the no-data-center optimum, per region and supply
                          strategy.  The +10% added-load share is drawn as the
                          "proportional cost" reference.  A handful of very-high-
                          latitude rooftop-PV cases run to several thousand %
                          (feasibility statements, not meaningful costs); they are
                          drawn off the top with an up-arrow so the linear axis
                          resolves the rest.
  fig_dc_strategy_summary two panels: per-strategy distribution of the cost
                          increase across regions (median marked), and the total
                          added cost over all 30 regions.

Strategy identity is encoded redundantly (colour + marker shape), so the figures
survive greyscale printing and colour-vision deficiency.  No titles or on-figure
statistics (journal style): caption numbers are printed to stdout.

Usage:
    python -m scripts.plot_dc_comparison
    python -m scripts.plot_dc_comparison --csv path.csv --outdir figs/
"""
import argparse
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import numpy as np
import matplotlib.pyplot as plt
import pandas as pd

from scripts.plot_style import (
    apply_style, style_region_axis, add_region_bands, DC_STRATEGIES,
    GRID, MUTED, INK, INK_SECONDARY,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CSV = REPO_ROOT / "data" / "results_verification" / "dc_comparison_summary.csv"

apply_style()

# Per-region axis is capped here; points above it are drawn as an up-arrow at the
# top with their value labelled, so the linear scale resolves the bulk (5-30%).
OUTLIER_PCT = 30.0
# Left summary panel: distribution x-axis clip (a few extreme tails go off-scale).
DIST_XMAX = 42.0


def _load(csv_path: Path) -> pd.DataFrame:
    df = pd.read_csv(csv_path)
    df = df[df["region"] != "TOTAL"].copy()
    for c in df.columns:
        if c != "region" and not c.endswith("_feasible"):
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.sort_values("base_cost_bil_per_yr", ascending=False)


def fig_dc_cost_by_region(df: pd.DataFrame, outdir: Path) -> None:
    order = list(df["region"])
    n = len(order)
    xs = np.arange(n)
    offsets = np.linspace(-0.32, 0.32, len(DC_STRATEGIES))

    fig, ax = plt.subplots(figsize=(16.0, 6.8))

    # Alternating vertical banding groups the five markers belonging to a region.
    add_region_bands(ax, n)

    ax.axhline(10.0, color=MUTED, lw=1.1, ls=(0, (5, 3)), zorder=1)
    # info notes sit in the empty top-right corner (off-scale arrows are on the
    # left/centre) so no extra vertical space is used.
    ax.text(0.985, 0.975, "– – –  +10% = added-load share (proportional-cost reference)",
            transform=ax.transAxes, fontsize=10.5, color=INK_SECONDARY,
            va="top", ha="right")

    cap = OUTLIER_PCT
    offscale = []  # (region, strategy label, value)
    excluded = []  # (region, strategy label)
    for (case, label, color, marker), dx in zip(DC_STRATEGIES, offsets):
        vals = df[f"{case}_delta_pct"].to_numpy(dtype=float)
        x = xs + dx
        onscale = np.where(vals <= cap, vals, np.nan)
        ax.scatter(x, onscale, s=46, color=color, marker=marker, label=label,
                   edgecolors="white", linewidths=0.7, zorder=4)
        for xi, v, reg in zip(x, vals, order):
            if not np.isfinite(v):
                # infeasible / excluded scenario (e.g. rooftop PV at high latitude)
                excluded.append((reg, label))
                ax.scatter([xi], [1.4], s=48, color=color, marker="x",
                           linewidths=1.8, zorder=5)
            elif v > cap:
                offscale.append((reg, label, v))
                ax.annotate("", xy=(xi, cap * 0.95), xytext=(xi, cap * 0.80),
                            arrowprops=dict(arrowstyle="-|>", color=color, lw=1.6),
                            zorder=5)
                ax.text(xi, cap * 0.77, f"{v:.0f}%", ha="center", va="top",
                        fontsize=7.5, color=color, zorder=5)

    ax.set_ylim(0, cap)
    ax.set_ylabel("Cost of powering data centers: increase in\n"
                  "annual system cost vs no-data-center optimum (%)")
    if excluded:
        ax.text(0.985, 0.915, "×  excluded",
                transform=ax.transAxes, fontsize=10, color=INK_SECONDARY,
                va="top", ha="right")
    ax.legend(loc="upper left", ncols=len(DC_STRATEGIES), handletextpad=0.2,
              columnspacing=1.0, borderaxespad=0.3, bbox_to_anchor=(0.0, 1.10))
    style_region_axis(ax, order)

    for ext in ("pdf", "png"):
        fig.savefig(outdir / f"fig_dc_cost_by_region.{ext}")
    plt.close(fig)
    print("  wrote fig_dc_cost_by_region.pdf/.png")
    if offscale:
        note = "; ".join(f"{r} {lab.split('(')[0].strip()} {v:,.0f}%" for r, lab, v in offscale)
        print(f"    off-scale (>{cap:.0f}%, up-arrow + value): {note}")
    if excluded:
        note = "; ".join(f"{r} {lab.split('(')[0].strip()}" for r, lab in excluded)
        print(f"    excluded (marked x): {note}")


def fig_dc_strategy_summary(df: pd.DataFrame, outdir: Path) -> None:
    ny = len(DC_STRATEGIES)
    fig, (axL, axR) = plt.subplots(
        1, 2, figsize=(14.5, 4.6), gridspec_kw={"width_ratios": [1.25, 1.0],
                                                "wspace": 0.08})

    # Left: distribution of the per-region cost increase per strategy (strip +
    # median). Extreme tails beyond DIST_XMAX are annotated rather than plotted so
    # the axis resolves the bulk of the distribution.
    axL.grid(axis="x", color=GRID, lw=0.7, zorder=0)
    rng = np.random.default_rng(0)  # deterministic vertical jitter to declutter
    for i, (case, label, color, marker) in enumerate(DC_STRATEGIES):
        y = ny - 1 - i
        vals = df[f"{case}_delta_pct"].dropna()
        shown = vals[vals <= DIST_XMAX]
        jitter = rng.uniform(-0.22, 0.22, size=len(shown))
        axL.scatter(shown, y + jitter, s=34, color=color, marker=marker,
                    alpha=0.8, edgecolors="white", linewidths=0.6, zorder=3)
        med = vals.median()
        if med <= DIST_XMAX:
            axL.plot([med, med], [y - 0.34, y + 0.34], color=INK, lw=2.4, zorder=4)
            axL.text(med, y + 0.42, f"{med:.1f}%", ha="center", fontsize=10, color=INK)
        n_off = int((vals > DIST_XMAX).sum())
        if n_off:
            axL.annotate(f"+{n_off} >{DIST_XMAX:.0f}%  ", xy=(DIST_XMAX, y),
                         ha="right", va="center", fontsize=9, color=color,
                         fontstyle="italic")
    axL.axvline(10.0, color=MUTED, lw=1.1, ls=(0, (5, 3)), zorder=1)
    axL.set_xlim(0, DIST_XMAX)
    axL.set_yticks(range(ny))
    axL.set_yticklabels([s[1] for s in reversed(DC_STRATEGIES)])
    axL.set_ylim(-0.6, ny - 0.4)
    axL.set_xlabel("Cost increase per region (%; median marked)")
    axL.tick_params(axis="y", length=0)

    # Right: total added cost over all regions (bars).
    axR.grid(axis="x", color=GRID, lw=0.7, zorder=0)
    totals = [df[f"{case}_delta_bil_per_yr"].dropna().sum() for case, *_ in DC_STRATEGIES]
    ypos = list(range(ny))[::-1]
    axR.barh(ypos, totals, height=0.66, color=[s[2] for s in DC_STRATEGIES], zorder=3)
    for y, tot in zip(ypos, totals):
        axR.text(tot, y, f" {tot:,.0f}", va="center", fontsize=10, color=INK_SECONDARY)
    axR.set_yticks(range(ny))
    axR.set_yticklabels([])
    axR.set_ylim(-0.6, ny - 0.4)
    axR.set_xlim(0, max(totals) * 1.22)
    axR.set_xlabel("Total added cost, all regions (billion USD yr$^{-1}$)")
    axR.tick_params(axis="y", length=0)

    for ext in ("pdf", "png"):
        fig.savefig(outdir / f"fig_dc_strategy_summary.{ext}")
    plt.close(fig)
    print("  wrote fig_dc_strategy_summary.pdf/.png")
    meds = "; ".join(f"{lab.split('(')[0].strip()} {df[f'{c}_delta_pct'].median():.1f}%"
                     for c, lab, *_ in DC_STRATEGIES)
    print(f"    caption stats: median cost increase - {meds}.")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    ap.add_argument("--outdir", type=Path,
                    default=REPO_ROOT / "data" / "results_verification")
    args = ap.parse_args(argv)
    args.outdir.mkdir(parents=True, exist_ok=True)
    df = _load(args.csv)
    if df.empty:
        print("No data-center rows found in", args.csv)
        return
    fig_dc_cost_by_region(df, args.outdir)
    fig_dc_strategy_summary(df, args.outdir)


if __name__ == "__main__":
    main()

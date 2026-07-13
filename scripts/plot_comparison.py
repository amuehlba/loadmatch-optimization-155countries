"""Publication figures from comparison_summary.csv.

Reads data/results_verification/comparison_summary.csv (written by
export_comparison.py) and produces three landscape, journal-style figures with
the regions on the x-axis (shared region order = largest system cost first):

  fig_cost_comparison  two stacked panels sharing the region axis:
                        (a) absolute annual system cost per region on a log axis
                            (dumbbell markers: trial-and-error, GA-from-trial-and-
                            error, GA-from-scratch) - shows the regional scale and
                            that the three track each other within a region;
                        (b) GA cost reduction vs trial-and-error (%, linear) - the
                            headline comparison, which is invisible on the log
                            panel because it is ~1-8% against a 10^4 spread.
  fig_land_comparison  new land for WWS (wind spacing + footprint, % of regional
                        land) per region on a log axis, trial-and-error vs GA
                        (dumbbell); the connector length shows where GA changed
                        land use.
  fig_solve_time       GA solve time per region (hours, linear): GA-from-trial-
                        and-error vs GA-from-scratch.

Figures carry no titles or on-figure statistics (journal style): the numbers a
caption needs are printed to stdout when the script runs.

Standalone (needs only pandas/matplotlib + the CSV), so it can be regenerated
without the full per-region result tree.

Usage:
    python -m scripts.plot_comparison
    python -m scripts.plot_comparison --csv path/to/comparison_summary.csv --outdir figs/
"""
import argparse
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import numpy as np
import matplotlib.pyplot as plt
import pandas as pd

from scripts.plot_style import (
    apply_style, region_order, style_region_axis, add_region_bands,
    C_BASELINE, C_GA, C_SCRATCH,
    LABEL_BASELINE, LABEL_GA, LABEL_SCRATCH,
    GRID, STICK, MUTED,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CSV = REPO_ROOT / "data" / "results_verification" / "comparison_summary.csv"

apply_style()

# Marker areas: the trial-and-error baseline is drawn as a larger halo behind the
# smaller optimized markers, so it stays visible where GA nearly coincides with it
# (the differences are ~1-8%, invisible on a log axis otherwise).
_MARKER = dict(edgecolors="white", linewidths=0.9, zorder=4)
SZ_BASE, SZ_GA, SZ_SCRATCH = 88, 46, 60


def _load(csv_path: Path) -> pd.DataFrame:
    df = pd.read_csv(csv_path)
    df = df[~df["region"].astype(str).str.startswith("TOTAL")].copy()
    numeric = ["baseline_cost_bil_per_yr", "ga_cost_bil_per_yr",
               "pct_savings", "optimize_seconds", "total_seconds",
               "ga_scratch2_cost_bil_per_yr", "scratch2_vs_ga_pct",
               "scratch2_optimize_seconds",
               "bl_newland_pct_regland", "ga_newland_pct_regland",
               "land_delta_pp"]
    for c in numeric:
        df[c] = pd.to_numeric(df[c], errors="coerce") if c in df.columns else float("nan")
    return df


def _dumbbell(ax, xs, series):
    """Draw a dumbbell column per region: a thin connector spanning the series,
    then a coloured marker per series.  `series` = [(values, color, marker,
    label, size), ...] aligned to xs.  Returns {label: handle} so the caller
    controls legend order independently of draw order.

    Markers are drawn largest-first so smaller optimized markers land on top of
    the baseline halo; the legend order follows the given series order.
    """
    stacked = np.vstack([np.asarray(s[0], dtype=float) for s in series])
    lo = np.nanmin(stacked, axis=0)
    hi = np.nanmax(stacked, axis=0)
    for x, a, b in zip(xs, lo, hi):
        if b > a:
            ax.plot([x, x], [a, b], color=STICK, lw=1.6, zorder=2,
                    solid_capstyle="round")
    handles = {}
    for vals, color, marker, label, size in sorted(series, key=lambda s: -s[4]):
        handles[label] = ax.scatter(xs, vals, color=color, marker=marker,
                                     label=label, s=size, **_MARKER)
    return handles


def _paired_bars(ax, xs, series):
    """Grouped vertical bars around a zero baseline.  `series` = [(values, color),
    ...]: one series -> a single centred bar, two -> dodged side by side."""
    ns = len(series)
    if ns == 1:
        ax.bar(xs, series[0][0], width=0.62, color=series[0][1], zorder=3)
        return
    w = 0.8 / ns
    for si, (vals, color) in enumerate(series):
        offset = (si - (ns - 1) / 2.0) * w
        ax.bar([x + offset for x in xs], vals, width=w * 0.92, color=color, zorder=3)


def fig_cost_comparison(df: pd.DataFrame, outdir: Path, order) -> None:
    d = df.set_index("region").reindex(order).reset_index()
    n = len(d)
    xs = list(range(n))

    fig, (axA, axB) = plt.subplots(
        2, 1, figsize=(13.5, 8.2), sharex=True,
        gridspec_kw={"height_ratios": [1.35, 1.0], "hspace": 0.12})

    # (a) absolute cost, log axis, dumbbell of the three model variants
    add_region_bands(axA, n)
    axA.grid(axis="y", color=GRID, lw=0.7, zorder=0.5)
    order_series = [LABEL_BASELINE, LABEL_GA, LABEL_SCRATCH]
    h = _dumbbell(axA, xs, [
        (d["baseline_cost_bil_per_yr"], C_BASELINE, "o", LABEL_BASELINE, SZ_BASE),
        (d["ga_cost_bil_per_yr"], C_GA, "o", LABEL_GA, SZ_GA),
        (d["ga_scratch2_cost_bil_per_yr"], C_SCRATCH, "o", LABEL_SCRATCH, SZ_SCRATCH),
    ])
    axA.set_yscale("log")
    axA.set_ylabel("Annual system cost\n(billion USD yr$^{-1}$, log scale)")
    axA.legend([h[k] for k in order_series], order_series, loc="upper right",
               ncols=3, handletextpad=0.2, columnspacing=1.1, borderaxespad=0.2)
    axA.text(0.0, 1.02, "a", transform=axA.transAxes, fontweight="bold",
             fontsize=14, va="bottom")

    # (b) cost reduction vs trial-and-error (%): GA and the from-scratch run, each
    # measured against the same baseline (positive = cheaper than trial-and-error;
    # the from-scratch run is mostly more expensive, so its bars go below zero).
    add_region_bands(axB, n)
    axB.grid(axis="y", color=GRID, lw=0.7, zorder=0.5)
    axB.axhline(0, color=MUTED, lw=0.9, zorder=1)
    sc_red = (100.0 * (d["baseline_cost_bil_per_yr"] - d["ga_scratch2_cost_bil_per_yr"])
              / d["baseline_cost_bil_per_yr"])
    _paired_bars(axB, xs, [(d["pct_savings"], C_GA), (sc_red, C_SCRATCH)])
    axB.set_ylabel("Cost reduction vs\ntrial-and-error (%)")
    axB.text(0.0, 1.02, "b", transform=axB.transAxes, fontweight="bold",
             fontsize=14, va="bottom")

    style_region_axis(axB, order)
    axA.tick_params(axis="x", length=0)

    for ext in ("pdf", "png"):
        fig.savefig(outdir / f"fig_cost_comparison.{ext}")
    plt.close(fig)

    tot_b = d["baseline_cost_bil_per_yr"].sum()
    tot_g = d["ga_cost_bil_per_yr"].sum()
    red = 100.0 * (tot_b - tot_g) / tot_b
    sc = d.dropna(subset=["ga_scratch2_cost_bil_per_yr"])
    sc_gap = (100.0 * (sc["ga_scratch2_cost_bil_per_yr"].sum() - sc["ga_cost_bil_per_yr"].sum())
              / sc["ga_cost_bil_per_yr"].sum()) if len(sc) else float("nan")
    print(f"  wrote fig_cost_comparison.pdf/.png")
    print(f"    caption stats: total cost over {n} regions "
          f"{tot_b:,.0f} -> {tot_g:,.0f} billion USD/yr (-{red:.1f}%); "
          f"per-region GA reduction {d['pct_savings'].min():.1f}-{d['pct_savings'].max():.1f}%; "
          f"GA-from-scratch (extended) is {sc_gap:+.1f}% vs GA-from-trial-and-error over {len(sc)} regions.")


def fig_land_comparison(df: pd.DataFrame, outdir: Path, order,
                        tolerance: float = 0.10) -> None:
    d = df.dropna(subset=["bl_newland_pct_regland", "ga_newland_pct_regland"]).copy()
    if d.empty:
        print("  [SKIP] fig_land_comparison: no land data in the CSV.")
        return
    d = d.set_index("region").reindex([r for r in order if r in set(d["region"])]).reset_index()
    order_l = list(d["region"])
    n = len(d)
    xs = list(range(n))
    # Panel (b) is the difference in new-land share vs trial-and-error, in
    # percentage points (the shares are already % of regional land, so a pp
    # difference is the directly meaningful quantity).
    d["land_pp_change"] = d["ga_newland_pct_regland"] - d["bl_newland_pct_regland"]
    # The from-scratch run's land is added to both panels automatically once the
    # CSV carries the column (ga_scratch2_newland_pct_regland); the current export
    # only has trial-and-error and GA-from-trial-and-error land.
    has_sc_land = ("ga_scratch2_newland_pct_regland" in d.columns
                   and d["ga_scratch2_newland_pct_regland"].notna().any())
    if has_sc_land:
        sc_land = pd.to_numeric(d["ga_scratch2_newland_pct_regland"], errors="coerce")
        d["land_pp_change_scratch"] = sc_land - d["bl_newland_pct_regland"]

    fig, (axA, axB) = plt.subplots(
        2, 1, figsize=(13.5, 7.6), sharex=True,
        gridspec_kw={"height_ratios": [1.3, 1.0], "hspace": 0.12})

    # (a) absolute new-land share, dumbbell of baseline vs GA (+ from-scratch)
    add_region_bands(axA, n)
    axA.grid(axis="y", color=GRID, lw=0.7, zorder=0.5)
    land_series = [
        (d["bl_newland_pct_regland"], C_BASELINE, "o", LABEL_BASELINE, SZ_BASE),
        (d["ga_newland_pct_regland"], C_GA, "o", LABEL_GA, SZ_GA),
    ]
    order_series = [LABEL_BASELINE, LABEL_GA]
    if has_sc_land:
        land_series.append((sc_land, C_SCRATCH, "o", LABEL_SCRATCH, SZ_SCRATCH))
        order_series.append(LABEL_SCRATCH)
    h = _dumbbell(axA, xs, land_series)
    axA.set_ylim(bottom=0)
    axA.set_ylabel("New land for WWS: spacing + footprint\n"
                   "(% of regional land area)")
    axA.legend([h[k] for k in order_series], order_series, loc="upper right", ncols=1)
    axA.text(0.0, 1.02, "a", transform=axA.transAxes, fontweight="bold",
             fontsize=14, va="bottom")

    # (b) difference in new-land use vs trial-and-error, percentage points
    # (signed: up = uses more land, down = less), for GA and (when present) the
    # from-scratch run.
    add_region_bands(axB, n)
    axB.grid(axis="y", color=GRID, lw=0.7, zorder=0.5)
    axB.axhline(0, color=MUTED, lw=0.9, zorder=1)
    bars = [(d["land_pp_change"], C_GA)]
    if has_sc_land:
        bars.append((d["land_pp_change_scratch"], C_SCRATCH))
    _paired_bars(axB, xs, bars)
    axB.set_ylabel("Difference in new-land use vs\ntrial-and-error (percentage points)")
    axB.text(0.0, 1.02, "b", transform=axB.transAxes, fontweight="bold",
             fontsize=14, va="bottom")

    style_region_axis(axB, order_l)
    axA.tick_params(axis="x", length=0)

    for ext in ("pdf", "png"):
        fig.savefig(outdir / f"fig_land_comparison.{ext}")
    plt.close(fig)

    pp = d["land_pp_change"]
    flagged = d["region"][(d["bl_newland_pct_regland"] > 0) &
                          (d["ga_newland_pct_regland"] > d["bl_newland_pct_regland"] * (1 + tolerance))].tolist()
    check = (f"all {n} regions within +{100*tolerance:.0f}% of baseline"
             if not flagged else
             f"{len(flagged)} of {n} exceed baseline by >{100*tolerance:.0f}%: {flagged}")
    print(f"  wrote fig_land_comparison.pdf/.png")
    print(f"    caption stats: GA land difference {pp.min():+.2f} to {pp.max():+.2f} pp; {check}.")


def fig_solve_time(df: pd.DataFrame, outdir: Path, order) -> None:
    d = df.dropna(subset=["optimize_seconds"]).copy()
    d["hours"] = d["optimize_seconds"] / 3600.0
    d["hours_scratch2"] = d["scratch2_optimize_seconds"] / 3600.0
    d = d.set_index("region").reindex([r for r in order if r in set(d["region"])]).reset_index()
    order_s = list(d["region"])
    n = len(d)
    xs = list(range(n))

    series = [("hours", C_GA, LABEL_GA)]
    if d["hours_scratch2"].notna().any():
        series.append(("hours_scratch2", C_SCRATCH, LABEL_SCRATCH))
    ns = len(series)
    w = 0.8 / ns

    fig, ax = plt.subplots(figsize=(13.5, 5.2))
    ax.grid(axis="y", color=GRID, lw=0.7, zorder=0)
    for si, (col, color, label) in enumerate(series):
        offset = (si - (ns - 1) / 2.0) * w
        ax.bar([x + offset for x in xs], d[col], width=w * 0.92,
               color=color, label=label, zorder=3)
    if ns > 1:
        ax.legend(loc="upper right", ncols=ns)
    ax.set_ylabel("GA solve time (hours)")
    ax.set_ylim(bottom=0)
    style_region_axis(ax, order_s)

    for ext in ("pdf", "png"):
        fig.savefig(outdir / f"fig_solve_time.{ext}")
    plt.close(fig)

    totals = "; ".join(f"{d[col].sum():.0f} h {label}" for col, _, label in series)
    print(f"  wrote fig_solve_time.pdf/.png")
    print(f"    caption stats: totals over {n} regions - {totals}.")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    ap.add_argument("--outdir", type=Path,
                    default=REPO_ROOT / "data" / "results_verification")
    ap.add_argument("--land-tolerance", type=float, default=0.10,
                    help="Relative tolerance for flagging optimized new-land "
                         "share above baseline (default: %(default)s).")
    args = ap.parse_args(argv)
    args.outdir.mkdir(parents=True, exist_ok=True)
    df = _load(args.csv)
    missing = df[df["baseline_cost_bil_per_yr"].isna()]["region"].tolist()
    if missing:
        print(f"  [WARN] regions missing a baseline cost (excluded): {missing}")
    order = region_order(df)
    fig_cost_comparison(df, args.outdir, order)
    fig_land_comparison(df, args.outdir, order, tolerance=args.land_tolerance)
    fig_solve_time(df, args.outdir, order)


if __name__ == "__main__":
    main()

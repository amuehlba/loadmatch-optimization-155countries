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
  fig_land_comparison  single panel: new land for WWS (wind spacing + footprint,
                        % of regional land) per region, linear axis, dumbbell of
                        trial-and-error, GA-from-trial-and-error and GA-from-
                        scratch; the connector length shows where GA changed
                        land use.
  fig_solve_time       two panels:
                        (a) GA solve time per region (hours, linear): GA-from-
                            trial-and-error vs GA-from-scratch, with the mean
                            trial-and-error time per region as a reference;
                        (b) time to solve all 30 regions (hours, log) per model:
                            one simulation at a time on one core vs (GA only)
                            one region at a time on 24 cores vs as run (~3
                            overlapping jobs for trial-and-error; all regions
                            at once for the GA) - the parallelisation benefit.

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
    apply_style, region_order, style_region_axis, add_region_bands, region_label,
    C_BASELINE, C_GA, C_SCRATCH,
    LABEL_BASELINE, LABEL_GA, LABEL_SCRATCH, TAE_IT, plain_label,
    GRID, STICK, MUTED, INK, INK_SECONDARY,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CSV = REPO_ROOT / "data" / "results_verification" / "comparison_summary.csv"

apply_style()

# Marker areas: the trial-and-error baseline is drawn as a larger halo behind the
# smaller optimized markers, so it stays visible where GA nearly coincides with it
# (the differences are ~1-8%, invisible on a log axis otherwise).
_MARKER = dict(edgecolors="white", linewidths=0.9, zorder=4)
SZ_BASE, SZ_GA, SZ_SCRATCH = 88, 46, 60

# LOADMATCH trial-and-error effort for the 30 regions, as recorded by the model
# developer: ~30 simulations per region at 75.3 s each on one core (Intel Xeon
# Gold 6154) plus ~45 s of expert time per simulation to inspect, adjust and
# resubmit; run with ~3 jobs overlapping, the tuning took ~17.5 h of expert-
# attended wall-clock.  75.3 s is also the single-core reference used for the
# GA's one-simulation-at-a-time (serial) equivalent.
TAE_SIMS_PER_REGION = 30
TAE_SEC_PER_SIM = 75.3
TAE_EXPERT_SEC_PER_SIM = 45.0
TAE_WALL_HOURS = 17.5
GA_PARALLEL_EVALS = 24          # --parallel-evals / cores per region job


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
    axA.set_ylabel("Annual private energy cost\n(billion 2023 USD yr$^{-1}$, log scale)")
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
    axB.set_ylabel(f"Cost reduction vs LOADMATCH\n({TAE_IT}) (%)")
    axB.text(0.0, 1.02, "b", transform=axB.transAxes, fontweight="bold",
             fontsize=14, va="bottom")

    style_region_axis(axB, [region_label(r) for r in order])
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
          f"{tot_b:,.0f} -> {tot_g:,.0f} billion 2023 USD/yr (-{red:.1f}%); "
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
    # Difference in new-land share vs trial-and-error, in percentage points (the
    # shares are already % of regional land); caption stats only - the dumbbell
    # connector already shows it, so there is no separate difference panel.
    d["land_pp_change"] = d["ga_newland_pct_regland"] - d["bl_newland_pct_regland"]
    # The from-scratch run's land is added automatically once the CSV carries the
    # column (ga_scratch2_newland_pct_regland).
    has_sc_land = ("ga_scratch2_newland_pct_regland" in d.columns
                   and d["ga_scratch2_newland_pct_regland"].notna().any())
    if has_sc_land:
        sc_land = pd.to_numeric(d["ga_scratch2_newland_pct_regland"], errors="coerce")
        d["land_pp_change_scratch"] = sc_land - d["bl_newland_pct_regland"]

    fig, ax = plt.subplots(figsize=(13.5, 5.6))

    # absolute new-land share, dumbbell of trial-and-error vs GA (+ from-scratch)
    add_region_bands(ax, n)
    ax.grid(axis="y", color=GRID, lw=0.7, zorder=0.5)
    land_series = [
        (d["bl_newland_pct_regland"], C_BASELINE, "o", LABEL_BASELINE, SZ_BASE),
        (d["ga_newland_pct_regland"], C_GA, "o", LABEL_GA, SZ_GA),
    ]
    order_series = [LABEL_BASELINE, LABEL_GA]
    if has_sc_land:
        land_series.append((sc_land, C_SCRATCH, "o", LABEL_SCRATCH, SZ_SCRATCH))
        order_series.append(LABEL_SCRATCH)
    h = _dumbbell(ax, xs, land_series)
    ax.set_ylim(bottom=0)
    ax.set_ylabel("New land for WWS: spacing + footprint\n"
                  "(% of regional land area)")
    ax.legend([h[k] for k in order_series], order_series, loc="upper right", ncols=1)
    style_region_axis(ax, [region_label(r) for r in order_l])

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
    if has_sc_land:
        ps = d["land_pp_change_scratch"]
        print(f"    caption stats: from-scratch land difference {ps.min():+.2f} to {ps.max():+.2f} pp.")


def _solve_time_totals(d: pd.DataFrame, n_regions: int):
    """Per-model totals over all regions (hours): `serial` = every simulation one
    after another on one core; `as_run` = wall-clock as actually run (trial-and-
    error: ~3 overlapping jobs; GA: all regions at once, so the slowest region);
    `per_region` (GA only) = regions one after another, each on its own 24-core
    node (the summed per-region times)."""
    tae_sims = TAE_SIMS_PER_REGION * n_regions
    tae_cpu = tae_sims * TAE_SEC_PER_SIM / 3600.0
    tae_expert = tae_sims * TAE_EXPERT_SEC_PER_SIM / 3600.0
    rows = [dict(label=LABEL_BASELINE, color=C_BASELINE, sims=tae_sims,
                 serial=tae_cpu + tae_expert, per_region=None, as_run=TAE_WALL_HOURS,
                 cpu=tae_cpu, expert=tae_expert)]
    for hcol, ncol, color, label in (("hours", "n_evaluations", C_GA, LABEL_GA),
                                     ("hours_scratch2", "scratch2_n_evaluations",
                                      C_SCRATCH, LABEL_SCRATCH)):
        if hcol not in d or d[hcol].isna().all() or ncol not in d:
            continue
        sims = float(pd.to_numeric(d[ncol], errors="coerce").sum())
        rows.append(dict(label=label, color=color, sims=sims,
                         serial=sims * TAE_SEC_PER_SIM / 3600.0,
                         per_region=float(d[hcol].sum()), as_run=float(d[hcol].max()),
                         cpu=None, expert=0.0))
    return rows


def _h(v):
    """Hours label: 1 decimal below 100 h (rounded half-up, so 11.25 -> 11.3),
    thousands-separated whole hours above."""
    return f"{v:,.0f} h" if v >= 100 else f"{v + 1e-9:.1f} h"


def _draw_solve_totals(ax, rows):
    """Panel (b): one row per model on a log hour axis.  Open circle = serial
    (one simulation at a time on one core, plus expert time for trial-and-
    error); diamond = GA with regions one after another on 24 cores each; filled
    circle = as run.  The connector spans the parallelisation gain; labels give
    the speed-up over serial."""
    ny = len(rows)

    def _mark(x, y, speedup, **kw):
        ax.scatter(x, y, zorder=5, **kw)
        ax.text(x, y + 0.2, _h(x), ha="center", va="bottom", fontsize=9.5, color=INK)
        if speedup:
            ax.text(x, y - 0.22, speedup, ha="center", va="top", fontsize=9.5,
                    color=INK_SECONDARY)

    for i, r in enumerate(rows):
        y = ny - 1 - i
        pts = [v for v in (r["serial"], r["per_region"], r["as_run"]) if v]
        ax.plot([min(pts), max(pts)], [y, y], color=STICK, lw=1.6, zorder=2,
                solid_capstyle="round")
        _mark(r["serial"], y, None, s=80, facecolors="white", edgecolors=r["color"],
              linewidths=1.8)
        if r["per_region"]:
            _mark(r["per_region"], y, f"{r['serial'] / r['per_region']:.1f}\u00d7",
                  s=56, marker="D", color=r["color"], alpha=0.55, edgecolors="white",
                  linewidths=0.9)
        sp = r["serial"] / r["as_run"]
        _mark(r["as_run"], y, f"{sp:.1f}\u00d7 faster" if sp < 100 else f"{sp:.0f}\u00d7 faster",
              s=80, color=r["color"], edgecolors="white", linewidths=0.9)
        note = (f"{r['sims']:,.0f} simulations, "
                + (f"{_h(r['cpu'])} compute\n+ {_h(r['expert'])} expert time"
                   if r["expert"] else "no expert time"))
        ax.text(1.01, y, note, transform=ax.get_yaxis_transform(), ha="left",
                va="center", fontsize=10, color=INK)
    ax.set_xscale("log")
    lo = min(r["as_run"] for r in rows)
    hi = max(r["serial"] for r in rows)
    ax.set_xlim(lo / 2.0, hi * 2.0)
    ax.set_ylim(-0.7, ny - 0.3)
    ax.set_yticks(range(ny))
    ax.set_yticklabels([r["label"] for r in reversed(rows)])
    ax.tick_params(axis="y", length=0)
    ax.grid(axis="x", which="major", color=GRID, lw=0.7, zorder=0)
    ax.set_xlabel("Time to solve all 30 regions (hours, log scale)")
    keys = [
        plt.Line2D([], [], marker="o", ls="", mfc="white", mec=MUTED, mew=1.8, ms=8,
                   label=f"One simulation at a time on one core ({TAE_SEC_PER_SIM:g} s each)"),
        plt.Line2D([], [], marker="D", ls="", color=MUTED, alpha=0.55, mec="white", ms=7,
                   label=f"LOADMATCH-O: one region at a time, {GA_PARALLEL_EVALS} cores"),
        plt.Line2D([], [], marker="o", ls="", color=MUTED, mec="white", ms=8,
                   label=f"As run: {TAE_IT}, ~3 overlapping jobs; LOADMATCH-O, all "
                         f"regions at once ({GA_PARALLEL_EVALS} cores each)"),
    ]
    return ax.legend(handles=keys, loc="lower left", bbox_to_anchor=(0.03, 1.0),
                     ncols=1, fontsize=9.5, handletextpad=0.3, borderaxespad=0.3)


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

    fig, (axA, axB) = plt.subplots(
        2, 1, figsize=(13.5, 9.8),
        gridspec_kw={"height_ratios": [1.5, 1.0], "hspace": 0.75})

    # (a) GA solve time per region, trial-and-error mean as a reference line
    add_region_bands(axA, n)
    axA.grid(axis="y", color=GRID, lw=0.7, zorder=0)
    for si, (col, color, label) in enumerate(series):
        offset = (si - (ns - 1) / 2.0) * w
        axA.bar([x + offset for x in xs], d[col], width=w * 0.92,
                color=color, label=label, zorder=3)
    tae_region_h = TAE_WALL_HOURS / n
    axA.axhline(tae_region_h, color=C_BASELINE, lw=1.6, ls="--", zorder=4,
                label=f"{LABEL_BASELINE}, mean {tae_region_h:.2f} h per region")
    ymax = max(float(d[col].max()) for col, _, _ in series)
    axA.set_ylim(0, ymax * 1.22)   # headroom so the upper-right legend clears the bars
    axA.legend(loc="upper right", ncols=3, fontsize=10)
    axA.set_ylabel("Solve time per region\n(hours, wall-clock)")
    style_region_axis(axA, [region_label(r) for r in order_s])
    axA.text(0.0, 1.02, "a", transform=axA.transAxes, fontweight="bold",
             fontsize=14, va="bottom")

    # (b) all-region totals: serial vs (GA) one region at a time vs as run
    rows = _solve_time_totals(d, n)
    leg = _draw_solve_totals(axB, rows)
    fig.canvas.draw()   # panel letter sits level with the top of the key
    top = axB.transAxes.inverted().transform(leg.get_window_extent().extents[2:])[1]
    axB.text(0.0, top, "b", transform=axB.transAxes, fontweight="bold",
             fontsize=14, va="top")

    for ext in ("pdf", "png"):
        fig.savefig(outdir / f"fig_solve_time.{ext}")
    plt.close(fig)

    print(f"  wrote fig_solve_time.pdf/.png")
    for r in rows:
        extra = (f"; one region at a time {r['per_region']:.1f} h "
                 f"({r['serial'] / r['per_region']:.1f}x)" if r["per_region"] else
                 f" (compute {_h(r['cpu'])} + expert {_h(r['expert'])} serial)")
        print(f"    caption stats: {plain_label(r['label'])}: {r['sims']:,.0f} simulations; "
              f"serial {r['serial']:,.1f} h; as run {r['as_run']:.1f} h "
              f"({r['serial'] / r['as_run']:.1f}x){extra}.")
    for col, _, label in series:
        eff = d[col].sum() * 3600.0 * GA_PARALLEL_EVALS / pd.to_numeric(
            d["n_evaluations" if col == "hours" else "scratch2_n_evaluations"]).sum()
        print(f"    {plain_label(label)}: effective {eff:.0f} core-s per simulation under "
              f"{GA_PARALLEL_EVALS}-way load (vs {TAE_SEC_PER_SIM} s single-core reference).")


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

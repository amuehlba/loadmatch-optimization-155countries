"""Data-center cost figure (fig_dc_combined).

Reads data/results_verification/dc_comparison_summary.csv (written by
export_dc_comparison.py) and draws one landscape figure:

  (a) per supply strategy: the distribution across regions of the cost increase
      of powering the data centers vs the no-data-center optimum (median and
      cost-weighted mean marked), and the total added cost over all regions;
  (b) per region (alphabetical) and strategy: the cost increase as a dodged dot
      plot, with the +10% added-demand share as the "proportional cost"
      reference.  Points above the axis cap are drawn as labelled up-arrows.

Greenland and Iceland are excluded from the rooftop (RBH) case, which is not
applicable at those latitudes; the exclusion is stated in the caption.

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
    apply_dc_style as apply_style, style_region_axis, add_region_bands,
    cap_region, DC_STRATEGIES, minor_ticks, region_sort_key,
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
# Greenland & Iceland rooftop (case 3 / dc2rc) results are excluded from the
# figures entirely; the exclusion is acknowledged in the caption.
EXCLUDE = {"dc2rc": {"GREENLAND", "ICELAND"}}


def _case_delta(df, case, suffix):
    """A strategy's per-region column with the excluded regions blanked out."""
    s = df[f"{case}_{suffix}"]
    drop = EXCLUDE.get(case, set())
    return s.where(~df["region"].isin(drop)) if drop else s


def _mean_pct(df, case):
    """Cost-weighted (system) mean % cost increase for a strategy = total added
    cost / total base cost over the same non-excluded regions (the aggregate %
    increase of the post-processed tables)."""
    d = _case_delta(df, case, "delta_bil_per_yr")
    base = pd.to_numeric(df["base_cost_bil_per_yr"], errors="coerce").where(d.notna())
    den = base.sum()
    return 100.0 * d.sum() / den if den else float("nan")


def _load(csv_path: Path) -> pd.DataFrame:
    df = pd.read_csv(csv_path)
    df = df[df["region"] != "TOTAL"].copy()
    for c in df.columns:
        if c != "region" and not c.endswith("_feasible"):
            df[c] = pd.to_numeric(df[c], errors="coerce")
    # alphabetical region order, shared with every other region figure so the
    # labelled plots act as a key for the unlabelled scatter plots
    return df.sort_values("region", key=lambda s: s.map(region_sort_key))


def _draw_cost_by_region(ax, df):
    """Draw the per-region dodged dot plot on *ax*.  Returns (offscale, excluded)
    lists for the caption note."""
    order = list(df["region"])
    n = len(order)
    xs = np.arange(n)
    offsets = np.linspace(-0.32, 0.32, len(DC_STRATEGIES))

    # Alternating vertical banding groups the five markers belonging to a region.
    add_region_bands(ax, n)

    ax.axhline(10.0, color=MUTED, lw=1.1, ls=(0, (5, 3)), zorder=1)
    # info notes sit in the empty top-right corner (off-scale arrows are on the
    # left/centre) so no extra vertical space is used.
    ax.text(0.985, 0.975, "– – –  +10% = added demand due to data centers",
            transform=ax.transAxes, fontsize=10.5, color=INK_SECONDARY,
            va="top", ha="right")

    cap = OUTLIER_PCT
    offscale = []  # (region, strategy label, value)
    excluded = []  # (region, strategy label) - excluded, noted in caption
    ymin = 0.0     # extend the axis if any case lowers cost (negative delta)
    for (case, label, color, marker), dx in zip(DC_STRATEGIES, offsets):
        vals = df[f"{case}_delta_pct"].to_numpy(dtype=float)
        for j, reg in enumerate(order):
            if reg in EXCLUDE.get(case, set()):
                excluded.append((reg, label))
                vals[j] = np.nan            # excluded: not plotted at all
        x = xs + dx
        onscale = np.where(vals <= cap, vals, np.nan)
        ax.scatter(x, onscale, s=46, color=color, marker=marker, label=label,
                   edgecolors="white", linewidths=0.7, zorder=4)
        fin = onscale[np.isfinite(onscale)]
        if fin.size:
            ymin = min(ymin, float(fin.min()))
        for xi, v, reg in zip(x, vals, order):
            if np.isfinite(v) and v > cap:
                offscale.append((reg, label, v))
                ax.text(xi, cap * 0.99, f"{v:.0f}%$\\uparrow$", ha="center", va="top",
                        fontsize=8.5, color=color, zorder=5)

    ax.axhline(0.0, color=MUTED, lw=0.8, zorder=1)   # 0 = no change; below = cost falls
    ax.set_ylim(ymin * 1.12, cap)
    ax.set_ylabel("Percent increase, relative to base (no DC), in 2050 regional\n"
                  "annual energy cost of powering data centers that consume\n"
                  "the equivalent of 10% of all non-DC end-use energy")
    ax.legend(loc="upper left", ncols=len(DC_STRATEGIES), handletextpad=0.2,
              columnspacing=1.0, borderaxespad=0.3, bbox_to_anchor=(0.0, 1.10))
    style_region_axis(ax, [cap_region(r) for r in order])
    minor_ticks(ax)
    return offscale, excluded


def _print_by_region_caption(offscale, excluded):
    cap = OUTLIER_PCT
    if offscale:
        note = "; ".join(f"{r} {lab.split('(')[0].strip()} {v:,.0f}%" for r, lab, v in offscale)
        print(f"    off-scale (>{cap:.0f}%, up-arrow + value): {note}")
    if excluded:
        note = "; ".join(f"{r} {lab.split('(')[0].strip()}" for r, lab in excluded)
        print(f"    excluded from figure (acknowledge in caption): {note}")


def _draw_strategy_dist(axL, df):
    """Left summary sub-panel: per-strategy distribution of the per-region cost
    increase (strip + median/mean ticks)."""
    ny = len(DC_STRATEGIES)
    axL.grid(axis="x", color=GRID, lw=0.7, zorder=0)
    rng = np.random.default_rng(0)  # deterministic vertical jitter to declutter
    for i, (case, label, color, marker) in enumerate(DC_STRATEGIES):
        y = ny - 1 - i
        vals = _case_delta(df, case, "delta_pct").dropna()
        shown = vals[vals <= DIST_XMAX]
        jitter = rng.uniform(-0.22, 0.22, size=len(shown))
        axL.scatter(shown, y + jitter, s=34, color=color, marker=marker,
                    alpha=0.8, edgecolors="white", linewidths=0.6, zorder=3)
        med = vals.median()
        mean = _mean_pct(df, case)   # cost-weighted system mean (aggregate %)
        # labels sit to the RIGHT of their tick (not centred on it) with a light
        # white backing so they stay readable over the dots.
        lbl = dict(fontsize=8.5, color=INK, ha="left", va="center", zorder=5,
                   bbox=dict(boxstyle="round,pad=0.12", fc="white", ec="none", alpha=0.75))
        if med <= DIST_XMAX:         # median: solid tick, upper half
            axL.plot([med, med], [y + 0.02, y + 0.36], color=INK, lw=2.4, zorder=4)
            axL.text(med + 0.6, y + 0.19, f"med {med:.1f}%", **lbl)
        if mean <= DIST_XMAX:        # mean: dashed tick, lower half
            axL.plot([mean, mean], [y - 0.36, y - 0.02], color=INK, lw=2.4,
                     ls=(0, (2, 1.5)), zorder=4)
            axL.text(mean + 0.6, y - 0.19, f"mean {mean:.1f}%", **lbl)
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
    axL.set_xlabel("Percent cost increase relative to the base (no DC) "
                   "(median & mean marked)")
    axL.tick_params(axis="y", length=0)
    minor_ticks(axL, x=True, y=False)


def _draw_strategy_totals(axR, df):
    """Right summary sub-panel: total added cost over all regions (bars)."""
    ny = len(DC_STRATEGIES)
    axR.grid(axis="x", color=GRID, lw=0.7, zorder=0)
    totals = [_case_delta(df, case, "delta_bil_per_yr").dropna().sum() for case, *_ in DC_STRATEGIES]
    ypos = list(range(ny))[::-1]
    axR.barh(ypos, totals, height=0.66, color=[s[2] for s in DC_STRATEGIES], zorder=3)
    for y, tot in zip(ypos, totals):
        axR.text(tot, y, f" {tot:,.0f}", va="center", fontsize=10, color=INK_SECONDARY)
    axR.set_yticks(range(ny))
    axR.set_yticklabels([])
    axR.set_ylim(-0.6, ny - 0.4)
    axR.set_xlim(0, max(totals) * 1.22)
    axR.set_xlabel("Added energy cost due to data centers (2023 USD \\$Bil/y)")
    axR.tick_params(axis="y", length=0)
    minor_ticks(axR, x=True, y=False)


def _print_summary_caption(df):
    stats = "; ".join(f"{lab} med {_case_delta(df, c, 'delta_pct').median():.1f}%"
                      f"/mean {_mean_pct(df, c):.1f}%" for c, lab, *_ in DC_STRATEGIES)
    print(f"    caption stats (cost increase per region) - {stats}.")


def fig_dc_combined(df: pd.DataFrame, outdir: Path) -> None:
    """Single figure: (a) the strategy summary (distribution + totals) on top,
    (b) the per-region cost dot plot below."""
    fig = plt.figure(figsize=(16.0, 12.4))
    gs = fig.add_gridspec(2, 1, height_ratios=[4.6, 6.8], hspace=0.42)
    gs_top = gs[0].subgridspec(1, 2, width_ratios=[1.25, 1.0], wspace=0.08)
    axL = fig.add_subplot(gs_top[0])
    axR = fig.add_subplot(gs_top[1])
    axB = fig.add_subplot(gs[1])

    _draw_strategy_dist(axL, df)
    _draw_strategy_totals(axR, df)
    offscale, excluded = _draw_cost_by_region(axB, df)

    # panel labels at each panel's top-left plotting corner (matches the other
    # combined figures), not floated out at the figure's left edge.
    axL.text(0.0, 1.045, "a", transform=axL.transAxes,
             fontsize=17, fontweight="bold", va="bottom", ha="left")
    axB.text(0.0, 1.045, "b", transform=axB.transAxes,
             fontsize=17, fontweight="bold", va="bottom", ha="left")

    for ext in ("pdf", "png"):
        fig.savefig(outdir / f"fig_dc_combined.{ext}")
    plt.close(fig)
    print("  wrote fig_dc_combined.pdf/.png")
    _print_summary_caption(df)
    _print_by_region_caption(offscale, excluded)


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
    fig_dc_combined(df, args.outdir)   # single figure: a = strategy summary, b = per-region


if __name__ == "__main__":
    main()

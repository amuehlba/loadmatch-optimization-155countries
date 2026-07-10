"""Publication figures for the data-center cases.

Reads data/results_verification/dc_comparison_summary.csv (written by
export_dc_comparison.py) and produces:

  fig_dc_cost_by_region   dot plot: cost increase of powering the data centers
                          vs the no-data-center optimum, per region and supply
                          strategy (log axis; the +10% added-load share is
                          marked as the "proportional cost" reference).
  fig_dc_strategy_summary two panels: per-strategy distribution of the cost
                          increase (median highlighted), and the total added
                          cost over all regions (non-viable region/strategy
                          combinations excluded and noted).

Strategy identity is encoded redundantly (color + marker shape), so the
figures survive grayscale printing and color-vision deficiency.

Usage:
    python -m scripts.plot_dc_comparison
    python -m scripts.plot_dc_comparison --csv path.csv --outdir figs/
"""
import argparse
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CSV = REPO_ROOT / "data" / "results_verification" / "dc_comparison_summary.csv"

# Strategy -> (CSV prefix, label, color, marker).  Colors are five distinct
# hues with varied lightness; markers give a second identity channel.
STRATEGIES = [
    ("dc1",    "EGS (case 1)",                              "#2e8b57", "o"),
    ("dc2",    "Utility PV + wind\n+ batteries + H2 (case 2)", "#2c7fb8", "s"),
    ("dc2rc",  "Rooftop PV\n+ batteries + H2 (case 3)",        "#7a5195", "^"),
    ("dc2bat", "Case 2, storage:\nbatteries only",             "#e6a117", "D"),
    ("dc2h2",  "Case 2, storage:\nhydrogen only",              "#c51b8a", "v"),
]

plt.rcParams.update({
    "font.size": 9,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "savefig.dpi": 300,
})


def _load(csv_path: Path) -> pd.DataFrame:
    df = pd.read_csv(csv_path)
    df = df[df["region"] != "TOTAL"].copy()
    for c in df.columns:
        if c != "region" and not c.endswith("_feasible"):
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.sort_values("base_cost_bil_per_yr", ascending=True)


def fig_dc_cost_by_region(df: pd.DataFrame, outdir: Path) -> None:
    n = len(df)
    y = range(n)
    fig, ax = plt.subplots(figsize=(8.5, 0.34 * n + 1.6))

    # Light row banding for readability across 5 dots per row
    for i in y:
        if i % 2 == 0:
            ax.axhspan(i - 0.5, i + 0.5, color="#f2f2f2", zorder=0)

    for case, label, color, marker in STRATEGIES:
        vals = df[f"{case}_delta_pct"]
        ax.scatter(vals, list(y), s=42, color=color, marker=marker,
                   label=label, zorder=3, edgecolors="white", linewidths=0.8)

    ax.set_xscale("log")
    ax.axvline(10.0, color="#666666", lw=1.0, ls="--", zorder=2)
    ax.text(10.0, n - 0.2, " +10% = added load share\n (proportional cost)",
            fontsize=7, color="#666666", va="top", ha="left")

    ax.set_yticks(list(y))
    ax.set_yticklabels(df["region"])
    ax.set_ylim(-0.6, n - 0.4)
    ax.set_xlabel("Cost of powering data centers: increase in annual system cost "
                  "vs no-data-center optimum (%, log scale)")
    ax.legend(loc="upper right", frameon=True, framealpha=0.95, fontsize=8)
    ax.set_title(
        "Cost of powering data centers (+10% constant load) by supply strategy",
        fontsize=9)
    fig.tight_layout()
    for ext in ("pdf", "png"):
        fig.savefig(outdir / f"fig_dc_cost_by_region.{ext}", bbox_inches="tight")
    plt.close(fig)
    print("  wrote fig_dc_cost_by_region.pdf/.png")


def fig_dc_strategy_summary(df: pd.DataFrame, outdir: Path) -> None:
    fig, (axL, axR) = plt.subplots(
        1, 2, figsize=(10.5, 3.4), gridspec_kw={"width_ratios": [1.15, 1]})

    ny = len(STRATEGIES)
    # Left: distribution of the per-region cost increase per strategy
    for i, (case, label, color, marker) in enumerate(STRATEGIES):
        vals = df[f"{case}_delta_pct"].dropna()
        yy = [ny - 1 - i] * len(vals)
        axL.scatter(vals, yy, s=26, color=color, marker=marker, alpha=0.75,
                    edgecolors="white", linewidths=0.6, zorder=3)
        med = vals.median()
        axL.plot([med, med], [ny - 1 - i - 0.32, ny - 1 - i + 0.32],
                 color="#222222", lw=2.2, zorder=4)
        axL.text(med, ny - 1 - i + 0.38, f"{med:.1f}%", ha="center",
                 fontsize=7.5, color="#222222")
    axL.set_xscale("log")
    axL.axvline(10.0, color="#666666", lw=1.0, ls="--")
    axL.set_yticks(range(ny))
    axL.set_yticklabels([s[1] for s in reversed(STRATEGIES)], fontsize=8)
    axL.set_xlabel("Cost increase per region (%, log scale; median marked)")
    axL.set_title("Distribution across regions", fontsize=9)

    # Right: total added cost over ALL regions (non-viable combos included:
    # their absolute deltas are moderate compared with the global totals)
    totals = []
    for case, label, color, marker in STRATEGIES:
        totals.append(df[f"{case}_delta_bil_per_yr"].dropna().sum())
    yy = list(range(ny))[::-1]
    axR.barh(yy, totals, height=0.62,
             color=[s[2] for s in STRATEGIES], zorder=3)
    for ypos, tot in zip(yy, totals):
        axR.text(tot, ypos, f" {tot:,.0f}", va="center", fontsize=7.5)
    axR.set_yticks(range(ny))
    axR.set_yticklabels([])
    axR.set_xlim(0, max(totals) * 1.25)
    axR.set_xlabel("Total added cost, all regions (billion \\$/yr)")
    axR.set_title("Aggregate over all 30 regions", fontsize=9)

    fig.suptitle("Powering data centers: supply-strategy comparison", fontsize=10)
    fig.tight_layout()
    for ext in ("pdf", "png"):
        fig.savefig(outdir / f"fig_dc_strategy_summary.{ext}", bbox_inches="tight")
    plt.close(fig)
    print("  wrote fig_dc_strategy_summary.pdf/.png")


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

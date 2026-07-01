"""Publication figures from comparison_summary.csv.

Reads data/results_verification/comparison_summary.csv (written by
export_comparison.py) and produces two figures:

  fig_cost_comparison  — annual system cost by region, trial-and-error vs
                         GA-optimized (log axis so all 30 regions are legible;
                         annotated with the per-region cost reduction).
  fig_solve_time       — GA solve time by region (hours), with the total.

Standalone (only needs pandas/matplotlib + the CSV), so it can be regenerated
without the full per-region result tree.

Usage:
    python -m scripts.plot_comparison
    python -m scripts.plot_comparison --csv path/to/comparison_summary.csv --outdir figs/
"""
import argparse
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CSV = REPO_ROOT / "data" / "results_verification" / "comparison_summary.csv"

# Neutral, publication-appropriate colours/labels.
C_BASELINE = "#b0b7bf"   # grey
C_GA = "#2c7fb8"         # blue
LABEL_BASELINE = "Trial-and-error"
LABEL_GA = "GA-optimized"

plt.rcParams.update({
    "font.size": 9,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "savefig.dpi": 300,
})


def _load(csv_path: Path) -> pd.DataFrame:
    df = pd.read_csv(csv_path)
    df = df[~df["region"].astype(str).str.startswith("TOTAL")].copy()
    for c in ("baseline_cost_bil_per_yr", "ga_cost_bil_per_yr",
              "pct_savings", "optimize_seconds", "total_seconds"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def fig_cost_comparison(df: pd.DataFrame, outdir: Path) -> None:
    d = df.dropna(subset=["baseline_cost_bil_per_yr", "ga_cost_bil_per_yr"]).copy()
    d = d.sort_values("baseline_cost_bil_per_yr", ascending=True)  # largest at top
    n = len(d)
    y = range(n)
    h = 0.38

    fig, ax = plt.subplots(figsize=(8.5, 0.42 * n + 1.2))
    ax.barh([i + h / 2 for i in y], d["baseline_cost_bil_per_yr"], height=h,
            color=C_BASELINE, label=LABEL_BASELINE, zorder=3)
    ax.barh([i - h / 2 for i in y], d["ga_cost_bil_per_yr"], height=h,
            color=C_GA, label=LABEL_GA, zorder=3)

    ax.set_xscale("log")
    xmax = float(d["baseline_cost_bil_per_yr"].max())
    ax.set_xlim(right=xmax * 3.2)

    # Per-region cost reduction, annotated at the end of each pair.
    for i, (_, r) in zip(y, d.iterrows()):
        red = r["pct_savings"]
        xpos = max(r["baseline_cost_bil_per_yr"], r["ga_cost_bil_per_yr"]) * 1.15
        ax.text(xpos, i, "−{:.1f}%".format(red), va="center", ha="left",
                fontsize=7, color="#08519c")

    ax.set_yticks(list(y))
    ax.set_yticklabels(d["region"])
    ax.set_xlabel("Annual system cost (billion \\$/yr, log scale)")
    ax.legend(loc="lower right", frameon=False)

    tot_b = d["baseline_cost_bil_per_yr"].sum()
    tot_g = d["ga_cost_bil_per_yr"].sum()
    red_tot = 100.0 * (tot_b - tot_g) / tot_b
    ax.set_title(
        "Annual system cost by region: trial-and-error vs GA-optimized\n"
        "Total over {} regions: {:,.0f} → {:,.0f} billion \\$/yr "
        "(−{:.1f}%);  annotations = per-region cost reduction".format(
            n, tot_b, tot_g, red_tot),
        fontsize=9)
    fig.tight_layout()
    for ext in ("pdf", "png"):
        fig.savefig(outdir / f"fig_cost_comparison.{ext}", bbox_inches="tight")
    plt.close(fig)
    print(f"  wrote fig_cost_comparison.pdf/.png  (total −{red_tot:.1f}% over {n} regions)")


def fig_solve_time(df: pd.DataFrame, outdir: Path) -> None:
    d = df.dropna(subset=["optimize_seconds"]).copy()
    d["hours"] = d["optimize_seconds"] / 3600.0
    d = d.sort_values("hours", ascending=True)
    n = len(d)
    y = range(n)

    fig, ax = plt.subplots(figsize=(8.0, 0.34 * n + 1.2))
    ax.barh(list(y), d["hours"], color="#41ab5d", zorder=3)
    ax.set_yticks(list(y))
    ax.set_yticklabels(d["region"])
    ax.set_xlabel("GA solve time (hours)")
    total_h = d["hours"].sum()
    ax.set_title("GA solve time by region  (total {:.0f} h over {} regions)".format(
        total_h, n), fontsize=9)
    fig.tight_layout()
    for ext in ("pdf", "png"):
        fig.savefig(outdir / f"fig_solve_time.{ext}", bbox_inches="tight")
    plt.close(fig)
    print(f"  wrote fig_solve_time.pdf/.png  (total {total_h:.0f} h over {n} regions)")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    ap.add_argument("--outdir", type=Path,
                    default=REPO_ROOT / "data" / "results_verification")
    args = ap.parse_args(argv)
    args.outdir.mkdir(parents=True, exist_ok=True)
    df = _load(args.csv)
    missing = df[df["baseline_cost_bil_per_yr"].isna()]["region"].tolist()
    if missing:
        print(f"  [WARN] regions missing a baseline cost (excluded from cost fig): {missing}")
    fig_cost_comparison(df, args.outdir)
    fig_solve_time(df, args.outdir)


if __name__ == "__main__":
    main()

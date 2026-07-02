"""Publication figures from comparison_summary.csv.

Reads data/results_verification/comparison_summary.csv (written by
export_comparison.py) and produces three figures:

  fig_cost_comparison  — annual system cost by region, trial-and-error vs
                         GA-optimized (log axis so all 30 regions are legible;
                         annotated with the per-region cost reduction).
  fig_land_comparison  — new land area for WWS (wind spacing + footprint, % of
                         regional land) by region, trial-and-error vs
                         GA-optimized; annotated with the relative change and
                         flagging regions that exceed the baseline share.
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

# Neutral, publication-appropriate colours/labels.  Color follows the entity
# across all figures: baseline grey, GA-from-trial-and-error blue, GA-from-
# scratch orange.
C_BASELINE = "#b0b7bf"     # grey
C_GA = "#2c7fb8"           # blue
C_GA_SCRATCH = "#e08214"   # orange
LABEL_BASELINE = "Trial-and-error"
LABEL_GA = "GA (from trial-and-error)"
LABEL_GA_SCRATCH = "GA (from scratch)"

plt.rcParams.update({
    "font.size": 9,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "savefig.dpi": 300,
})


def _load(csv_path: Path) -> pd.DataFrame:
    df = pd.read_csv(csv_path)
    df = df[~df["region"].astype(str).str.startswith("TOTAL")].copy()
    numeric = ["baseline_cost_bil_per_yr", "ga_cost_bil_per_yr",
               "pct_savings", "optimize_seconds", "total_seconds",
               "ga_scratch_cost_bil_per_yr", "scratch_vs_ga_pct",
               "scratch_optimize_seconds",
               "bl_newland_pct_regland", "ga_newland_pct_regland",
               "land_delta_pp"]
    for c in numeric:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        else:
            df[c] = float("nan")
    return df


def fig_cost_comparison(df: pd.DataFrame, outdir: Path) -> None:
    d = df.dropna(subset=["baseline_cost_bil_per_yr", "ga_cost_bil_per_yr"]).copy()
    d = d.sort_values("baseline_cost_bil_per_yr", ascending=True)  # largest at top
    n = len(d)
    y = range(n)
    has_scratch = d["ga_scratch_cost_bil_per_yr"].notna().any()

    if has_scratch:
        h = 0.27
        row_h = 0.52
    else:
        h = 0.38
        row_h = 0.42
    fig, ax = plt.subplots(figsize=(8.5, row_h * n + 1.2))
    ax.barh([i + (h if has_scratch else h / 2) for i in y],
            d["baseline_cost_bil_per_yr"], height=h,
            color=C_BASELINE, label=LABEL_BASELINE, zorder=3)
    ax.barh([i if has_scratch else i - h / 2 for i in y],
            d["ga_cost_bil_per_yr"], height=h,
            color=C_GA, label=LABEL_GA, zorder=3)
    if has_scratch:
        ax.barh([i - h for i in y], d["ga_scratch_cost_bil_per_yr"], height=h,
                color=C_GA_SCRATCH, label=LABEL_GA_SCRATCH, zorder=3)

    ax.set_xscale("log")
    xmax = float(d["baseline_cost_bil_per_yr"].max())
    ax.set_xlim(right=xmax * 3.2)

    # Per-region cost reduction (GA from trial-and-error vs baseline),
    # annotated at the end of each group.
    for i, (_, r) in zip(y, d.iterrows()):
        red = r["pct_savings"]
        vals = [r["baseline_cost_bil_per_yr"], r["ga_cost_bil_per_yr"],
                r.get("ga_scratch_cost_bil_per_yr")]
        xpos = max(v for v in vals if pd.notna(v)) * 1.15
        ax.text(xpos, i, "−{:.1f}%".format(red), va="center", ha="left",
                fontsize=7, color="#08519c")

    ax.set_yticks(list(y))
    ax.set_yticklabels(d["region"])
    ax.set_xlabel("Annual system cost (billion \\$/yr, log scale)")
    ax.legend(loc="lower right", frameon=False)

    tot_b = d["baseline_cost_bil_per_yr"].sum()
    tot_g = d["ga_cost_bil_per_yr"].sum()
    red_tot = 100.0 * (tot_b - tot_g) / tot_b
    title = ("Annual system cost by region: trial-and-error vs GA-optimized\n"
             "Total over {} regions: {:,.0f} → {:,.0f} billion \\$/yr "
             "(−{:.1f}%);  annotations = per-region cost reduction".format(
                 n, tot_b, tot_g, red_tot))
    if has_scratch:
        both = d.dropna(subset=["ga_scratch_cost_bil_per_yr"])
        ts = both["ga_scratch_cost_bil_per_yr"].sum()
        tg = both["ga_cost_bil_per_yr"].sum()
        gap = 100.0 * (ts - tg) / tg if tg else float("nan")
        title += ("\nGA from scratch (FAC=1, storage=0 start): total {:,.0f} "
                  "billion \\$/yr, {:+.1f}% vs GA from trial-and-error "
                  "({} regions)".format(ts, gap, len(both)))
    ax.set_title(title, fontsize=9)
    fig.tight_layout()
    for ext in ("pdf", "png"):
        fig.savefig(outdir / f"fig_cost_comparison.{ext}", bbox_inches="tight")
    plt.close(fig)
    print(f"  wrote fig_cost_comparison.pdf/.png  (total −{red_tot:.1f}% over {n} regions"
          + (", incl. scratch series)" if has_scratch else ")"))


def fig_land_comparison(df: pd.DataFrame, outdir: Path,
                        tolerance: float = 0.10) -> None:
    """New land area for WWS (wind spacing + footprint, % of each region's own
    land) by region, trial-and-error vs GA-optimized.  Same layout and colors
    as the cost figure; annotations give the relative change, colored dark red
    when the optimized share exceeds baseline by more than the tolerance."""
    d = df.dropna(subset=["bl_newland_pct_regland", "ga_newland_pct_regland"]).copy()
    if d.empty:
        print("  [SKIP] fig_land_comparison: no land data in the CSV "
              "(regenerate comparison_summary.csv with the land-aware exporter).")
        return
    d = d.sort_values("bl_newland_pct_regland", ascending=True)  # largest at top
    n = len(d)
    y = range(n)
    h = 0.38

    fig, ax = plt.subplots(figsize=(8.5, 0.42 * n + 1.2))
    ax.barh([i + h / 2 for i in y], d["bl_newland_pct_regland"], height=h,
            color=C_BASELINE, label=LABEL_BASELINE, zorder=3)
    ax.barh([i - h / 2 for i in y], d["ga_newland_pct_regland"], height=h,
            color=C_GA, label=LABEL_GA, zorder=3)

    ax.set_xscale("log")
    xmax = float(d[["bl_newland_pct_regland", "ga_newland_pct_regland"]].max().max())
    ax.set_xlim(right=xmax * 3.2)

    n_flagged = 0
    for i, (_, r) in zip(y, d.iterrows()):
        bl, ga = r["bl_newland_pct_regland"], r["ga_newland_pct_regland"]
        rel = (ga - bl) / bl * 100 if bl else float("nan")
        exceeded = bl > 0 and ga > bl * (1.0 + tolerance)
        n_flagged += exceeded
        xpos = max(bl, ga) * 1.15
        ax.text(xpos, i, "{:+.1f}%".format(rel), va="center", ha="left",
                fontsize=7, color="#a63603" if exceeded else "#08519c")

    ax.set_yticks(list(y))
    ax.set_yticklabels(d["region"])
    ax.set_xlabel("New land for WWS: wind spacing + footprint "
                  "(% of regional land area, log scale)")
    ax.legend(loc="lower right", frameon=False)

    check = ("all {} regions within +{:.0f}% of baseline".format(n, 100 * tolerance)
             if n_flagged == 0 else
             "{} of {} regions exceed baseline by >{:.0f}% (red)".format(
                 n_flagged, n, 100 * tolerance))
    ax.set_title(
        "New land area by region: trial-and-error vs GA-optimized\n"
        "annotations = relative change in new-land share;  " + check,
        fontsize=9)
    fig.tight_layout()
    for ext in ("pdf", "png"):
        fig.savefig(outdir / f"fig_land_comparison.{ext}", bbox_inches="tight")
    plt.close(fig)
    print(f"  wrote fig_land_comparison.pdf/.png  ({check})")


def fig_solve_time(df: pd.DataFrame, outdir: Path) -> None:
    d = df.dropna(subset=["optimize_seconds"]).copy()
    d["hours"] = d["optimize_seconds"] / 3600.0
    d["hours_scratch"] = d["scratch_optimize_seconds"] / 3600.0
    d = d.sort_values("hours", ascending=True)
    n = len(d)
    y = range(n)
    has_scratch = d["hours_scratch"].notna().any()
    total_h = d["hours"].sum()

    fig, ax = plt.subplots(figsize=(8.0, (0.42 if has_scratch else 0.34) * n + 1.2))
    if has_scratch:
        h = 0.38
        ax.barh([i + h / 2 for i in y], d["hours"], height=h,
                color=C_GA, label=LABEL_GA, zorder=3)
        ax.barh([i - h / 2 for i in y], d["hours_scratch"], height=h,
                color=C_GA_SCRATCH, label=LABEL_GA_SCRATCH, zorder=3)
        ax.legend(loc="lower right", frameon=False)
        total_s = d["hours_scratch"].sum()
        title = ("GA solve time by region  (totals: {:.0f} h from "
                 "trial-and-error, {:.0f} h from scratch; {} regions)".format(
                     total_h, total_s, n))
    else:
        ax.barh(list(y), d["hours"], color=C_GA, zorder=3)
        title = "GA solve time by region  (total {:.0f} h over {} regions)".format(
            total_h, n)
    ax.set_yticks(list(y))
    ax.set_yticklabels(d["region"])
    ax.set_xlabel("GA solve time (hours)")
    ax.set_title(title, fontsize=9)
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
    ap.add_argument("--land-tolerance", type=float, default=0.10,
                    help="Relative tolerance for flagging optimized new-land "
                         "share above baseline (default: %(default)s).")
    args = ap.parse_args(argv)
    args.outdir.mkdir(parents=True, exist_ok=True)
    df = _load(args.csv)
    missing = df[df["baseline_cost_bil_per_yr"].isna()]["region"].tolist()
    if missing:
        print(f"  [WARN] regions missing a baseline cost (excluded from cost fig): {missing}")
    fig_cost_comparison(df, args.outdir)
    fig_land_comparison(df, args.outdir, tolerance=args.land_tolerance)
    fig_solve_time(df, args.outdir)


if __name__ == "__main__":
    main()

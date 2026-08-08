"""Region-level WWS-vs-BAU figure (base / from-scratch optimization paper).

Reads data/results_verification/bau_comparison_summary.csv (written by
export_bau_comparison.py) and draws, per region, the 2050 business-as-usual
social cost of energy decomposed into private energy + air-pollution health +
climate (2013 US cents/kWh), with the optimized WWS energy cost overlaid.  The
gap between the BAU stack and the WWS marker is the societal saving from the WWS
transition.  Regions are ordered by BAU social cost (largest first).

Usage
-----
    python -m scripts.plot_bau_comparison
    python -m scripts.plot_bau_comparison --csv path.csv --outdir figs/
"""
import argparse
from pathlib import Path

import pandas as pd
import matplotlib.pyplot as plt

from scripts.plot_style import (apply_style, cap_region, add_region_bands,
                                style_region_axis, minor_ticks, GRID, MUTED,
                                INK_SECONDARY, C_GA)

apply_style()

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CSV = REPO_ROOT / "data" / "results_verification" / "bau_comparison_summary.csv"
DEFAULT_OUTDIR = REPO_ROOT / "data" / "results_verification"

# BAU social-cost components (categorical): private energy, then the two
# externalities WWS removes.  WWS itself uses the shared headline blue (C_GA).
C_ENERGY = "#6e7377"    # neutral grey  (private energy cost)
C_HEALTH = "#c98500"    # amber         (air-pollution health cost)
C_CLIMATE = "#e34948"   # red           (climate cost)

_NUM = ["bau_lcoe_c_per_kwh", "bau_health_c_per_kwh", "bau_climate_c_per_kwh",
        "bau_social_c_per_kwh", "wws_lcoe_ga_c_per_kwh"]


def _load(csv_path: Path) -> pd.DataFrame:
    df = pd.read_csv(csv_path)
    df = df[~df["region"].astype(str).str.startswith("TOTAL")].copy()
    for c in _NUM:
        df[c] = pd.to_numeric(df.get(c), errors="coerce")
    df = df[df["bau_social_c_per_kwh"].notna() & (df["bau_social_c_per_kwh"] > 0)]
    df = df.sort_values("bau_social_c_per_kwh", ascending=False).reset_index(drop=True)
    return df


def fig_wws_vs_bau(df: pd.DataFrame, outdir: Path) -> None:
    regions = list(df["region"])
    n = len(regions)
    x = list(range(n))
    energy = df["bau_lcoe_c_per_kwh"].values
    health = df["bau_health_c_per_kwh"].values
    climate = df["bau_climate_c_per_kwh"].values
    wws = df["wws_lcoe_ga_c_per_kwh"].values

    fig, ax = plt.subplots(figsize=(max(9.0, 0.42 * n + 3.0), 6.2))
    add_region_bands(ax, n)
    ax.grid(axis="y", color=GRID, linewidth=0.6, zorder=0)

    bw = 0.62
    # Stacked BAU social cost; a thin white edge is the 2px surface gap between
    # adjacent fills so the three components stay legible even when thin.
    ax.bar(x, energy, bw, color=C_ENERGY, edgecolor="white", linewidth=0.6,
           label="BAU private energy", zorder=2)
    ax.bar(x, health, bw, bottom=energy, color=C_HEALTH, edgecolor="white",
           linewidth=0.6, label="BAU air-pollution health", zorder=2)
    ax.bar(x, climate, bw, bottom=energy + health, color=C_CLIMATE,
           edgecolor="white", linewidth=0.6, label="BAU climate", zorder=2)

    # WWS optimized energy cost (headline), a marker well below the BAU stack.
    ax.scatter(x, wws, s=36, color=C_GA, edgecolor="white", linewidth=0.8,
               zorder=4, label="WWS optimized")

    ax.set_ylabel("2050 energy social cost (2013 US cents/kWh)")
    ax.set_ylim(0, None)
    style_region_axis(ax, [cap_region(r) for r in regions])
    minor_ticks(ax, y=True)
    ax.legend(loc="upper right", ncol=1, handletextpad=0.5, borderaxespad=0.8)

    for ext in ("pdf", "png"):
        fig.savefig(outdir / "fig_wws_vs_bau.{}".format(ext))
    plt.close(fig)
    print("  wrote fig_wws_vs_bau.pdf/.png")
    _print_caption(df)


def _print_caption(df: pd.DataFrame) -> None:
    tot_bau = pd.to_numeric(df.get("bau_total_bil_per_yr"), errors="coerce").sum()
    tot_wws = pd.to_numeric(df.get("wws_total_ga_bil_per_yr"), errors="coerce").sum()
    share = (100.0 * tot_wws / tot_bau) if tot_bau else float("nan")
    print("  caption: Per-region 2050 business-as-usual (BAU) social cost of "
          "energy, decomposed into private energy, air-pollution health, and "
          "climate cost (2013 US cents/kWh), with the optimized WWS energy cost "
          "overlaid (blue). Regions ordered by BAU social cost. Across "
          "{} regions the optimized WWS total societal cost is {:.0f}% of BAU "
          "(${:.0f}B/yr vs ${:.0f}B/yr).".format(len(df), share, tot_wws, tot_bau))


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    ap.add_argument("--outdir", type=Path, default=DEFAULT_OUTDIR)
    args = ap.parse_args(argv)

    if not args.csv.exists():
        print("No BAU comparison CSV at {} (run export_bau_comparison first).".format(args.csv))
        return
    df = _load(args.csv)
    if df.empty:
        print("No regions with BAU data in", args.csv)
        return
    args.outdir.mkdir(parents=True, exist_ok=True)
    fig_wws_vs_bau(df, args.outdir)


if __name__ == "__main__":
    main()

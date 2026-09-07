"""BAU-vs-WWS figure (base optimization paper), 2020 USD, two panels.

Reads the PI's provided tables in data/results_verification/Tables/, sheet
BAULCOE (per-region source block), and draws two aligned per-region panels
(same region order in both):

  A) per unit energy (2020 US cents/kWh): 2050 business-as-usual social cost
     decomposed into private energy + air-pollution health + climate, with the
     WWS energy cost overlaid for the trial-and-error model and the GA Baseline
     optimization.

  B) relative reductions vs BAU (%): per region, the reduction in aggregate
     private energy cost (WWS vs BAU energy $/yr) and the reduction in end-use
     energy demand.  Shown as percentages so regions of vastly different size
     are comparable; the demand reduction is a large part of why the aggregate
     energy-cost reduction is bigger than the per-kWh one in panel A.

WWS (Trial-and-Error) is read from Tables-155Countries-Base-Trial-Error-New.xlsx
and WWS (Baseline) from Tables-155Countries-Opt-New.xlsx; BAU is the shared
reference (verified identical in both).  Panel B uses WWS (Baseline).

Usage
-----
    python -m scripts.plot_bau_comparison
"""
import argparse
from pathlib import Path

import pandas as pd
import matplotlib.pyplot as plt
from openpyxl import load_workbook

from scripts.plot_style import (apply_style, add_region_bands, style_region_axis,
                                minor_ticks, GRID, MUTED, INK, C_GA)

apply_style()

REPO_ROOT = Path(__file__).resolve().parent.parent
TABLES = REPO_ROOT / "data" / "results_verification" / "Tables"
OPT_TABLE = TABLES / "Tables-155Countries-Opt-New.xlsx"               # WWS (Baseline)
TAE_TABLE = TABLES / "Tables-155Countries-Base-Trial-Error-New.xlsx"  # WWS (Trial-and-Error)
DEFAULT_OUTDIR = REPO_ROOT / "data" / "results_verification"

# BAULCOE left block (the per-region source that the paper-layout AC:BL block
# references): region names in col A, rows 2..31; "All regions" aggregate in
# row 32.  Reading the source columns here avoids a stray reference in the
# transposed block, where Haiti's AC-block WWS cell (AT6) points at BAU social
# (=G14) instead of WWS (=H14); the source column H is correct for all regions.
_ROW_FIRST, _ROW_LAST, _ROW_ALL = 2, 31, 32
_C_NAME = 1                                                   # A
_C_LOAD_BAU, _C_LOAD_WWS = 2, 3                               # B,C (GW)
_C_ENERGY, _C_HEALTH, _C_CLIMATE, _C_WWS = 4, 5, 6, 8        # D,E,F,H (cents/kWh)
_C_ENERGY_BIL, _C_BAU_SOCIAL_BIL, _C_WWS_BIL = 22, 25, 26     # V,Y,Z ($bil/yr)

C_ENERGY = "#6e7377"    # grey   BAU private energy
C_HEALTH = "#c98500"    # amber  BAU air-pollution health
C_CLIMATE = "#e34948"   # red    BAU climate
C_DEMAND = "#6a3d9a"    # purple end-use demand reduction (panel B)
# WWS (Baseline) = C_GA blue; WWS (Trial-and-Error) = INK dark.


def _read_baulcoe(path: Path):
    ws = load_workbook(path, data_only=True)["BAULCOE"]
    per = {}
    for r in range(_ROW_FIRST, _ROW_LAST + 1):
        name = ws.cell(r, _C_NAME).value
        if not name:
            continue
        per[name] = dict(
            energy=ws.cell(r, _C_ENERGY).value,
            health=ws.cell(r, _C_HEALTH).value,
            climate=ws.cell(r, _C_CLIMATE).value,
            wws=ws.cell(r, _C_WWS).value,
            energy_bil=ws.cell(r, _C_ENERGY_BIL).value,
            wws_bil=ws.cell(r, _C_WWS_BIL).value,
            load_bau=ws.cell(r, _C_LOAD_BAU).value,
            load_wws=ws.cell(r, _C_LOAD_WWS).value)
    agg = dict(
        bau_social_bil=ws.cell(_ROW_ALL, _C_BAU_SOCIAL_BIL).value,
        bau_energy_bil=ws.cell(_ROW_ALL, _C_ENERGY_BIL).value,
        bau_load_gw=ws.cell(_ROW_ALL, _C_LOAD_BAU).value,
        wws_load_gw=ws.cell(_ROW_ALL, _C_LOAD_WWS).value,
        wws_bil=ws.cell(_ROW_ALL, _C_WWS_BIL).value)
    return per, agg


def _load(opt_path: Path = OPT_TABLE, tae_path: Path = TAE_TABLE):
    opt, opt_agg = _read_baulcoe(opt_path)
    tae, tae_agg = _read_baulcoe(tae_path)
    rows = []
    for name, o in opt.items():
        t = tae.get(name, {})
        rows.append(dict(
            region=name,
            bau_energy=o["energy"], bau_health=o["health"], bau_climate=o["climate"],
            wws_baseline=o["wws"], wws_tae=t.get("wws"),
            bau_energy_bil=o["energy_bil"], wws_bil_baseline=o["wws_bil"],
            wws_bil_tae=t.get("wws_bil"),
            load_bau=o["load_bau"], load_wws=o["load_wws"]))
    df = pd.DataFrame(rows)
    for c in [c for c in df.columns if c != "region"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["bau_social"] = df.bau_energy + df.bau_health + df.bau_climate
    # relative reductions vs BAU (%)
    df["cost_reduction_baseline_pct"] = 100.0 * (1.0 - df.wws_bil_baseline / df.bau_energy_bil)
    df["cost_reduction_tae_pct"] = 100.0 * (1.0 - df.wws_bil_tae / df.bau_energy_bil)
    df["demand_reduction_pct"] = 100.0 * (1.0 - df.load_wws / df.load_bau)
    df = df.sort_values("bau_social", ascending=False).reset_index(drop=True)
    agg = dict(bau_social_bil=opt_agg["bau_social_bil"],
               bau_energy_bil=opt_agg["bau_energy_bil"],
               bau_load_gw=opt_agg["bau_load_gw"],
               wws_load_gw=opt_agg["wws_load_gw"],
               wws_baseline_bil=opt_agg["wws_bil"],
               wws_tae_bil=tae_agg["wws_bil"])
    return df, agg


def _draw_perkwh(ax, df: pd.DataFrame) -> None:
    """Panel A: per-region social cost per unit energy (2020 cents/kWh)."""
    n = len(df)
    x = list(range(n))
    energy = df.bau_energy.values
    health = df.bau_health.values
    add_region_bands(ax, n)
    ax.grid(axis="y", color=GRID, linewidth=0.6, zorder=0)
    bw = 0.62
    ax.bar(x, energy, bw, color=C_ENERGY, edgecolor="white", linewidth=0.6,
           label="BAU private energy", zorder=2)
    ax.bar(x, health, bw, bottom=energy, color=C_HEALTH, edgecolor="white",
           linewidth=0.6, label="BAU air-pollution health", zorder=2)
    ax.bar(x, df.bau_climate.values, bw, bottom=energy + health, color=C_CLIMATE,
           edgecolor="white", linewidth=0.6, label="BAU climate", zorder=2)
    dx = 0.13
    ax.scatter([xi - dx for xi in x], df.wws_tae.values, s=30, color=INK, marker="D",
               edgecolor="white", linewidth=0.8, zorder=4, label="WWS (Trial-and-Error)")
    ax.scatter([xi + dx for xi in x], df.wws_baseline.values, s=32, color=C_GA, marker="o",
               edgecolor="white", linewidth=0.8, zorder=4, label="WWS (Baseline)")
    ax.set_ylabel("2050 energy social cost\n(2020 US cents/kWh)")
    ax.set_ylim(0, None)
    minor_ticks(ax, y=True)
    ax.legend(loc="upper right", ncol=1, handletextpad=0.5, borderaxespad=0.8, fontsize=10)


def _draw_reductions(ax, df: pd.DataFrame) -> None:
    """Panel B: per-region relative reductions vs BAU (%) — aggregate energy
    cost for both WWS scenarios, plus end-use energy demand."""
    n = len(df)
    x = list(range(n))
    cost_t = df.cost_reduction_tae_pct.values
    cost_b = df.cost_reduction_baseline_pct.values
    dem = df.demand_reduction_pct.values
    add_region_bands(ax, n)
    ax.grid(axis="y", color=GRID, linewidth=0.6, zorder=0)
    w = 0.26
    ax.bar([xi - 0.27 for xi in x], cost_t, w, color=INK, edgecolor="white",
           linewidth=0.5, zorder=2, label="Aggregate private energy cost, WWS (Trial-and-Error)")
    ax.bar(list(x), cost_b, w, color=C_GA, edgecolor="white",
           linewidth=0.5, zorder=2, label="Aggregate private energy cost, WWS (Baseline)")
    ax.bar([xi + 0.27 for xi in x], dem, w, color=C_DEMAND, edgecolor="white",
           linewidth=0.5, zorder=2, label="End-use energy demand")
    lo = min(0.0, float(min(cost_t.min(), cost_b.min(), dem.min())))
    if lo < 0:
        ax.axhline(0, color=MUTED, lw=0.8, zorder=3)
    ax.set_ylim(lo - 4 if lo < 0 else 0, 100)
    ax.set_ylabel("Relative reduction vs BAU (%)")
    style_region_axis(ax, list(df.region))
    minor_ticks(ax, y=True)
    ax.legend(loc="upper right", ncol=1, handletextpad=0.5, borderaxespad=0.8, fontsize=9.5)


def fig_wws_vs_bau(df: pd.DataFrame, agg: dict, outdir: Path) -> None:
    # Same layout as fig_cost_comparison: two stacked panels sharing the region
    # axis (labels only on the bottom), panel labels at each plotting corner.
    fig, (axA, axB) = plt.subplots(
        2, 1, figsize=(13.5, 8.4), sharex=True,
        gridspec_kw={"height_ratios": [1.35, 1.0], "hspace": 0.12})
    _draw_perkwh(axA, df)
    _draw_reductions(axB, df)          # sets the shared region axis on axB
    axA.tick_params(axis="x", length=0)
    axA.text(0.0, 1.02, "a", transform=axA.transAxes, fontweight="bold",
             fontsize=14, va="bottom")
    axB.text(0.0, 1.02, "b", transform=axB.transAxes, fontweight="bold",
             fontsize=14, va="bottom")
    for ext in ("pdf", "png"):
        fig.savefig(outdir / "fig_wws_vs_bau.{}".format(ext))
    plt.close(fig)
    print("  wrote fig_wws_vs_bau.pdf/.png")
    _print_caption(df, agg)


def _print_caption(df: pd.DataFrame, agg: dict) -> None:
    b, en, wb = agg["bau_social_bil"], agg["bau_energy_bil"], agg["wws_baseline_bil"]
    print("  caption: Business-as-usual (BAU) social cost of energy versus WWS by "
          "region, 2020 USD: BAU private energy, air-pollution health, and climate "
          "cost per unit energy (2020 US cents/kWh), with WWS for the trial-and-error "
          "model and GA from the trial-and-error start (a); relative reduction versus "
          "BAU of aggregate private energy cost for the trial-and-error model and GA "
          "from the trial-and-error start, and of end-use energy demand (b).")
    print("  [all-regions: private energy cost -{:.0f}% (Baseline), end-use demand "
          "-{:.0f}%, societal cost -{:.0f}%]".format(
              100 * (1 - wb / en),
              100 * (1 - agg["wws_load_gw"] / agg["bau_load_gw"]),
              100 * (1 - wb / b)))


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--opt-table", type=Path, default=OPT_TABLE)
    ap.add_argument("--tae-table", type=Path, default=TAE_TABLE)
    ap.add_argument("--outdir", type=Path, default=DEFAULT_OUTDIR)
    args = ap.parse_args(argv)
    for p in (args.opt_table, args.tae_table):
        if not p.exists():
            print("Missing PI table:", p)
            return
    df, agg = _load(args.opt_table, args.tae_table)
    args.outdir.mkdir(parents=True, exist_ok=True)
    fig_wws_vs_bau(df, agg, args.outdir)


if __name__ == "__main__":
    main()

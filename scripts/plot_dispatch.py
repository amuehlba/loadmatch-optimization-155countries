"""SI dispatch + storage state-of-charge plots, per region, from the Fortran output.

Each region's ``fortran_optimal_run.out`` (the LoadMatch stdout) carries, besides
the summary sections, the hourly time series the PI exports to the
``wwshourly.<REGION>`` xlsx and plots:

  * hourly dispatch rows   ``X   <GMTDAY> <hour> ...``  (powerworld.f FORMAT 262)
  * daily storage-SOC rows ``XGMTD <day> ...``          (powerworld.f FORMAT 210)

For each region this writes two SI figures:

  <REGION>_dispatch.{pdf,png}  hourly load+storage/T&D/curtailment (blue dots)
                               vs total WWS generation before losses (red line)
  <REGION>_soc.{pdf,png}       storage state of charge (TWh) by technology, daily

Reading the retained ``.out`` means every optimized region can be plotted with no
need to regenerate ``wwshourly``.  If you prefer the dedicated file, pass one with
``--wwshourly`` (same ``X`` rows); the SOC always comes from the ``.out``.

Column order is taken verbatim from powerworld.f (FORMAT 261/262 and 208/210).

Usage (from repo root, on Sherlock)
-----------------------------------
    python -m scripts.plot_dispatch                          # all regions found
    python -m scripts.plot_dispatch --regions EUROPE CHINA
    python -m scripts.plot_dispatch --day-window 100 200     # also a zoomed dispatch
"""
import argparse
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

REPO_ROOT = Path(__file__).resolve().parent.parent
RESULTS = REPO_ROOT / "data" / "results_verification"
DEFAULT_OUTDIR = RESULTS / "SI_dispatch"

# Dispatch row, FORMAT 262: 'X', GMTDAY, hour, then 27 columns (header FORMAT 261).
_DISP = ["gmtday", "hour", "inflx", "flx", "h2", "flxh2", "totload", "origload",
         "loss_in_stor", "allstorloss", "d_stor", "loss_ug", "all_ug_loss", "d_ug",
         "d_h2", "curt", "load_chg_loss_curt", "sup_bef_td", "wind", "solpv_csp",
         "hydro", "wav_geo_tid", "sol_heat", "geo_heat", "tdloss", "sup_aft_td",
         "cold", "warm", "hrs"]
_DISP_BLUE = "load_chg_loss_curt"   # Load + changes in storage + losses (T&D, storage) + curtailment
_DISP_RED = "sup_bef_td"            # Total WWS electricity + heat generation before losses

# SOC row, FORMAT 210: 'XGMTD', igmtd, then 22 values (header FORMAT 208).
_SOC = ["igmtd", "demandnew", "deminflex", "remaindem", "supply", "suppht", "warm",
        "cold", "flexload", "flexh2", "flexsum", "csp", "phs", "battery", "storf",
        "storo", "storh", "utes", "hydro", "h2", "cumshed", "hydisch", "utesdisch"]
# (label, key, colour) for the SOC plot; only series with nonzero energy are drawn.
# CSP/PHS/battery/H2/UTES labels are certain; storf/storh are thermal stores whose
# exact medium can be renamed to match the PI's convention if needed.
_SOC_SERIES = [
    ("Battery",              "battery", "#2a78d6"),
    ("Pumped hydro",         "phs",     "#008300"),
    ("CSP (thermal)",        "csp",     "#c98500"),
    ("Hydrogen",             "h2",      "#e34948"),
    ("Seasonal heat (UTES)", "utes",    "#6a3d9a"),
    ("Hot-water / heat",     "storh",   "#eb6834"),
    ("Cold / PCM",           "storf",   "#17becf"),
]

C_BLUE = "#1f4fd8"
C_RED = "#e02318"
_XLABEL = ("GMT day of simulation for {region}, "
           "starting 0 GMT January 1, 2050, ending December 31, 2052")


def _display_region(name: str) -> str:
    return str(name).replace("-", " ").title()


def parse_out(path: Path):
    """Parse the hourly dispatch (X rows) and daily SOC (XGMTD rows) from a
    LoadMatch stdout/.out file.  Returns (disp, soc) dicts of column -> list."""
    disp = {k: [] for k in _DISP}
    soc = {k: [] for k in _SOC}
    with open(path, errors="replace") as fh:
        for ln in fh:
            if ln.startswith("XGMTD"):
                t = ln.split()
                if len(t) < 1 + len(_SOC):
                    continue
                try:
                    nums = [float(x) for x in t[1:1 + len(_SOC)]]
                except ValueError:
                    continue
                for k, v in zip(_SOC, nums):
                    soc[k].append(v)
            elif ln.startswith("X ") or ln.startswith("X\t"):
                t = ln.split()
                if len(t) < 1 + len(_DISP) or t[1] == "GMTDAY":   # skip header row
                    continue
                try:
                    nums = [float(x) for x in t[1:1 + len(_DISP)]]
                except ValueError:
                    continue
                if nums[0] <= 0.0:            # skip the day-0 totals row
                    continue
                for k, v in zip(_DISP, nums):
                    disp[k].append(v)
    return disp, soc


def fig_dispatch(disp: dict, region: str, outdir: Path, window=None) -> bool:
    day = disp["gmtday"]
    if not day:
        return False
    blue = disp[_DISP_BLUE]
    red = disp[_DISP_RED]
    suffix, lo, hi = "", 0.0, max(day)
    if window is not None:
        lo, hi = float(window[0]), float(window[1])
        suffix = "_d{:.0f}-{:.0f}".format(lo, hi)

    fig, ax = plt.subplots(figsize=(13.5, 3.5))
    ax.scatter(day, blue, s=2.0, color=C_BLUE, linewidths=0, zorder=3,
               label="Load + changes in storage + losses from storage, T&D, curtailment")
    ax.plot(day, red, color=C_RED, lw=0.5, zorder=2,
            label="Total WWS electricity + heat generation before losses")
    ax.set_xlim(lo, hi)
    ax.set_ylim(0, None)
    ax.set_ylabel("Energy each hour (TWh/hour)", color=C_RED)
    ax.set_xlabel(_XLABEL.format(region=_display_region(region)), color=C_RED)
    ax.grid(True, color="#dddddd", lw=0.5, zorder=0)
    ax.legend(loc="upper left", fontsize=8, markerscale=4, framealpha=0.9)
    outdir.mkdir(parents=True, exist_ok=True)
    for ext in ("pdf", "png"):
        fig.savefig(outdir / "{}_dispatch{}.{}".format(region, suffix, ext),
                    bbox_inches="tight", dpi=200)
    plt.close(fig)
    return True


def fig_soc(soc: dict, region: str, outdir: Path) -> bool:
    day = soc["igmtd"]
    if not day:
        return False
    fig, ax = plt.subplots(figsize=(13.5, 3.8))
    drawn = 0
    for lab, key, col in _SOC_SERIES:
        y = soc.get(key) or []
        if y and max(y) > 1e-9:
            ax.plot(day, y, color=col, lw=1.0, label=lab)
            drawn += 1
    if not drawn:
        plt.close(fig)
        return False
    ax.set_xlim(0, max(day))
    ax.set_ylim(0, None)
    ax.set_ylabel("Storage state of charge (TWh)")
    ax.set_xlabel(_XLABEL.format(region=_display_region(region)))
    ax.grid(True, color="#dddddd", lw=0.5, zorder=0)
    ax.legend(loc="upper left", fontsize=8, ncol=2, framealpha=0.9)
    outdir.mkdir(parents=True, exist_ok=True)
    for ext in ("pdf", "png"):
        fig.savefig(outdir / "{}_soc.{}".format(region, ext),
                    bbox_inches="tight", dpi=200)
    plt.close(fig)
    return True


def _region_out_files(results_root: Path, regions):
    for rdir in sorted(results_root.iterdir()):
        if not rdir.is_dir() or "_" in rdir.name:      # base regions only
            continue
        if regions and rdir.name not in regions:
            continue
        out = rdir / "fortran_optimal_run.out"
        if out.exists():
            yield rdir.name, out


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--regions", nargs="*", default=None,
                    help="Region labels to plot (default: all base regions found).")
    ap.add_argument("--results-root", type=Path, default=RESULTS)
    ap.add_argument("--outdir", type=Path, default=DEFAULT_OUTDIR)
    ap.add_argument("--wwshourly", type=Path, default=None,
                    help="Optional: dispatch from this wwshourly.<REGION> file instead "
                         "of the .out (single-region use with --region).")
    ap.add_argument("--region", type=str, default=None,
                    help="Region label when using --wwshourly.")
    ap.add_argument("--day-window", nargs=2, type=float, metavar=("LO", "HI"),
                    default=None, help="Also write a zoomed dispatch over [LO, HI] GMT days.")
    args = ap.parse_args(argv)

    if args.wwshourly:
        region = args.region or args.wwshourly.name.split(".")[-1]
        disp, _ = parse_out(args.wwshourly)
        ok = fig_dispatch(disp, region, args.outdir)
        if ok and args.day_window:
            fig_dispatch(disp, region, args.outdir, window=args.day_window)
        print("{}: dispatch {}".format(region, "ok" if ok else "no rows"))
        return

    n = 0
    for region, out in _region_out_files(args.results_root, args.regions):
        disp, soc = parse_out(out)
        d = fig_dispatch(disp, region, args.outdir)
        if d and args.day_window:
            fig_dispatch(disp, region, args.outdir, window=args.day_window)
        s = fig_soc(soc, region, args.outdir)
        print("  {:16s} dispatch={:5s} soc={:5s} ({} hourly rows, {} daily rows)".format(
            region, str(d), str(s), len(disp["gmtday"]), len(soc["igmtd"])))
        n += 1
    if not n:
        print("No region fortran_optimal_run.out files found under", args.results_root)
    else:
        print("\nWrote SI dispatch/SOC figures for {} regions to {}".format(n, args.outdir))


if __name__ == "__main__":
    main()

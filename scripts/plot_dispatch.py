"""SI dispatch + storage state-of-charge plots, per region, from the Fortran output.

Each region's ``fortran_optimal_run.out`` (the LOADMATCH stdout) carries, besides
the summary sections, the hourly time series that the model also writes to
``wwshourly.<REGION>``:

  * hourly dispatch rows   ``X   <GMTDAY> <hour> ...``  (powerworld.f FORMAT 262)
  * daily storage-SOC rows ``XGMTD <day> ...``          (powerworld.f FORMAT 210)

For each region this writes:

  <REGION>_dispatch.{pdf,png}  the four-row dispatch figure (see below)
  <REGION>_soc.{pdf,png}       storage state of charge (TWh) by technology, daily

The four-row dispatch figure (all series in energy-each-hour = TWh/h = TW):
  row 1  total WWS generation before losses vs demand + storage changes + all
         losses (storage, T&D, curtailment), full three-year simulation
  row 2  same, for a window of `window_days` days
  row 3  WWS generation broken down by source, over the window
  row 4  demand + storage changes + losses broken down by component, over the
         window (a signed stack: storage discharging is negative)

Column order is taken verbatim from powerworld.f (FORMAT 261/262 and 208/210).
Reading the retained ``.out`` means every optimized region can be plotted with no
need to regenerate ``wwshourly``.  Pass ``--wwshourly`` to read such a file
instead (same ``X`` rows, e.g. the data-center cases written by
regen_wwshourly_us_dc.sh); the SOC always comes from the ``.out``.

Usage (from the repo root, with the per-region results present)
---------------------------------------------------------------
    python -m scripts.plot_dispatch                          # all regions found
    python -m scripts.plot_dispatch --regions EUROPE CHINA
    python -m scripts.plot_dispatch --window-start 100 --window-days 100
"""
import argparse
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from scripts.plot_style import region_label

REPO_ROOT = Path(__file__).resolve().parent.parent
RESULTS = REPO_ROOT / "data" / "results_verification"
DEFAULT_OUTDIR = RESULTS / "SI_dispatch"

# Dispatch row, FORMAT 262: 'X', GMTDAY, hour, then 27 columns (header FORMAT 261).
_DISP = ["gmtday", "hour", "inflx", "flx", "h2", "flxh2", "totload", "origload",
         "loss_in_stor", "allstorloss", "d_stor", "loss_ug", "all_ug_loss", "d_ug",
         "d_h2", "curt", "load_chg_loss_curt", "sup_bef_td", "wind", "solpv_csp",
         "hydro", "wav_geo_tid", "sol_heat", "geo_heat", "tdloss", "sup_aft_td",
         "cold", "warm", "hrs"]
_DISP_BLUE = "load_chg_loss_curt"   # Demand + storage changes + losses (storage, T&D, curtailment)
_DISP_RED = "sup_bef_td"            # Total WWS electricity + heat generation before losses

# SOC row, FORMAT 210: 'XGMTD', igmtd, then 22 values (header FORMAT 208).
_SOC = ["igmtd", "demandnew", "deminflex", "remaindem", "supply", "suppht", "warm",
        "cold", "flexload", "flexh2", "flexsum", "csp", "phs", "battery", "storf",
        "storo", "storh", "utes", "hydro", "h2", "cumshed", "hydisch", "utesdisch"]
_SOC_SERIES = [
    ("Battery",              "battery", "#2a78d6"),
    ("Pumped hydro",         "phs",     "#008300"),
    ("CSP (thermal)",        "csp",     "#c98500"),
    ("Hydrogen",             "h2",      "#e34948"),
    ("Seasonal heat (UTES)", "utes",    "#6a3d9a"),
    ("Hot-water / heat",     "storh",   "#eb6834"),
    ("Cold / PCM",           "storf",   "#17becf"),
]

# Row 3: WWS generation by source (all >= 0; sum to sup_bef_td).
_GEN_SOURCES = [
    ("Onshore + offshore wind", "wind",        "#3b7dd8"),
    ("Solar PV + CSP",          "solpv_csp",   "#f2a900"),
    ("Hydro",                   "hydro",       "#1b9e77"),
    ("Wave + tidal + geo elec", "wav_geo_tid", "#7570b3"),
    ("Solar heat",              "sol_heat",    "#fdae61"),
    ("Geo heat",                "geo_heat",    "#8c6d31"),
]

C_BLUE = "#1f4fd8"
C_RED = "#e02318"
_XLABEL = ("GMT day of simulation for {region}, "
           "starting 0 GMT January 1, 2050, ending December 31, 2052")


def _display_region(name: str) -> str:
    return region_label(name)


def parse_out(path: Path):
    """Parse the hourly dispatch (X rows) and daily SOC (XGMTD rows) from a
    LOADMATCH stdout/.out file.  Returns (disp, soc) dicts of column -> list."""
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


def _demand_components(A):
    """Row-4 components (signed) whose sum is the blue LOAD+TDSTORLS+CURT line."""
    return [
        ("Inflexible demand",           A["inflx"],                            "#4d4d4d"),
        ("Flexible elec + heat + cold", A["flx"],                              "#66c2a5"),
        ("Flexible hydrogen",           A["h2"],                               "#8da0cb"),
        ("Change in all storage",       A["d_stor"] + A["d_ug"] + A["d_h2"],   "#e78ac3"),
        ("Losses in/out of storage",    A["allstorloss"] + A["all_ug_loss"],   "#e6c700"),
        ("T&D losses",                  A["tdloss"],                           "#fc8d62"),
        ("Curtailment",                 A["curt"],                             "#a6d854"),
    ]


def _stack_pos(ax, x, comps):
    """Stack non-negative components upward from zero."""
    base = np.zeros_like(x, dtype=float)
    for label, y, color in comps:
        y = np.asarray(y, dtype=float)
        ax.fill_between(x, base, base + y, color=color, linewidth=0, label=label, zorder=2)
        base += y


def _stack_signed(ax, x, comps):
    """Stack each component's positive part up and negative part down (so a net
    of storage charging/discharging is preserved and the stack sums correctly)."""
    pos = np.zeros_like(x, dtype=float)
    neg = np.zeros_like(x, dtype=float)
    for label, y, color in comps:
        y = np.asarray(y, dtype=float)
        yp = np.clip(y, 0.0, None)
        yn = np.clip(y, None, 0.0)
        ax.fill_between(x, pos, pos + yp, color=color, linewidth=0, label=label, zorder=2)
        ax.fill_between(x, neg, neg + yn, color=color, linewidth=0, zorder=2)
        pos += yp
        neg += yn


def fig_dispatch(disp, region, outdir, window_start=100.0, window_days=100.0):
    """Four-row dispatch figure (see module docstring)."""
    day = np.asarray(disp["gmtday"], dtype=float)
    if day.size == 0:
        return False
    A = {k: np.asarray(v, dtype=float) for k, v in disp.items()}
    blue, red = A[_DISP_BLUE], A[_DISP_RED]
    disp_region = _display_region(region)

    w0 = float(window_start)
    w1 = min(w0 + float(window_days), float(day.max()))
    m = (day >= w0) & (day <= w1)
    if m.sum() < 2:                      # requested window outside data -> first window
        w0 = float(day.min())
        w1 = min(w0 + float(window_days), float(day.max()))
        m = (day >= w0) & (day <= w1)

    fig, (ax1, ax2, ax3, ax4) = plt.subplots(4, 1, figsize=(13.5, 13.5))

    # Row 1: full three-year period
    ax1.scatter(day, blue, s=1.5, color=C_BLUE, linewidths=0, zorder=3,
                label="Demand + storage changes + losses (storage, T&D, curtailment)")
    ax1.plot(day, red, color=C_RED, lw=0.4, zorder=2,
             label="Total WWS generation before losses")
    ax1.set_xlim(0, day.max())
    ax1.legend(loc="upper left", fontsize=7.5, markerscale=5, framealpha=0.9)

    # Row 2: window, same two series
    ax2.scatter(day[m], blue[m], s=6, color=C_BLUE, linewidths=0, zorder=3,
                label="Demand + storage changes + losses")
    ax2.plot(day[m], red[m], color=C_RED, lw=0.8, zorder=2,
             label="Total WWS generation before losses")
    ax2.legend(loc="upper left", fontsize=7.5, markerscale=3, framealpha=0.9)

    # Row 3: generation by source, window (stack sums to red)
    _stack_pos(ax3, day[m], [(lab, A[key][m], col) for lab, key, col in _GEN_SOURCES])
    ax3.plot(day[m], red[m], color=C_RED, lw=0.5, alpha=0.6, zorder=3)
    ax3.legend(loc="upper left", fontsize=7.5, ncol=3, framealpha=0.9)

    # Row 4: demand/storage/losses by component, window (signed stack, sums to blue)
    _stack_signed(ax4, day[m], [(lab, y[m], col) for lab, y, col in _demand_components(A)])
    ax4.plot(day[m], red[m], color=C_RED, lw=0.5, alpha=0.6, zorder=3)
    ax4.axhline(0, color="#888888", lw=0.5, zorder=1)
    ax4.legend(loc="upper left", fontsize=7.5, ncol=4, framealpha=0.9)

    titles = [
        "(a) Total WWS generation vs demand + storage changes + losses: full 3-year simulation",
        f"(b) Same, {int(round(w1 - w0))}-day window (GMT days {int(w0)}-{int(w1)})",
        "(c) WWS generation by source (window)",
        "(d) Demand, storage changes, and losses by component (window)",
    ]
    for ax, ttl, xl in ((ax1, titles[0], (0, day.max())), (ax2, titles[1], (w0, w1)),
                        (ax3, titles[2], (w0, w1)), (ax4, titles[3], (w0, w1))):
        ax.set_xlim(*xl)
        ax.set_ylabel("Energy each hour\n(TWh/hour)", color=C_RED)
        ax.set_title(ttl, loc="left", fontsize=9.5, fontweight="bold")
        ax.grid(True, color="#e6e6e6", lw=0.4, zorder=0)
    ax4.set_xlabel(_XLABEL.format(region=disp_region), color=C_RED)

    # Decomposition residual check (validates the column mapping on real data).
    gen_sum = sum(A[k] for _, k, _ in _GEN_SOURCES)
    dem_sum = sum(y for _, y, _ in _demand_components(A))
    print("    [{}] gen-breakdown max resid vs total {:.3g} TWh/h; "
          "demand-breakdown max resid vs blue {:.3g} TWh/h".format(
              region, float(np.max(np.abs(gen_sum - red))),
              float(np.max(np.abs(dem_sum - blue)))))

    outdir.mkdir(parents=True, exist_ok=True)
    for ext in ("pdf", "png"):
        fig.savefig(outdir / "{}_dispatch.{}".format(region, ext),
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
    ap.add_argument("--window-start", type=float, default=100.0,
                    help="First GMT day of the zoom window (rows 2-4). Default 100.")
    ap.add_argument("--window-days", type=float, default=100.0,
                    help="Width of the zoom window in days. Default 100.")
    args = ap.parse_args(argv)

    if args.wwshourly:
        region = args.region or args.wwshourly.name.split(".")[-1]
        disp, _ = parse_out(args.wwshourly)
        ok = fig_dispatch(disp, region, args.outdir, args.window_start, args.window_days)
        print("{}: dispatch {}".format(region, "ok" if ok else "no rows"))
        return

    n = 0
    for region, out in _region_out_files(args.results_root, args.regions):
        disp, soc = parse_out(out)
        d = fig_dispatch(disp, region, args.outdir, args.window_start, args.window_days)
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

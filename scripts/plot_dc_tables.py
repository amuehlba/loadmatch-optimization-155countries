"""Figures from the PI's post-processed Tables/ (DATA-CENTER PAPER ONLY).

The PI drops one Excel workbook per optimized case into
data/results_verification/Tables/.  This script compares the optimized
no-data-center case against the five data-center supply cases on the
post-processed metrics, using the same house style as the other reporting
figures (scripts/plot_style.py).

Cases (optimized only):
    Opt   -> no data center (optimized)      Tables-155Countries-Opt.xlsx
    EGS   -> case 1: EGS                      Tables-155-DC-EGS.xlsx
    WSBH  -> case 2: utility PV+wind+bat+H2   Tables-155-DC-WSBH.xlsx
    RBH   -> case 3: rooftop PV+bat+H2        Tables-155-DC-RBH.xlsx
    WSB   -> case 2, batteries only           Tables-155-DC-WSB.xlsx
    WSH   -> case 2, hydrogen only            Tables-155-DC-WSH.xlsx

Each figure has two stacked panels sharing the case axis:
  (a) aggregate total across all regions (bars vs the no-DC base line);
  (b) per-region distribution of the change vs base, expressed against a STABLE
      denominator so it stays bounded and interpretable (a raw % change is
      meaningless where the base is ~0, e.g. Greenland's 6 km^2 of new land, or
      where net jobs cross zero):
        land -> change in new-land share, percentage POINTS of regional area;
        jobs -> net-job change as % of the region's base energy-sector jobs.

    fig_dc_land_use   total new WWS land (spacing + footprint, 10^3 km^2)
    fig_dc_net_jobs   total net jobs (millions, all countries)

Usage:
    python -m scripts.plot_dc_tables
    python -m scripts.plot_dc_tables --tables path/to/Tables --outdir figs/
"""
import argparse
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.colors import Normalize
import openpyxl

from scripts.plot_style import (
    apply_dc_style as apply_style, DC_STRATEGIES, C_BASELINE,
    GRID, MUTED, INK, INK_SECONDARY, diverging_cmap, cap_region, minor_ticks,
    region_sort_key,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TABLES = REPO_ROOT / "data" / "results_verification" / "Tables"
DEFAULT_OUT = REPO_ROOT / "data" / "results_verification"

apply_style()

_DC_COLOR = {k: c for k, _, c, _ in DC_STRATEGIES}
BASE_FILE = "Tables-155Countries-Opt-New.xlsx"   # optimized no-DC base (latest -New results)

# PI decision: exclude Greenland & Iceland rooftop (case 3, RBH) results from the
# figures (aggregate total and per-region distribution); noted in the caption.
EXCLUDE = {"Tables-155-DC-RBH-New.xlsx": {"Greenland", "Iceland"}}

CASES = [
    (BASE_FILE,                     "Base (no DC)", C_BASELINE),
    ("Tables-155-DC-EGS-New.xlsx",  "DC EGS",  _DC_COLOR["dc1"]),
    ("Tables-155-DC-WSBH-New.xlsx", "DC WSBH", _DC_COLOR["dc2"]),
    ("Tables-155-DC-WSB-New.xlsx",  "DC WSB",  _DC_COLOR["dc2bat"]),
    ("Tables-155-DC-WSH-New.xlsx",  "DC WSH",  _DC_COLOR["dc2h2"]),
    ("Tables-155-DC-RBH-New.xlsx",  "DC RBH",  _DC_COLOR["dc2rc"]),
]


def _num(x):
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return float(x)
    try:
        return float(str(x).replace(",", "").strip())
    except ValueError:
        return None


def _col(header, name):
    for i, h in enumerate(header):
        if h is not None and str(h).strip().upper() == name.upper():
            return i
    raise KeyError(f"column {name!r} not found in {[h for h in header if h]}")


def _land_block(wb):
    """Per-region {region: footprint+spacing km^2}, {region: % of regional area},
    {region: regional land area km^2}."""
    rows = list(wb["LandArea"].iter_rows(values_only=True))
    h = rows[0]
    fi, si = _col(h, "FOOTPRIN-KM2"), _col(h, "SPACING-KM2")
    pfi, psi = _col(h, "%FOOTPRIN"), _col(h, "%SPACING")
    lri = _col(h, "LANDREG-KM2")
    km2, pct, landreg = {}, {}, {}
    for r in rows[1:]:
        a = r[0]
        if a is None or str(a).strip() == "" or str(a).strip().lower().startswith("all"):
            break
        f, s = _num(r[fi]), _num(r[si])
        if f is None or s is None:
            break
        reg = str(a).strip()
        km2[reg] = f + s
        landreg[reg] = _num(r[lri])
        pf, ps = _num(r[pfi]), _num(r[psi])
        if pf is not None and ps is not None:
            pct[reg] = pf + ps
    return km2, pct, landreg


def _jobs_block(wb):
    """Per-region {region: net jobs}, {region: gross (construction+operation) jobs}."""
    rows = list(wb["Jobs"].iter_rows(values_only=True))
    ni, gi = _col(rows[0], "NETJOBS"), _col(rows[0], "CONS+OP")
    net, gross = {}, {}
    for r in rows[1:]:
        a = r[0]
        if a is None or str(a).strip() == "":
            continue
        if str(a).strip().lower().startswith("all"):
            break
        reg = str(a).strip()
        n, g = _num(r[ni]), _num(r[gi])
        if n is not None:
            net[reg] = n
        if g is not None:
            gross[reg] = g
    return net, gross


def collect(tables_dir: Path):
    """rows: [(label, colour, land_10^3km2, jobs_M, land_dpp[], jobs_pct[],
              land_abs[], land_wmean, jobs_wmean), ...].

    land_abs   = footprint+spacing land as % of each region's area (per-region
                 distribution the PI asked to show around a mean line).
    land_wmean = AREA-WEIGHTED mean = sum(footprint+spacing km^2) / sum(regional
                 land area km^2) x 100 (the true "mean across all countries";
                 the simple mean of per-region % overweights small dense regions).
    jobs_wmean = aggregate % net job gain = sum(case-base net jobs)/sum(base net
                 jobs) x 100 (same weighting logic; = the panel-(a) % change).
    """
    bwb = openpyxl.load_workbook(tables_dir / BASE_FILE, read_only=True, data_only=True)
    b_km2, b_pct, b_landreg = _land_block(bwb)
    b_net, _bg = _jobs_block(bwb)
    bwb.close()
    rows = []
    for fn, label, color in CASES:
        path = tables_dir / fn
        if not path.exists():
            print(f"  [WARN] missing workbook: {fn} (skipped)")
            continue
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        km2, pct, landreg = _land_block(wb)
        net, _g = _jobs_block(wb)
        for reg in EXCLUDE.get(fn, ()):        # drop PI-excluded regions for this case
            km2.pop(reg, None); pct.pop(reg, None); landreg.pop(reg, None); net.pop(reg, None)
        land_dpp = np.array([pct[k] - b_pct[k] for k in b_pct if k in pct])
        # per-region dicts so each region can be drawn at a fixed (alphabetical) slot
        land_abs = {k: pct[k] for k in b_pct if k in pct}
        jobs_pct = {k: (net[k] - b_net[k]) / abs(b_net[k]) * 100.0
                    for k in b_net if k in net and abs(b_net[k]) > 1e-9}
        land_wmean = (100.0 * sum(km2[k] for k in km2 if landreg.get(k))
                      / sum(landreg[k] for k in km2 if landreg.get(k)))
        jkeys = [k for k in b_net if k in net]
        num = sum(net[k] - b_net[k] for k in jkeys)
        den = sum(b_net[k] for k in jkeys)
        jobs_wmean = 100.0 * num / den if den else float("nan")
        rows.append((label, color, sum(km2.values()) / 1e3, sum(net.values()) / 1e6,
                     land_dpp, jobs_pct, land_abs, land_wmean, jobs_wmean))
        wb.close()
    return rows


def _two_panel(rows, ti, pi, mi, ylab_top, ylab_bot, valfmt, medfmt, outname, outdir, clip=None):
    labels = [r[0] for r in rows]
    colors = [r[1] for r in rows]
    totals = [r[ti] for r in rows]
    region_dicts = [r[pi] for r in rows]     # {region: value} per case
    means = [r[mi] for r in rows]            # weighted (aggregate) mean per case
    n = len(rows)
    xs = list(range(n))
    base = totals[0]

    # ONE alphabetical region order, shared with every other figure, and a fixed
    # horizontal offset per region so a region sits at the SAME spot in every
    # case column (base and RBH land then line up exactly).
    all_regions = sorted(set().union(*[set(d) for d in region_dicts]), key=region_sort_key)
    N = len(all_regions)
    xoff = {r: (-0.28 + 0.56 * k / max(N - 1, 1)) for k, r in enumerate(all_regions)}

    fig, (axA, axB) = plt.subplots(2, 1, figsize=(9.8, 8.6), sharex=True,
                                   gridspec_kw={"height_ratios": [1.1, 1.2], "hspace": 0.13})

    # (a) aggregate totals
    axA.grid(axis="y", color=GRID, lw=0.7, zorder=0)
    axA.axhline(base, color=MUTED, lw=1.1, ls=(0, (5, 3)), zorder=2)
    axA.text(1.006, base, " no-DC base", transform=axA.get_yaxis_transform(),
             va="center", ha="left", fontsize=10, color=INK_SECONDARY)
    axA.bar(xs, totals, width=0.64, color=colors, zorder=3)
    for x, v in zip(xs, totals):
        axA.text(x, v, valfmt.format(v), ha="center", va="bottom", fontsize=10, color=INK)
    axA.set_ylabel(ylab_top)
    axA.set_ylim(0, max(totals) * 1.13)
    axA.text(0.0, 1.02, "a", transform=axA.transAxes, fontweight="bold", fontsize=14, va="bottom")
    minor_ticks(axA)

    # (b) per-region distribution.  Dots sit at their fixed alphabetical slot;
    # median (solid) + weighted mean (dashed) lines run through each column.
    axB.grid(axis="y", color=GRID, lw=0.7, zorder=0)
    axB.axhline(0, color=MUTED, lw=0.9, zorder=1)
    lo, hi = clip if clip else (None, None)
    for i, (d, color, mean) in enumerate(zip(region_dicts, colors, means)):
        if not d:
            continue
        px, py = [], []
        for r in all_regions:
            if r in d and (clip is None or lo <= d[r] <= hi):
                px.append(i + xoff[r]); py.append(d[r])
        axB.scatter(px, py, s=20, color=color, alpha=0.85, edgecolors="white",
                    linewidths=0.4, zorder=3)
        vals = np.array(list(d.values()))
        med = float(np.median(vals))
        axB.plot([i - 0.3, i + 0.3], [med, med], color=INK, lw=2.3, zorder=4,
                 label="median" if i == 0 else None)
        axB.plot([i - 0.3, i + 0.3], [mean, mean], color=INK, lw=1.7, ls=(0, (2, 1.4)),
                 zorder=5, label="mean" if i == 0 else None)
        if clip is not None:
            n_hi, n_lo = int((vals > hi).sum()), int((vals < lo).sum())
            if n_hi:
                axB.text(i, hi, f"+{n_hi}$\\uparrow$", va="top", ha="center", fontsize=8.5, color=color)
            if n_lo:
                axB.text(i, lo, f"+{n_lo}$\\downarrow$", va="bottom", ha="center", fontsize=8.5, color=color)

    # y-limits with a header band on top to hold the value labels above all dots
    if clip is not None:
        axB.set_ylim(lo, hi + 0.26 * (hi - lo))
    else:
        allp = np.concatenate([np.array(list(d.values())) for d in region_dicts if d])
        r_ = max(allp.max() - allp.min(), 1e-6)
        axB.set_ylim(allp.min() - 0.06 * r_, allp.max() + 0.32 * r_)

    # median & mean VALUES lined up horizontally in the header band (above the
    # dots, so they don't cover them); the legend says which line is which.
    tr = axB.get_xaxis_transform()   # x in data coords, y in axes fraction
    for i, mean in enumerate(means):
        d = region_dicts[i]
        if not d:
            continue
        med = float(np.median(np.array(list(d.values()))))
        axB.text(i, 0.91, "med " + medfmt.format(med), transform=tr, ha="center",
                 va="center", fontsize=8, color=INK)
        axB.text(i, 0.85, "mean " + medfmt.format(mean), transform=tr, ha="center",
                 va="center", fontsize=8, color=INK)
    axB.legend(loc="upper center", bbox_to_anchor=(0.5, 1.0), ncol=2, frameon=False,
               fontsize=9.5, handlelength=2.6, columnspacing=1.6)
    axB.set_ylabel(ylab_bot)
    axB.text(0.0, 1.02, "b", transform=axB.transAxes, fontweight="bold", fontsize=14, va="bottom")
    minor_ticks(axB)

    axB.set_xticks(xs)
    axB.set_xticklabels(labels, fontsize=10)
    axB.set_xlim(-0.6, n - 0.4)
    axB.tick_params(axis="x", length=0)
    axA.tick_params(axis="x", length=0)

    for ext in ("pdf", "png"):
        fig.savefig(outdir / f"{outname}.{ext}")
    plt.close(fig)


def _region_heatmap(by_case, labels, base_metric, cbar_label, outname, outdir):
    """Regions (x, alphabetical) x DC cases (y): every region shown explicitly.
    Diverging colour centred on 0; NaN (excluded) cells are grey; the colour range
    is clipped at the 96th percentile so a few small-base outliers don't wash out
    the map.  Alphabetical order matches every other region figure."""
    regions = sorted(base_metric, key=region_sort_key)
    M = np.full((len(labels), len(regions)), np.nan)
    for i, lab in enumerate(labels):
        d = by_case.get(lab, {})
        for j, r in enumerate(regions):
            if r in d:
                M[i, j] = d[r]
    fin = M[np.isfinite(M)]
    hr = max(float(np.nanpercentile(np.abs(fin), 96)) if fin.size else 1.0, 1e-6)
    cmap = diverging_cmap()
    cmap.set_bad("#d9d9d6")
    fig, ax = plt.subplots(figsize=(16.0, 0.6 * len(labels) + 3.0))
    im = ax.imshow(M, aspect="auto", cmap=cmap, norm=Normalize(-hr, hr))
    ax.set_yticks(range(len(labels)))
    ax.set_yticklabels(labels)
    ax.set_xticks(range(len(regions)))
    ax.set_xticklabels([cap_region(r) for r in regions], rotation=45, ha="right",
                       rotation_mode="anchor")
    ax.set_xticks(np.arange(-0.5, len(regions), 1), minor=True)
    ax.set_yticks(np.arange(-0.5, len(labels), 1), minor=True)
    ax.grid(which="minor", color="white", linewidth=1.4)
    ax.tick_params(length=0)
    for s in ax.spines.values():
        s.set_visible(False)
    cb = fig.colorbar(im, ax=ax, pad=0.01, fraction=0.03)
    cb.set_label(cbar_label, fontsize=11)
    cb.outline.set_visible(False)
    for ext in ("pdf", "png"):
        fig.savefig(outdir / f"{outname}.{ext}")
    plt.close(fig)


def region_heatmaps(tables_dir, outdir):
    """Extra 'regional' views: per-region change vs base for each DC case."""
    bwb = openpyxl.load_workbook(tables_dir / BASE_FILE, read_only=True, data_only=True)
    _bk, b_pct, _blr = _land_block(bwb)
    b_net, _bg = _jobs_block(bwb)
    bwb.close()
    dc = [(fn, lab, col) for fn, lab, col in CASES if fn != BASE_FILE]
    land_by_case, jobs_by_case, labels = {}, {}, []
    for fn, lab, _col in dc:
        path = tables_dir / fn
        if not path.exists():
            continue
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        _k, pct, _lr = _land_block(wb)
        net, _g = _jobs_block(wb)
        wb.close()
        excl = EXCLUDE.get(fn, ())
        labels.append(lab)
        land_by_case[lab] = {r: pct[r] - b_pct[r] for r in b_pct
                             if r in pct and r not in excl}
        jobs_by_case[lab] = {r: (net[r] - b_net[r]) / abs(b_net[r]) * 100.0
                             for r in b_net if r in net and abs(b_net[r]) > 1e-9
                             and r not in excl}
    _region_heatmap(jobs_by_case, labels, b_net,
                    "% net job gain vs base (no DC)",
                    "fig_dc_jobs_region_heatmap", outdir)
    _region_heatmap(land_by_case, labels, b_pct,
                    "Increase in land area vs base (% of regional area)",
                    "fig_dc_land_region_heatmap", outdir)
    print("  wrote fig_dc_jobs_region_heatmap.pdf/.png and fig_dc_land_region_heatmap.pdf/.png")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tables", type=Path, default=DEFAULT_TABLES)
    ap.add_argument("--outdir", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args(argv)
    args.outdir.mkdir(parents=True, exist_ok=True)

    rows = collect(args.tables)
    if not rows:
        print(f"No case workbooks found under {args.tables}")
        return

    _two_panel(rows, 2, 6, 7,
               "Total new footprint + spacing land\narea for WWS (1,000 km$^2$)",
               "Region-by-region land area for WWS\nfootprint + spacing (% of regional area)",
               "{:,.0f}", "{:.2f}%", "fig_dc_land_use", args.outdir)
    _two_panel(rows, 3, 5, 8,
               "Net job gain versus BAU (millions)",
               "Region-by-region % net job gain\nversus base (no DC)",
               "{:.1f}", "{:+.0f}%", "fig_dc_net_jobs", args.outdir, clip=(-160, 220))
    region_heatmaps(args.tables, args.outdir)

    bl, bj = rows[0][2], rows[0][3]
    print("  wrote fig_dc_land_use.pdf/.png and fig_dc_net_jobs.pdf/.png")
    print("    case (land 10^3 km^2 [Δtot] · land %area wmean/med per region | "
          "net jobs M [Δtot] · %gain vs base wmean/med per region):")
    for label, _c, land, jobs, _lpp, jpc, labs, lwm, jwm in rows:
        name = label.replace("\n", " ")
        lm = f"{lwm:.2f}/{np.median(list(labs.values())):.2f}%" if labs else "n/a"
        jm = f"{jwm:+.0f}/{np.median(list(jpc.values())):+.0f}%" if jpc else "n/a"
        print(f"      {name:22s} {land:7,.0f} [{land-bl:+6,.0f}] {lm:>13} | "
              f"{jobs:6.1f} [{jobs-bj:+5.1f}] {jm:>11}")


if __name__ == "__main__":
    main()

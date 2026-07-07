"""Cross-region comparison: PI trial-and-error baseline vs GA-optimized results.

For every region under ``data/results_verification/<REGION>/`` this reads
``baseline_summary.json`` (our binary run with the PI's baseline factors — i.e.
the PI's trial-and-error cost, validated by the parity check) and
``optimal_summary.json`` (the GA-optimized result, including solve-time timing),
then writes a CSV and prints a table with a TOTAL row.

The paper's headline numbers come from here:
  * total baseline cost vs total GA cost, and the % saving
  * total GA solve time, summed over all regions

Usage
-----
    python -m scripts.export_comparison
    python -m scripts.export_comparison --regions UNITED-STATES EUROPE
    python -m scripts.export_comparison --output path/to/comparison.csv

Only stdlib is used so this runs on any machine, with or without the GA env.
"""
import argparse
import csv
import json
import re
from pathlib import Path
from typing import Dict, List, Optional

from src.region_shortcodes import REGION_SHORTCODE
from scripts.parse_fortran_output import parse_land_area

REPO_ROOT = Path(__file__).resolve().parent.parent
RESULTS_ROOT = REPO_ROOT / "data" / "results_verification"
RAW_DIR = REPO_ROOT / "data" / "raw"

# Mean ($/yr) from a Fortran/xx "ANNUAL TOT ENERGY COST ... LO MN HI=" line.
_ANNUAL_COST_RE = re.compile(
    r"ANNUAL TOT ENERGY COST.*?LO MN HI=\s*([-\d.Ee+]+)\s+([-\d.Ee+]+)\s+([-\d.Ee+]+)")

_LAND_KEYS = ("new_spacing_pct_regland", "new_footprint_pct_regland",
              "new_land_pct_regland")

# Flag a region when its optimized total new-land share (wind spacing +
# footprint, % of regional land) exceeds the baseline share by more than this
# relative tolerance.  Override with --land-tolerance.
DEFAULT_LAND_TOLERANCE = 0.10


def _pi_xx_text(region: str, raw_dir: Path) -> Optional[str]:
    """Contents of the PI's pristine xx.<SHORTCODE> file in raw_dir, if present.
    Used only as a fallback when our re-run baseline failed."""
    shortcode = REGION_SHORTCODE.get(region)
    if not shortcode:
        return None
    xx = raw_dir / "xx.{}".format(shortcode)
    if not xx.exists():
        return None
    return xx.read_text(encoding="ascii", errors="replace")


def _land_stats(summary: Optional[dict], rdir: Path, out_name: str) -> dict:
    """Land percentages for one run: prefer fields already in the summary JSON
    (present for runs made after the land parser was added); otherwise parse the
    retained raw Fortran output file."""
    if summary and summary.get("new_land_pct_regland") is not None:
        return {k: summary.get(k) for k in _LAND_KEYS}
    out_file = rdir / out_name
    if out_file.exists():
        return parse_land_area(out_file.read_text(encoding="ascii", errors="replace"))
    return {k: None for k in _LAND_KEYS}

# Column order for the console table: (dict key, header, format spec)
COLUMNS = [
    ("region",                    "Region",            "s"),
    ("baseline_cost_bil_per_yr",  "Baseline $B/yr",    ".2f"),
    ("ga_cost_bil_per_yr",        "GA $B/yr",          ".2f"),
    ("abs_savings_bil_per_yr",    "Savings $B/yr",     ".2f"),
    ("pct_savings",               "Savings %",         ".2f"),
    ("optimize_seconds",          "Optimize s",        ".0f"),
    ("total_seconds",             "Total s",           ".0f"),
    ("n_evaluations",             "Evals",             ".0f"),
    ("ga_feasible",               "Feasible",          "s"),
    ("ga_scratch_cost_bil_per_yr","GA-scr $B/yr",      ".2f"),
    ("scratch_vs_ga_pct",         "scr-GA %",          "+.2f"),
    ("ga_scratch2_cost_bil_per_yr","GA-scr2 $B/yr",    ".2f"),
    ("scratch2_vs_ga_pct",        "scr2-GA %",         "+.2f"),
    ("bl_newland_pct_regland",    "BL land %",         ".3f"),
    ("ga_newland_pct_regland",    "GA land %",         ".3f"),
    ("land_ok",                   "Land OK",           "s"),
    ("baseline_source",           "Baseline src",      "s"),
]

# CSV gets the full land breakdown as well (spacing / footprint / total / delta).
CSV_FIELDS = [
    "region",
    "baseline_cost_bil_per_yr", "ga_cost_bil_per_yr",
    "abs_savings_bil_per_yr", "pct_savings",
    "optimize_seconds", "total_seconds", "n_evaluations", "ga_feasible",
    "ga_scratch_cost_bil_per_yr", "scratch_vs_ga_pct",
    "scratch_start_feasible", "scratch_optimize_seconds", "scratch_n_evaluations",
    "ga_scratch2_cost_bil_per_yr", "scratch2_vs_ga_pct",
    "scratch2_optimize_seconds", "scratch2_n_evaluations",
    "bl_spacing_pct_regland", "bl_footprint_pct_regland", "bl_newland_pct_regland",
    "ga_spacing_pct_regland", "ga_footprint_pct_regland", "ga_newland_pct_regland",
    "land_delta_pp", "land_ok",
    "baseline_source",
]


def _load(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _cost(data: Optional[dict]) -> Optional[float]:
    return data.get("annual_cost_mn_bil_per_yr") if data else None


def collect(results_root: Path, regions: Optional[List[str]] = None,
            raw_dir: Path = RAW_DIR,
            land_tolerance: float = DEFAULT_LAND_TOLERANCE) -> List[Dict]:
    """One row per region that has an optimal_summary.json (i.e. was optimized).

    Baseline cost priority: use OUR re-run (baseline_summary.json) when it is
    feasible with a cost — this demonstrates we reproduce the PI's results.  Only
    when the re-run failed (infeasible / no cost) do we fall back to the PI's
    pristine xx.<SHORTCODE> result in raw_dir.

    Land: new-land shares (% of regional land) are collected for baseline and
    optimized runs — wind spacing (ONSHORE WIND row) + footprint (TOTAL
    ELEC+HEAT-FPRINT row) from the xx land-area table.  The optimized total is
    checked against the baseline total (land_ok: within +land_tolerance).
    """
    rows: List[Dict] = []
    if not results_root.exists():
        return rows
    for rdir in sorted(results_root.iterdir()):
        if not rdir.is_dir():
            continue
        region = rdir.name
        # Isolated alternative runs (<REGION>_scratch, <REGION>_dc<N>) are not
        # rows of their own: scratch results are joined onto the base-region row
        # below; data-center scenarios get their own dedicated comparison.
        if re.search(r"(_scratch\w*|_dc\d+\w*)$", region):
            continue
        if regions and region not in regions:
            continue
        opt = _load(rdir / "optimal_summary.json")
        if opt is None:
            continue  # region not optimized yet — skip

        bl = _load(rdir / "baseline_summary.json")
        base_cost = _cost(bl)
        base_feasible = bl.get("feasible") if bl else None
        if base_feasible and isinstance(base_cost, (int, float)):
            baseline_source = "rerun"
            bl_land = _land_stats(bl, rdir, "fortran_baseline_run.out")
        else:
            xx_text = _pi_xx_text(region, raw_dir)
            if xx_text is not None:
                m = _ANNUAL_COST_RE.search(xx_text)
                base_cost = float(m.group(2)) if m else None
                baseline_source = "PI xx (fallback)"
                bl_land = parse_land_area(xx_text)
            else:
                base_cost = None
                baseline_source = "rerun infeasible; no xx" if bl is not None else "missing"
                bl_land = {k: None for k in _LAND_KEYS}

        ga_cost = _cost(opt)
        ga_land = _land_stats(opt, rdir, "fortran_optimal_run.out")
        timing = opt.get("timing") or {}
        abs_sav = (base_cost - ga_cost) if (base_cost is not None and ga_cost is not None) else None
        pct_sav = (100.0 * abs_sav / base_cost) if (abs_sav is not None and base_cost) else None

        # GA started from scratch (spreadsheet values: FAC*=1, storage=0) —
        # produced by --baseline-start scratch into <REGION>_scratch/.
        sdir = results_root / (region + "_scratch")
        s_opt = _load(sdir / "optimal_summary.json")
        s_bl = _load(sdir / "baseline_summary.json")
        ga_scratch_cost = _cost(s_opt)
        s_timing = (s_opt.get("timing") or {}) if s_opt else {}

        # Second scratch campaign (improved algorithm), isolated under _scratch2.
        s2_opt = _load(results_root / (region + "_scratch2") / "optimal_summary.json")
        ga_scratch2_cost = _cost(s2_opt)
        s2_timing = (s2_opt.get("timing") or {}) if s2_opt else {}
        if isinstance(ga_scratch2_cost, (int, float)) and isinstance(ga_cost, (int, float)) and ga_cost:
            scratch2_vs_ga_pct = 100.0 * (ga_scratch2_cost - ga_cost) / ga_cost
        else:
            scratch2_vs_ga_pct = None
        if isinstance(ga_scratch_cost, (int, float)) and isinstance(ga_cost, (int, float)) and ga_cost:
            scratch_vs_ga_pct = 100.0 * (ga_scratch_cost - ga_cost) / ga_cost
        else:
            scratch_vs_ga_pct = None

        bl_total = bl_land.get("new_land_pct_regland")
        ga_total = ga_land.get("new_land_pct_regland")
        if isinstance(bl_total, (int, float)) and isinstance(ga_total, (int, float)):
            land_delta_pp = ga_total - bl_total
            land_ok = ga_total <= bl_total * (1.0 + land_tolerance) + 1e-9
        else:
            land_delta_pp = None
            land_ok = None

        rows.append({
            "region":                   region,
            "baseline_cost_bil_per_yr": base_cost,
            "baseline_source":          baseline_source,
            "ga_cost_bil_per_yr":       ga_cost,
            "abs_savings_bil_per_yr":   abs_sav,
            "pct_savings":              pct_sav,
            "optimize_seconds":         timing.get("optimize_seconds"),
            "total_seconds":            timing.get("total_seconds"),
            "n_evaluations":            timing.get("n_evaluations"),
            "ga_feasible":              opt.get("feasible"),
            "ga_scratch_cost_bil_per_yr": ga_scratch_cost,
            "scratch_vs_ga_pct":        scratch_vs_ga_pct,
            "scratch_start_feasible":   s_bl.get("feasible") if s_bl else None,
            "scratch_optimize_seconds": s_timing.get("optimize_seconds"),
            "scratch_n_evaluations":    s_timing.get("n_evaluations"),
            "ga_scratch2_cost_bil_per_yr": ga_scratch2_cost,
            "scratch2_vs_ga_pct":       scratch2_vs_ga_pct,
            "scratch2_optimize_seconds": s2_timing.get("optimize_seconds"),
            "scratch2_n_evaluations":   s2_timing.get("n_evaluations"),
            "bl_spacing_pct_regland":   bl_land.get("new_spacing_pct_regland"),
            "bl_footprint_pct_regland": bl_land.get("new_footprint_pct_regland"),
            "bl_newland_pct_regland":   bl_total,
            "ga_spacing_pct_regland":   ga_land.get("new_spacing_pct_regland"),
            "ga_footprint_pct_regland": ga_land.get("new_footprint_pct_regland"),
            "ga_newland_pct_regland":   ga_total,
            "land_delta_pp":            land_delta_pp,
            "land_ok":                  land_ok,
        })
    return rows


def _scratch_gap_total(rows: List[Dict], key: str = "ga_scratch_cost_bil_per_yr") -> Optional[float]:
    """Aggregate scratch-vs-baseline-start GA cost gap (%), over regions with both."""
    pairs = [(r[key], r["ga_cost_bil_per_yr"]) for r in rows
             if isinstance(r.get(key), (int, float))
             and isinstance(r.get("ga_cost_bil_per_yr"), (int, float))]
    if not pairs:
        return None
    ts, tg = sum(p[0] for p in pairs), sum(p[1] for p in pairs)
    return 100.0 * (ts - tg) / tg if tg else None


def total_row(rows: List[Dict]) -> Dict:
    def _sum(key: str, src: List[Dict]) -> Optional[float]:
        vals = [r[key] for r in src if isinstance(r.get(key), (int, float))]
        return sum(vals) if vals else None

    # Cost totals + % saving MUST be summed over the SAME regions — only those
    # that have both a baseline and a GA cost.  Otherwise a region missing its
    # baseline (e.g. an infeasible/failed baseline run) silently skews the saving.
    matched = [r for r in rows
               if isinstance(r.get("baseline_cost_bil_per_yr"), (int, float))
               and isinstance(r.get("ga_cost_bil_per_yr"), (int, float))]
    tb = _sum("baseline_cost_bil_per_yr", matched)
    tg = _sum("ga_cost_bil_per_yr", matched)
    abs_sav = (tb - tg) if (tb is not None and tg is not None) else None
    label = "TOTAL ({} regions)".format(len(matched))
    if len(matched) != len(rows):
        label = "TOTAL ({} of {} regions w/ baseline)".format(len(matched), len(rows))
    return {
        "region":                   label,
        "baseline_cost_bil_per_yr": tb,
        "ga_cost_bil_per_yr":       tg,
        "abs_savings_bil_per_yr":   abs_sav,
        "pct_savings":              (100.0 * abs_sav / tb) if (abs_sav is not None and tb) else None,
        # Timing totals are independent of the baseline, so sum over all rows.
        "optimize_seconds":         _sum("optimize_seconds", rows),
        "total_seconds":            _sum("total_seconds", rows),
        "n_evaluations":            _sum("n_evaluations", rows),
        "ga_feasible":              all(r.get("ga_feasible") for r in rows) if rows else None,
        # Scratch totals: cost gap computed over regions that have BOTH a
        # from-baseline and a from-scratch GA cost (same matched-set principle).
        "ga_scratch_cost_bil_per_yr": _sum("ga_scratch_cost_bil_per_yr", rows),
        "scratch_vs_ga_pct":        _scratch_gap_total(rows),
        "scratch_optimize_seconds": _sum("scratch_optimize_seconds", rows),
        "scratch_n_evaluations":    _sum("scratch_n_evaluations", rows),
        "ga_scratch2_cost_bil_per_yr": _sum("ga_scratch2_cost_bil_per_yr", rows),
        "scratch2_vs_ga_pct":       _scratch_gap_total(rows, "ga_scratch2_cost_bil_per_yr"),
        "scratch2_optimize_seconds": _sum("scratch2_optimize_seconds", rows),
        "scratch2_n_evaluations":   _sum("scratch2_n_evaluations", rows),
        # Land percentages are shares of each region's own land area, so summing
        # across regions is meaningless; report only the check outcome counts.
        "land_ok":                  "ok:{} flagged:{} n/a:{}".format(
            sum(1 for r in rows if r.get("land_ok") is True),
            sum(1 for r in rows if r.get("land_ok") is False),
            sum(1 for r in rows if r.get("land_ok") is None)),
        "baseline_source":          "rerun:{} fallback:{}".format(
            sum(1 for r in rows if r.get("baseline_source") == "rerun"),
            sum(1 for r in rows if str(r.get("baseline_source", "")).startswith("PI xx"))),
    }


def _cell(value, spec: str) -> str:
    if value is None:
        return ""
    if spec == "s":
        return str(value)
    if isinstance(value, (int, float)):
        return format(value, spec)
    return str(value)


def print_table(rows: List[Dict], total: Dict) -> None:
    headers = [h for _, h, _ in COLUMNS]
    table = [[_cell(r.get(k), spec) for k, _, spec in COLUMNS] for r in rows]
    total_line = [_cell(total.get(k), spec) for k, _, spec in COLUMNS]
    widths = [len(h) for h in headers]
    for line in table + [total_line]:
        widths = [max(w, len(c)) for w, c in zip(widths, line)]

    def fmt(cells):
        return "  ".join(c.rjust(w) if i else c.ljust(w)
                         for i, (c, w) in enumerate(zip(cells, widths)))

    print(fmt(headers))
    print("  ".join("-" * w for w in widths))
    for line in table:
        print(fmt(line))
    print("  ".join("-" * w for w in widths))
    print(fmt(total_line))

    ts = total.get("total_seconds")
    if isinstance(ts, (int, float)):
        print("\nTotal solve time: {:.0f} s  ({:.2f} h) across {} regions.".format(
            ts, ts / 3600.0, len(rows)))


def print_land_check(rows: List[Dict], land_tolerance: float) -> None:
    """Summary of the new-land check: optimized total new-land share (wind
    spacing + footprint, % of regional land) vs baseline, flagging regions where
    the optimized share exceeds baseline by more than the tolerance."""
    checked = [r for r in rows if r.get("land_ok") is not None]
    flagged = [r for r in rows if r.get("land_ok") is False]
    missing = [r for r in rows if r.get("land_ok") is None]
    print("\nLAND CHECK (optimized new spacing+footprint vs baseline, "
          "tolerance +{:.0f}%):".format(100 * land_tolerance))
    if not checked:
        print("  no regions with land data on both sides — nothing checked.")
    elif not flagged:
        print("  all {} checked regions OK (optimized new-land share within "
              "tolerance of baseline).".format(len(checked)))
    else:
        print("  {} of {} regions EXCEED tolerance:".format(len(flagged), len(checked)))
        for r in flagged:
            print("    {:<16s} baseline {:.4f}%  ->  optimized {:.4f}%  "
                  "(+{:.4f} pp, {:+.1f}%)".format(
                      r["region"], r["bl_newland_pct_regland"],
                      r["ga_newland_pct_regland"], r["land_delta_pp"],
                      100 * r["land_delta_pp"] / r["bl_newland_pct_regland"]
                      if r["bl_newland_pct_regland"] else float("nan")))
    if missing:
        print("  no land data for: {}".format(
            ", ".join(r["region"] for r in missing)))


def write_csv(rows: List[Dict], total: Dict, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = CSV_FIELDS
    with out_path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            writer.writerow({k: r.get(k) for k in fieldnames})
        writer.writerow({k: total.get(k) for k in fieldnames})
    print("\nWrote {}".format(out_path))


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--regions", nargs="*", default=None,
                        help="Restrict to these region labels (default: all found).")
    parser.add_argument("--output", type=Path,
                        default=RESULTS_ROOT / "comparison_summary.csv",
                        help="CSV destination (default: %(default)s).")
    parser.add_argument("--results-root", type=Path, default=RESULTS_ROOT,
                        help="Root of per-region result folders (default: %(default)s).")
    parser.add_argument("--raw-dir", type=Path, default=RAW_DIR,
                        help="Folder holding the PI's pristine xx.<SHORTCODE> files, "
                             "used only as a baseline fallback (default: %(default)s).")
    parser.add_argument("--land-tolerance", type=float, default=DEFAULT_LAND_TOLERANCE,
                        help="Relative tolerance for the new-land check: flag a region "
                             "when its optimized new-land share exceeds baseline by more "
                             "than this fraction (default: %(default)s).")
    args = parser.parse_args(argv)

    rows = collect(args.results_root, args.regions, args.raw_dir,
                   land_tolerance=args.land_tolerance)
    if not rows:
        print("No optimized regions found under {} (need optimal_summary.json).".format(
            args.results_root))
        return
    total = total_row(rows)
    print_table(rows, total)
    print_land_check(rows, args.land_tolerance)
    write_csv(rows, total, args.output)


if __name__ == "__main__":
    main()

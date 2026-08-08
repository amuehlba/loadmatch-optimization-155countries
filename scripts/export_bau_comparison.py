"""Region-level WWS-vs-BAU comparison (baseline and from-scratch WWS).

For every optimized region this reads the WWS-vs-BAU block that powerworld.f
prints (parsed by parse_bau_comparison) from each WWS scenario and tabulates the
2050 business-as-usual reference against the WWS result:

  * BAU per-kWh cost broken into energy / air-pollution-health / climate, and
    their sum (the BAU social cost of energy)
  * BAU total social cost ($B/yr), avoided air-pollution mortality (2050), and
    2050 CO2e emissions
  * WWS energy cost and the WWS:BAU overall social-cost ratio for each scenario
    (baseline = PI trial-and-error, GA = optimized, scratch = from-scratch GA)

BAU is a fixed 2050 reference (read from countrystats.dat) and is identical
across the WWS scenarios; only the WWS side and the ratios change.  The BAU
reference columns are taken from the GA-optimized run.

Usage
-----
    python -m scripts.export_bau_comparison
    python -m scripts.export_bau_comparison --regions UNITED-STATES EUROPE
    python -m scripts.export_bau_comparison --output path/to/bau.csv

Only stdlib is used, so this runs anywhere the results tree is present.
"""
import argparse
import csv
import json
import re
from pathlib import Path
from typing import Dict, List, Optional

from scripts.parse_fortran_output import parse_bau_comparison

REPO_ROOT = Path(__file__).resolve().parent.parent
RESULTS_ROOT = REPO_ROOT / "data" / "results_verification"


def _load(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _bau_stats(summary: Optional[dict], rdir: Path, out_name: str) -> dict:
    """WWS-vs-BAU metrics for one run: prefer the ``bau`` section already in the
    summary JSON (present for runs parsed after the BAU parser was added);
    otherwise parse the retained raw Fortran output.  Returns {} if neither has
    it."""
    bau = (summary or {}).get("bau")
    if bau and bau.get("bau_lcoe_c_per_kwh") is not None:
        return bau
    out_file = rdir / out_name
    if out_file.exists():
        return parse_bau_comparison(out_file.read_text(encoding="ascii", errors="replace"))
    return {}


def _has_bau(bau: dict) -> bool:
    """True when the block was present AND the region had BAU data (LCOE > 0;
    the Fortran zeroes the ratios when countrystats.dat has no BAU row)."""
    v = bau.get("bau_lcoe_c_per_kwh")
    return isinstance(v, (int, float)) and v > 0.0


# Console table: (dict key, header, format spec)
COLUMNS = [
    ("region",                    "Region",         "s"),
    ("bau_social_c_per_kwh",      "BAU soc c/kWh",  ".2f"),
    ("wws_lcoe_ga_c_per_kwh",     "WWS c/kWh",      ".2f"),
    ("ratio_social_baseline",     "WWS:BAU base",   ".3f"),
    ("ratio_social_ga",           "WWS:BAU GA",     ".3f"),
    ("ratio_social_scratch",      "WWS:BAU scr",    ".3f"),
    ("bau_total_bil_per_yr",      "BAU $B/yr",      ".1f"),
    ("wws_total_ga_bil_per_yr",   "WWS $B/yr",      ".1f"),
    ("air_poll_mortality_2050",   "BAU deaths/yr",  ".0f"),
    ("co2e_mtonne_per_yr",        "BAU MtCO2e/yr",  ".1f"),
]

CSV_FIELDS = [
    "region",
    "bau_load_gw",
    "bau_lcoe_c_per_kwh", "bau_health_c_per_kwh", "bau_climate_c_per_kwh",
    "bau_social_c_per_kwh",
    "bau_energy_bil_per_yr", "bau_health_bil_per_yr", "bau_climate_bil_per_yr",
    "bau_total_bil_per_yr",
    "wws_lcoe_baseline_c_per_kwh", "wws_lcoe_ga_c_per_kwh", "wws_lcoe_scratch_c_per_kwh",
    "wws_total_baseline_bil_per_yr", "wws_total_ga_bil_per_yr", "wws_total_scratch_bil_per_yr",
    "ratio_social_baseline", "ratio_social_ga", "ratio_social_scratch",
    "ratio_wws_bau_load_ga",
    "air_poll_mortality_2016", "air_poll_mortality_2050",
    "co2e_mtonne_per_yr",
    "bau_social_usd_per_tco2e", "wws_ga_usd_per_tco2e",
]


def collect(results_root: Path, regions: Optional[List[str]] = None) -> List[Dict]:
    """One row per region that has an optimal_summary.json with BAU data."""
    rows: List[Dict] = []
    if not results_root.exists():
        return rows
    for rdir in sorted(results_root.iterdir()):
        if not rdir.is_dir():
            continue
        region = rdir.name
        if re.search(r"(_scratch\w*|_dc\d+\w*)$", region):
            continue
        if regions and region not in regions:
            continue
        opt = _load(rdir / "optimal_summary.json")
        if opt is None:
            continue

        ga = _bau_stats(opt, rdir, "fortran_optimal_run.out")
        if not _has_bau(ga):
            continue  # no BAU reference for this region — skip

        bl = _load(rdir / "baseline_summary.json")
        base = _bau_stats(bl, rdir, "fortran_baseline_run.out")
        sdir = results_root / (region + "_scratch")
        scr = _bau_stats(_load(sdir / "optimal_summary.json"), sdir, "fortran_optimal_run.out")

        rows.append({
            "region":                     region,
            # BAU reference (identical across scenarios; taken from the GA run)
            "bau_load_gw":                ga.get("bau_load_gw"),
            "bau_lcoe_c_per_kwh":         ga.get("bau_lcoe_c_per_kwh"),
            "bau_health_c_per_kwh":       ga.get("bau_health_c_per_kwh"),
            "bau_climate_c_per_kwh":      ga.get("bau_climate_c_per_kwh"),
            "bau_social_c_per_kwh":       ga.get("bau_social_c_per_kwh"),
            "bau_energy_bil_per_yr":      ga.get("bau_energy_bil_per_yr"),
            "bau_health_bil_per_yr":      ga.get("bau_health_bil_per_yr"),
            "bau_climate_bil_per_yr":     ga.get("bau_climate_bil_per_yr"),
            "bau_total_bil_per_yr":       ga.get("bau_total_bil_per_yr"),
            "air_poll_mortality_2016":    ga.get("air_poll_mortality_2016"),
            "air_poll_mortality_2050":    ga.get("air_poll_mortality_2050"),
            "co2e_mtonne_per_yr":         ga.get("co2e_mtonne_per_yr"),
            "bau_social_usd_per_tco2e":   ga.get("bau_total_usd_per_tco2e"),
            "wws_ga_usd_per_tco2e":       ga.get("wws_total_usd_per_tco2e"),
            "ratio_wws_bau_load_ga":      ga.get("ratio_wws_bau_load"),
            # WWS side, per scenario
            "wws_lcoe_baseline_c_per_kwh": base.get("wws_lcoe_c_per_kwh") if _has_bau(base) else None,
            "wws_lcoe_ga_c_per_kwh":       ga.get("wws_lcoe_c_per_kwh"),
            "wws_lcoe_scratch_c_per_kwh":  scr.get("wws_lcoe_c_per_kwh") if _has_bau(scr) else None,
            "wws_total_baseline_bil_per_yr": base.get("wws_total_bil_per_yr") if _has_bau(base) else None,
            "wws_total_ga_bil_per_yr":       ga.get("wws_total_bil_per_yr"),
            "wws_total_scratch_bil_per_yr":  scr.get("wws_total_bil_per_yr") if _has_bau(scr) else None,
            "ratio_social_baseline":       base.get("ratio_wws_bau_social_overall") if _has_bau(base) else None,
            "ratio_social_ga":             ga.get("ratio_wws_bau_social_overall"),
            "ratio_social_scratch":        scr.get("ratio_wws_bau_social_overall") if _has_bau(scr) else None,
        })
    return rows


def total_row(rows: List[Dict]) -> Dict:
    def _sum(key: str) -> Optional[float]:
        vals = [r[key] for r in rows if isinstance(r.get(key), (int, float))]
        return sum(vals) if vals else None

    def _ratio(wws_key: str) -> Optional[float]:
        pairs = [(r[wws_key], r["bau_total_bil_per_yr"]) for r in rows
                 if isinstance(r.get(wws_key), (int, float))
                 and isinstance(r.get("bau_total_bil_per_yr"), (int, float))]
        if not pairs:
            return None
        tw, tb = sum(p[0] for p in pairs), sum(p[1] for p in pairs)
        return tw / tb if tb else None

    return {
        "region":                   "TOTAL ({} regions)".format(len(rows)),
        # Intensity (c/kWh) columns do not sum across regions -> left blank.
        "bau_total_bil_per_yr":     _sum("bau_total_bil_per_yr"),
        "wws_total_baseline_bil_per_yr": _sum("wws_total_baseline_bil_per_yr"),
        "wws_total_ga_bil_per_yr":  _sum("wws_total_ga_bil_per_yr"),
        "wws_total_scratch_bil_per_yr": _sum("wws_total_scratch_bil_per_yr"),
        # Aggregate WWS:BAU social ratio = total WWS $B / total BAU $B.
        "ratio_social_baseline":    _ratio("wws_total_baseline_bil_per_yr"),
        "ratio_social_ga":          _ratio("wws_total_ga_bil_per_yr"),
        "ratio_social_scratch":     _ratio("wws_total_scratch_bil_per_yr"),
        "air_poll_mortality_2050":  _sum("air_poll_mortality_2050"),
        "co2e_mtonne_per_yr":       _sum("co2e_mtonne_per_yr"),
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

    r = total.get("ratio_social_ga")
    if isinstance(r, (int, float)):
        print("\nAcross {} regions, optimized WWS total societal cost is {:.1f}% of BAU "
              "(BAU ${:.0f}B/yr vs WWS ${:.0f}B/yr).".format(
                  len(rows), 100.0 * r,
                  total.get("bau_total_bil_per_yr") or 0.0,
                  total.get("wws_total_ga_bil_per_yr") or 0.0))


def write_csv(rows: List[Dict], total: Dict, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for r in rows:
            writer.writerow({k: r.get(k) for k in CSV_FIELDS})
        writer.writerow({k: total.get(k) for k in CSV_FIELDS})
    print("\nWrote {}".format(out_path))


def main(argv: Optional[List[str]] = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--regions", nargs="*", default=None,
                    help="Restrict to these region labels (default: all found).")
    ap.add_argument("--output", type=Path,
                    default=RESULTS_ROOT / "bau_comparison_summary.csv",
                    help="CSV destination (default: %(default)s).")
    ap.add_argument("--results-root", type=Path, default=RESULTS_ROOT,
                    help="Root of per-region result folders (default: %(default)s).")
    args = ap.parse_args(argv)

    rows = collect(args.results_root, args.regions)
    if not rows:
        print("No regions with BAU data found under {} (need optimal_summary.json "
              "with a BAU block).".format(args.results_root))
        return
    total = total_row(rows)
    print_table(rows, total)
    write_csv(rows, total, args.output)


if __name__ == "__main__":
    main()

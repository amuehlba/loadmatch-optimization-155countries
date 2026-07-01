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
from pathlib import Path
from typing import Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
RESULTS_ROOT = REPO_ROOT / "data" / "results_verification"

# Column order for CSV + console table: (dict key, header, format spec)
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
]


def _load(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _cost(data: Optional[dict]) -> Optional[float]:
    return data.get("annual_cost_mn_bil_per_yr") if data else None


def collect(results_root: Path, regions: Optional[List[str]] = None) -> List[Dict]:
    """One row per region that has an optimal_summary.json (i.e. was optimized)."""
    rows: List[Dict] = []
    if not results_root.exists():
        return rows
    for rdir in sorted(results_root.iterdir()):
        if not rdir.is_dir():
            continue
        region = rdir.name
        if regions and region not in regions:
            continue
        opt = _load(rdir / "optimal_summary.json")
        if opt is None:
            continue  # region not optimized yet — skip
        base_cost = _cost(_load(rdir / "baseline_summary.json"))
        ga_cost = _cost(opt)
        timing = opt.get("timing") or {}
        abs_sav = (base_cost - ga_cost) if (base_cost is not None and ga_cost is not None) else None
        pct_sav = (100.0 * abs_sav / base_cost) if (abs_sav is not None and base_cost) else None
        rows.append({
            "region":                   region,
            "baseline_cost_bil_per_yr": base_cost,
            "ga_cost_bil_per_yr":       ga_cost,
            "abs_savings_bil_per_yr":   abs_sav,
            "pct_savings":              pct_sav,
            "optimize_seconds":         timing.get("optimize_seconds"),
            "total_seconds":            timing.get("total_seconds"),
            "n_evaluations":            timing.get("n_evaluations"),
            "ga_feasible":              opt.get("feasible"),
        })
    return rows


def total_row(rows: List[Dict]) -> Dict:
    def _sum(key: str) -> Optional[float]:
        vals = [r[key] for r in rows if isinstance(r.get(key), (int, float))]
        return sum(vals) if vals else None

    tb, tg = _sum("baseline_cost_bil_per_yr"), _sum("ga_cost_bil_per_yr")
    abs_sav = (tb - tg) if (tb is not None and tg is not None) else None
    return {
        "region":                   "TOTAL ({} regions)".format(len(rows)),
        "baseline_cost_bil_per_yr": tb,
        "ga_cost_bil_per_yr":       tg,
        "abs_savings_bil_per_yr":   abs_sav,
        "pct_savings":              (100.0 * abs_sav / tb) if (abs_sav is not None and tb) else None,
        "optimize_seconds":         _sum("optimize_seconds"),
        "total_seconds":            _sum("total_seconds"),
        "n_evaluations":            _sum("n_evaluations"),
        "ga_feasible":              all(r.get("ga_feasible") for r in rows) if rows else None,
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


def write_csv(rows: List[Dict], total: Dict, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [k for k, _, _ in COLUMNS]
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
    args = parser.parse_args(argv)

    rows = collect(args.results_root, args.regions)
    if not rows:
        print("No optimized regions found under {} (need optimal_summary.json).".format(
            args.results_root))
        return
    total = total_row(rows)
    print_table(rows, total)
    write_csv(rows, total, args.output)


if __name__ == "__main__":
    main()

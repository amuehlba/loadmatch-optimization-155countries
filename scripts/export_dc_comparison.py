"""Data-center case comparison: cost of powering data centers per supply strategy.

For every region, reads the no-data-center GA optimum (the reference) and each
data-center case's results from its isolated folder:

  dc1     EGS powers the data centers (IFDATCEN=1, single evaluation)
  dc2     utility PV + wind + batteries + hydrogen re-optimized (IFDATCEN=2)
  dc2rc   residential/commercial PV + batteries + hydrogen re-optimized
  dc2bat  dc2 with storage flexibility restricted to batteries
  dc2h2   dc2 with storage flexibility restricted to hydrogen

Writes data/results_verification/dc_comparison_summary.csv with, per region and
case: cost, cost increase vs the no-dc optimum (absolute and %), feasibility,
new-land share, and solve time.  This CSV is the data source for the
data-center paper's plots.

Only stdlib is used.

Usage:
    python -m scripts.export_dc_comparison
    python -m scripts.export_dc_comparison --regions UNITED-STATES EUROPE
"""
import argparse
import csv
import json
import re
from pathlib import Path
from typing import Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
RESULTS_ROOT = REPO_ROOT / "data" / "results_verification"

# case key -> results-dir suffix
DC_CASES = {
    "dc1":    "_dc1",
    "dc2":    "_dc2",
    "dc2rc":  "_dc2rc",
    "dc2bat": "_dc2bat",
    "dc2h2":  "_dc2h2",
}


def _load(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def collect(results_root: Path, regions: Optional[List[str]] = None) -> List[Dict]:
    rows: List[Dict] = []
    for rdir in sorted(results_root.iterdir()):
        if not rdir.is_dir() or re.search(r"(_scratch\w*|_dc\d+\w*)$", rdir.name) \
                or rdir.name.startswith("xx_optimized"):
            continue
        region = rdir.name
        if regions and region not in regions:
            continue
        base = _load(rdir / "optimal_summary.json")
        if base is None:
            continue
        base_cost = base.get("annual_cost_mn_bil_per_yr")

        row: Dict = {
            "region": region,
            "base_cost_bil_per_yr": base_cost,
            "base_land_pct": base.get("new_land_pct_regland"),
        }
        any_case = False
        for case, suffix in DC_CASES.items():
            opt = _load(results_root / (region + suffix) / "optimal_summary.json")
            cost = opt.get("annual_cost_mn_bil_per_yr") if opt else None
            timing = (opt.get("timing") or {}) if opt else {}
            if isinstance(cost, (int, float)) and isinstance(base_cost, (int, float)) and base_cost:
                delta = cost - base_cost
                delta_pct = 100.0 * delta / base_cost
            else:
                delta = delta_pct = None
            row.update({
                f"{case}_cost_bil_per_yr":     cost,
                f"{case}_delta_bil_per_yr":    delta,
                f"{case}_delta_pct":           delta_pct,
                f"{case}_feasible":            opt.get("feasible") if opt else None,
                f"{case}_land_pct":            opt.get("new_land_pct_regland") if opt else None,
                f"{case}_optimize_seconds":    timing.get("optimize_seconds"),
            })
            any_case = any_case or (opt is not None)
        if any_case:
            rows.append(row)
    return rows


def total_row(rows: List[Dict]) -> Dict:
    """Totals per case over the regions that HAVE that case (matched with base)."""
    total: Dict = {"region": "TOTAL"}
    for case in DC_CASES:
        pairs = [(r[f"{case}_cost_bil_per_yr"], r["base_cost_bil_per_yr"]) for r in rows
                 if isinstance(r.get(f"{case}_cost_bil_per_yr"), (int, float))
                 and isinstance(r.get("base_cost_bil_per_yr"), (int, float))]
        if pairs:
            tc = sum(p[0] for p in pairs)
            tb = sum(p[1] for p in pairs)
            total[f"{case}_cost_bil_per_yr"] = tc
            total[f"{case}_delta_bil_per_yr"] = tc - tb
            total[f"{case}_delta_pct"] = 100.0 * (tc - tb) / tb if tb else None
            total[f"{case}_optimize_seconds"] = sum(
                r[f"{case}_optimize_seconds"] for r in rows
                if isinstance(r.get(f"{case}_optimize_seconds"), (int, float)))
            total[f"{case}_feasible"] = "n={}".format(len(pairs))
    total["base_cost_bil_per_yr"] = sum(
        r["base_cost_bil_per_yr"] for r in rows
        if isinstance(r.get("base_cost_bil_per_yr"), (int, float)))
    return total


def _fmt(v, spec) -> str:
    if v is None:
        return ""
    if isinstance(v, (int, float)) and spec != "s":
        return format(v, spec)
    return str(v)


def print_table(rows: List[Dict], total: Dict) -> None:
    cols = [("region", "Region", "s"), ("base_cost_bil_per_yr", "no-dc $B/yr", ".2f")]
    for case in DC_CASES:
        cols.append((f"{case}_cost_bil_per_yr", f"{case} $B/yr", ".2f"))
        cols.append((f"{case}_delta_pct", f"{case} +%", "+.2f"))
    headers = [h for _, h, _ in cols]
    lines = [[_fmt(r.get(k), spec) for k, _, spec in cols] for r in rows + [total]]
    widths = [max(len(h), *(len(l[i]) for l in lines)) for i, h in enumerate(headers)]

    def fmt_line(cells):
        return "  ".join(c.rjust(w) if i else c.ljust(w)
                         for i, (c, w) in enumerate(zip(cells, widths)))

    print(fmt_line(headers))
    print("  ".join("-" * w for w in widths))
    for line in lines[:-1]:
        print(fmt_line(line))
    print("  ".join("-" * w for w in widths))
    print(fmt_line(lines[-1]))


def write_csv(rows: List[Dict], total: Dict, out_path: Path) -> None:
    fieldnames = ["region", "base_cost_bil_per_yr", "base_land_pct"]
    for case in DC_CASES:
        fieldnames += [f"{case}_cost_bil_per_yr", f"{case}_delta_bil_per_yr",
                       f"{case}_delta_pct", f"{case}_feasible",
                       f"{case}_land_pct", f"{case}_optimize_seconds"]
    out_path.parent.mkdir(parents=True, exist_ok=True)
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
    parser.add_argument("--regions", nargs="*", default=None)
    parser.add_argument("--results-root", type=Path, default=RESULTS_ROOT)
    parser.add_argument("--output", type=Path,
                        default=RESULTS_ROOT / "dc_comparison_summary.csv")
    args = parser.parse_args(argv)

    rows = collect(args.results_root, args.regions)
    if not rows:
        print("No regions with data-center results found under {} "
              "(need <REGION>_dc*/optimal_summary.json).".format(args.results_root))
        return
    total = total_row(rows)
    print_table(rows, total)
    write_csv(rows, total, args.output)


if __name__ == "__main__":
    main()

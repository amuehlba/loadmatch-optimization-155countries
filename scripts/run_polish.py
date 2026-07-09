"""Vestigial-capacity polish for EXISTING optima (no GA rerun needed).

Answers the PI's question directly: for each region's stored optimum
(genetic_factors.dat), evaluate variants with (a) FCCHARG=FCDISCH=0,
(b) FACSHT=0, (c) both, alongside a re-evaluation of the original, in one
parallel batch of isolated workspaces.  Reports per region whether zeroing the
unused capacity is feasible and what the cost difference is, and (with --apply)
adopts the best variant: updates genetic_factors.dat, optimal_summary.json
(cost fields refreshed, original timing preserved, "polish" recorded), and the
xx deliverable.

Run on Sherlock inside an allocation (a login node is too weak for 4 parallel
3-year simulations):
    salloc --partition=serc --cpus-per-task=8 --mem=80G --time=04:00:00
    python -m scripts.run_polish                     # report only, all regions
    python -m scripts.run_polish --regions TAIWAN    # subset
    python -m scripts.run_polish --apply             # adopt improvements

Writes data/results_verification/polish_summary.csv.
"""
import argparse
import csv
import json
from pathlib import Path

from src.io.dat_parser import read_dat, write_dat
import scripts.run_full_workflow as rfw
from scripts.parse_fortran_output import parse_and_save, save_summary

RESULTS_ROOT = Path("data/results_verification")


def polish_region(region, parallel_evals, apply_changes):
    rdir = RESULTS_ROOT / region
    seed_file = rdir / "genetic_factors.dat"
    summary_file = rdir / "optimal_summary.json"
    if not seed_file.exists() or not summary_file.exists():
        return None

    factors = {k.upper(): float(v) for k, v in read_dat(str(seed_file)).items()}
    paths = rfw._region_paths(region)

    # Variants: original re-evaluated on equal footing + the zero-out combos.
    combos = [("original", ())]
    active = [(n, ks) for n, ks in rfw._POLISH_GROUPS
              if any(factors.get(k, 0.0) > 0.0 for k in ks)]
    combos += active
    if len(active) > 1:
        combos.append(("all", tuple(k for _, ks in active for k in ks)))
    if len(combos) == 1:
        print(f"{region}: nothing to polish (H2 pair and FACSHT already zero).")
        return {"region": region, "note": "already clean"}

    specs = []
    for name, keys in combos:
        v = factors.copy()
        for k in keys:
            v[k] = 0.0
        specs.append({"label": f"polish-{name}", "factors": v})
    results = rfw._eval_batch(specs, parallel_evals, region, paths)

    row = {"region": region}
    base_cost = None
    best_i, best_cost = 0, float("inf")
    for i, ((name, _), spec, res) in enumerate(zip(combos, specs, results)):
        row[f"{name}_feasible"] = res["feasible"]
        row[f"{name}_cost"] = res["cost"] if res["cost"] != float("inf") else None
        if name == "original":
            base_cost = res["cost"]
        if res["feasible"] and res["cost"] < best_cost:
            best_i, best_cost = i, res["cost"]

    adopted = combos[best_i][0] if best_i != 0 else None
    row["adopted"] = adopted or "original"
    row["delta_vs_original"] = (best_cost - base_cost) if adopted else 0.0
    print("{:16s} original={:>10s}  best={} ({:.5f})".format(
        region,
        "{:.5f}".format(base_cost) if base_cost != float("inf") else "infeasible",
        row["adopted"], best_cost))

    if apply_changes and adopted:
        best_factors = specs[best_i]["factors"]
        stdout = rfw.run_single_isolated("polish-final", best_factors, region, paths)
        paths["fortran_optimal_out"].write_text(stdout)
        old = json.loads(summary_file.read_text())
        data = parse_and_save(stdout, factors=best_factors, region=region,
                              run_type=old.get("run_type", "ga_optimal"),
                              out_path=summary_file)
        timing = old.get("timing") or {}
        timing["polish"] = "polish-zero-{}".format(adopted)
        data["timing"] = timing
        save_summary(data, summary_file)
        write_dat(best_factors, seed_file)
        xx_out = rfw._xx_deliverable_path(region)
        xx_out.write_text(rfw._strip_override_echo(stdout))
        print(f"  applied: {seed_file}, {summary_file.name}, {xx_out}")
    return row


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--regions", nargs="*", default=None)
    ap.add_argument("--parallel-evals", type=int, default=4)
    ap.add_argument("--max-land-pct", type=float, default=7.0,
                    help="Match the campaign's land-cap methodology (default 7).")
    ap.add_argument("--apply", action="store_true",
                    help="Adopt improvements (update factors, summary, xx file).")
    ap.add_argument("--output", type=Path,
                    default=RESULTS_ROOT / "polish_summary.csv")
    args = ap.parse_args(argv)

    rfw._MAX_LAND_PCT = args.max_land_pct

    regions = args.regions or sorted(
        d.name for d in RESULTS_ROOT.iterdir()
        if d.is_dir() and "_" not in d.name.replace("-", "")
        and (d / "genetic_factors.dat").exists())
    rows = [r for r in (polish_region(reg, args.parallel_evals, args.apply)
                        for reg in regions) if r]
    if not rows:
        print("No regions with genetic_factors.dat found.")
        return
    fieldnames = sorted({k for r in rows for k in r}, key=lambda k: (k != "region", k))
    with args.output.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print(f"\nWrote {args.output}")


if __name__ == "__main__":
    main()

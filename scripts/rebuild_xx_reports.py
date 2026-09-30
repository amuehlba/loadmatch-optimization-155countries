"""Rewrite the optimized xx reports from the retained Fortran outputs.

For every results directory under data/results_verification/ that has a
fortran_optimal_run.out, strips the READ_FACTOR_OVERRIDES echo and writes the
report to data/results_verification/xx_optimized<SUFFIX>/xx.<SHORTCODE>, where
<SUFFIX> mirrors the results directory (<REGION><SUFFIX>, e.g. _scratch2,
_dc2rc).  No simulation is run; the driver already writes the same files, so
re-running this is always safe.

Usage:
    python -m scripts.rebuild_xx_reports
    python -m scripts.rebuild_xx_reports --results-root path/to/results
"""
import argparse
from pathlib import Path
from typing import Optional, List

from src.regions import REGION_SHORTCODE
from src.xx_tools import strip_override_echo

REPO_ROOT = Path(__file__).resolve().parent.parent
RESULTS_ROOT = REPO_ROOT / "data" / "results_verification"


def rebuild(results_root: Path) -> None:
    n_written = n_stripped = 0
    skipped: List[str] = []
    for rdir in sorted(results_root.iterdir()):
        if not rdir.is_dir() or rdir.name.startswith("xx_optimized"):
            continue
        raw = rdir / "fortran_optimal_run.out"
        if not raw.exists():
            skipped.append(rdir.name)
            continue

        # Region names never contain "_", so everything from the first "_" on
        # is the isolation suffix of the run.
        region, sep, suffix = rdir.name.partition("_")
        dest_dir = results_root / ("xx_optimized" + sep + suffix)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / "xx.{}".format(REGION_SHORTCODE.get(region, region))

        text = raw.read_text(encoding="ascii", errors="replace")
        clean = strip_override_echo(text)
        dest.write_text(clean)
        n_written += 1
        if len(clean) != len(text):
            n_stripped += 1
            print("  {:<22s} -> {}  (override echo removed)".format(rdir.name, dest))
        else:
            print("  {:<22s} -> {}  (already clean)".format(rdir.name, dest))

    print("\n{} xx reports written ({} had the echo block removed).".format(
        n_written, n_stripped))
    if skipped:
        print("Skipped (no fortran_optimal_run.out): {}".format(", ".join(skipped)))


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--results-root", type=Path, default=RESULTS_ROOT,
                        help="Root of per-region result folders (default: %(default)s).")
    args = parser.parse_args(argv)
    if not args.results_root.exists():
        raise SystemExit("Results root not found: {}".format(args.results_root))
    rebuild(args.results_root)


if __name__ == "__main__":
    main()

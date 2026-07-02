"""Regenerate the xx deliverable files from retained Fortran outputs.

For every optimized region under data/results_verification/, reads the retained
final-run output (fortran_optimal_run.out — identical content to what was first
written to xx_optimized/), strips the READ_FACTOR_OVERRIDES stdout echo that the
PI's pristine xx files do not contain, and rewrites
data/results_verification/xx_optimized[/­_dcN]/xx.<SHORTCODE>.

No simulation is run; this is a pure re-export.  Use it once to clean deliverables
produced before the echo-stripping was added to the workflow (runs made after
that write clean files directly, so re-running this is always a no-op-safe).

Usage:
    python -m scripts.rebuild_xx_deliverables
    python -m scripts.rebuild_xx_deliverables --results-root path/to/results
"""
import argparse
import re
from pathlib import Path
from typing import Optional, List

from src.region_shortcodes import REGION_SHORTCODE
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

        # <REGION>[_scratch][_dc<N>] (isolated scratch / data-center results dirs
        # mirror their suffix in the xx_optimized folder name)
        label = rdir.name
        m = re.match(r"(?P<region>.+?)(?P<suffix>(_scratch)?(_dc\d+)?)$", label)
        region = m.group("region")
        subdir = "xx_optimized" + m.group("suffix")

        shortcode = REGION_SHORTCODE.get(region, region)
        dest_dir = results_root / subdir
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / "xx.{}".format(shortcode)

        text = raw.read_text(encoding="ascii", errors="replace")
        clean = strip_override_echo(text)
        dest.write_text(clean)
        n_written += 1
        if len(clean) != len(text):
            n_stripped += 1
            print("  {:<22s} -> {}  (override echo removed)".format(label, dest))
        else:
            print("  {:<22s} -> {}  (already clean)".format(label, dest))

    print("\n{} deliverables written ({} had the echo block removed).".format(
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

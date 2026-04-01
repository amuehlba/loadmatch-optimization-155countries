#!/usr/bin/env python3
"""
check_inputs.py — Validate required input data files for a given LoadMatch region.

Exits 0 and writes a sentinel file if all required inputs are present.
Exits 1 with clear error messages if any critical file is missing.

Called by the Snakemake `check_inputs` rule before any computation starts.
Can also be run standalone:
    python -m scripts.check_inputs --region EUROPE --sentinel /tmp/check.done
"""

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Shared files required for every region.
SHARED_REQUIRED = {
    "data/raw/countrystats.dat":       "country statistics (base capacities, areas)",
    "data/raw/loadreg.COUNTRY2030GW":  "hourly electricity demand for all regions",
    "data/raw/heatcooldd.dat":         "heating/cooling degree days",
    "data/raw/heatfrac.dat":           "monthly heat/cold fractions",
}

FORTRAN_BINARY = "fortran/bin/powerworld"


def check_inputs(region: str) -> bool:
    """
    Validate all required and optional input files for `region`.

    Prints ERROR lines for missing required files (causes sys.exit(1)).
    Prints WARN lines for missing optional files (run continues).

    Returns True if all required files are present.
    """
    errors: list[str] = []
    warnings: list[str] = []

    # ── Shared required files ─────────────────────────────────────────────────
    for rel, description in SHARED_REQUIRED.items():
        p = REPO_ROOT / rel
        if not p.exists():
            errors.append(
                f"Missing: {p}\n"
                f"         ({description})"
            )
        elif p.stat().st_size == 0:
            errors.append(f"Empty file (expected data): {p}")

    # ── Fortran binary ────────────────────────────────────────────────────────
    binary = REPO_ROOT / FORTRAN_BINARY
    if not binary.exists():
        errors.append(
            f"Fortran binary not found: {binary}\n"
            "  Compile it with:\n"
            "    mkdir -p fortran/build fortran/bin\n"
            "    gfortran -O2 -mcmodel=medium -fdefault-real-8 -fdefault-double-8 \\\n"
            "        -fno-automatic -J fortran/build \\\n"
            "        fortran/src/powerworld.f -o fortran/bin/powerworld\n"
            "  Or set fortran.compile: true in config/workflow.yaml."
        )

    # ── Per-region supply file (one of two forms must exist) ──────────────────
    supply_region = REPO_ROOT / f"data/raw/wwssupworld.{region}"
    supply_raw    = REPO_ROOT / "data/raw/wwssupworld.dat"
    if supply_region.exists():
        print(f"  Supply file : {supply_region.name}  ✓")
    elif supply_raw.exists():
        print(
            f"  Supply file : wwssupworld.dat  (will run IFREWRITE=1,2 to create "
            f"wwssupworld.{region})"
        )
    else:
        errors.append(
            f"No supply file found for region '{region}'.\n"
            f"  Expected (aggregated form) : data/raw/wwssupworld.{region}\n"
            f"  or (raw form, rename to)   : data/raw/wwssupworld.dat\n"
            f"  Obtain wwssupworld.{region} from the Jacobson group GATOR-GCMOM output."
        )

    # ── Optional: canonical Jacobson baseline ─────────────────────────────────
    xxegs = REPO_ROOT / f"data/raw/xxEGS.{region}"
    if not xxegs.exists():
        warnings.append(
            f"data/raw/xxEGS.{region} not found.\n"
            f"  Figures comparing against the published Jacobson-group baseline\n"
            f"  (fig9, fig12, fig13) will be skipped or incomplete."
        )

    # ── Optional: pre-computed baseline warm-start ────────────────────────────
    bl_dat = REPO_ROOT / f"data/raw/baseline_results.{region}.dat"
    if not bl_dat.exists():
        warnings.append(
            f"data/raw/baseline_results.{region}.dat not found.\n"
            f"  GA warm-start will use Fortran region defaults (--baseline-start defaults)."
        )

    # ── Report ────────────────────────────────────────────────────────────────
    for w in warnings:
        for line in w.splitlines():
            print(f"  [WARN]  {line}")

    if errors:
        print(f"\n  {len(errors)} ERROR(s) found for region '{region}':", file=sys.stderr)
        for e in errors:
            for line in e.splitlines():
                print(f"  [ERROR] {line}", file=sys.stderr)
        return False

    return True


def main():
    parser = argparse.ArgumentParser(
        description="Validate required input data files for a LoadMatch region."
    )
    parser.add_argument(
        "--region",
        required=True,
        metavar="REGION",
        help="Region name (e.g. EUROPE, JAPAN, UNITED-STATES).",
    )
    parser.add_argument(
        "--sentinel",
        type=Path,
        required=True,
        metavar="PATH",
        help="Sentinel file to create on success (touched by Snakemake via touch()).",
    )
    args = parser.parse_args()

    print(f"Checking inputs for region: {args.region}")
    ok = check_inputs(args.region)

    if not ok:
        print(
            f"\n[FATAL] Input check failed for '{args.region}'. "
            "Resolve the errors above before running the workflow.",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"  Input check passed for '{args.region}'.")


if __name__ == "__main__":
    main()

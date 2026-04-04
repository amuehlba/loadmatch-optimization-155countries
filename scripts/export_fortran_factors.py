"""Convert LP summary.dat (PARAM_REGISTRY-keyed values) into a complete
fortran_factors.dat by merging LP-optimised values with the fixed PARAM_REGISTRY
defaults for all other parameters.
"""
import argparse
from pathlib import Path
from typing import Optional, Sequence

from src.io.dat_parser import read_dat, write_dat


def parse_args(argv: Optional[Sequence[str]] = None):
    parser = argparse.ArgumentParser(
        description="Merge LP results with PARAM_REGISTRY defaults → fortran_factors.dat"
    )
    parser.add_argument(
        "--summary",
        type=Path,
        default=Path("data/results_python/summary.dat"),
        help="Path to summary.dat written by run_python_model (contains PARAM_REGISTRY keys).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/results_python/fortran_factors.dat"),
        help="Destination .dat file to write the complete factor set.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None):
    args = parse_args(argv)

    if not args.summary.exists():
        raise FileNotFoundError(f"LP summary not found: {args.summary}")

    lp_values = read_dat(str(args.summary))

    # Start from PARAM_REGISTRY defaults (covers all parameters including fixed ones),
    # then override with LP-optimised values for the 20 non-fixed parameters.
    from scripts.run_full_workflow import PARAM_REGISTRY
    all_factors = {k: PARAM_REGISTRY[k][0] for k in PARAM_REGISTRY}
    # LP summary contains PARAM_REGISTRY-keyed values + "objective_cost"; skip unknowns.
    for key, val in lp_values.items():
        if key in all_factors:
            all_factors[key] = val

    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_dat(all_factors, args.output)

    print(f"Wrote Fortran factor overrides to {args.output}")
    for name, value in all_factors.items():
        print(f"  {name:12s} = {value:.6f}")


if __name__ == "__main__":
    main()

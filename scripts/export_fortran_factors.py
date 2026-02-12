import argparse
from pathlib import Path
from typing import Dict, Optional, Sequence

from src.io.data_loader import load_inputs
from src.io.dat_parser import read_dat, write_dat


def _ratio(total: float, base: float, label: str) -> float:
    if base <= 0:
        if total <= 0:
            return 1.0
        raise ValueError(
            f"Cannot compute scaling factor for {label}: base capacity is zero but the "
            f"optimization selected {total:.3f} MW. Update the spreadsheet inputs or "
            f"handle this case manually."
        )
    return total / base


def compute_fortran_factors(region: str, summary: Dict[str, float], inputs: Dict[str, object]) -> Dict[str, float]:
    base_caps = inputs["base_capacities_mw"]
    base_detail = inputs.get("base_capacities_detail", {})

    factors: Dict[str, float] = {}

    factors["FACONWIN"] = _ratio(
        summary["onshore_wind_capacity_total_mw"],
        base_caps["onshore_wind"],
        "onshore wind",
    )
    factors["FACOFFWIN"] = _ratio(
        summary["offshore_wind_capacity_total_mw"],
        base_caps["offshore_wind"],
        "offshore wind",
    )
    factors["FACUTILPV"] = _ratio(
        summary["utility_pv_capacity_total_mw"],
        base_caps["utility_pv"],
        "utility PV",
    )

    rooftop_total = summary["rooftop_pv_capacity_total_mw"]
    rooftop_base_total = base_detail.get("res_rooftop_pv", 0.0) + base_detail.get("com_rooftop_pv", 0.0)
    rooftop_factor = _ratio(
        rooftop_total,
        rooftop_base_total,
        "rooftop PV (combined residential + commercial/gov)",
    )
    factors["FACRESPV"] = rooftop_factor
    factors["FACCOMPV"] = rooftop_factor

    csp_original = base_detail.get("csp_original", 0.0)
    csp_additional = base_detail.get("csp_additional", 0.0)
    target_csp_turbine = summary["csp_capacity_total_mw"] - csp_additional
    factors["CSPTURBFAC"] = _ratio(
        max(target_csp_turbine, 0.0),
        csp_original if csp_original > 0 else base_caps["csp"],
        "CSP turbine",
    )

    solar_heat_base = base_detail.get("solar_thermal", base_caps["solar_thermal"])
    factors["FACSHT"] = _ratio(
        summary["solar_thermal_capacity_total_mw"],
        solar_heat_base,
        "solar thermal heat",
    )

    return factors


def parse_args(argv: Optional[Sequence[str]] = None):
    parser = argparse.ArgumentParser(
        description="Convert optimized capacities into LoadMatch Fortran scaling factors."
    )
    parser.add_argument(
        "--region",
        default="UNITED-STATES",
        help="Region identifier used in the Fortran inputs (default: UNITED-STATES).",
    )
    parser.add_argument(
        "--summary",
        type=Path,
        default=Path("data/results_python/summary.dat"),
        help="Path to the summary.dat file produced by the Python optimization.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/results_python/fortran_factors.dat"),
        help="Destination .dat file to write the scaling factors.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None):
    args = parse_args(argv)

    if not args.summary.exists():
        raise FileNotFoundError(f"Optimization summary not found: {args.summary}")

    summary = read_dat(str(args.summary))
    inputs = load_inputs(args.region)
    factors = compute_fortran_factors(args.region, summary, inputs)

    # Merge LP capacity factors with Fortran CONUS defaults for all other
    # tunable parameters so that fortran_factors.dat is always complete.
    from scripts.run_full_workflow import PARAM_REGISTRY
    all_factors = {k: PARAM_REGISTRY[k][0] for k in PARAM_REGISTRY}
    all_factors.update(factors)  # LP values override defaults for original 7

    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_dat(all_factors, args.output)

    print(f"Wrote Fortran factor overrides to {args.output}")
    for name, value in all_factors.items():
        print(f"  {name:12s} = {value:.6f}")


if __name__ == "__main__":
    main()

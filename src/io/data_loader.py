from __future__ import annotations

import csv
import math
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

import numpy as np


SUPPLY_COLUMNS: Tuple[str, ...] = (
    "onshore_wind",
    "offshore_wind",
    "rooftop_pv",
    "utility_pv",
    "csp",
    "solar_thermal",
    "heat_demand",
    "cold_demand",
)

COUNTRY_ALIASES: Dict[str, str] = {
    "UNITED-STATES": "UNITED-STATES-OF-AMERICA",
}


def load_supply_profiles(region: str, filepath: Path) -> Dict[str, np.ndarray]:
    """
    Load high-resolution WWS supply data for a given region and aggregate to hourly averages.

    Parameters
    ----------
    region:
        Region identifier used in the wwssupworld files (e.g., ``UNITED-STATES``).
    filepath:
        Path to the ``wwssupworld.<region>`` file.

    Returns
    -------
    dict
        Mapping from column name to a numpy array of hourly averages (in TW).
        Includes ``heat_demand`` and ``cold_demand`` columns, which represent loads.
    """
    if not filepath.exists():
        raise FileNotFoundError(f"Supply file not found: {filepath}")

    sums: Dict[str, defaultdict[int, float]] = {
        name: defaultdict(float) for name in SUPPLY_COLUMNS
    }
    counts: defaultdict[int, int] = defaultdict(int)

    with filepath.open() as handle:
        for raw_line in handle:
            if not raw_line.startswith("WWST:"):
                continue

            parts = raw_line.split()
            # Format: "WWST:" <time_days> <region> value0 value1 ...
            time_days = float(parts[1])
            domain = parts[2].strip()

            if domain != region:
                continue

            time_hours = time_days * 24.0
            hour_index = int(math.floor(time_hours + 1e-9))

            values = [float(p) for p in parts[3:3 + len(SUPPLY_COLUMNS)]]
            for column, value in zip(SUPPLY_COLUMNS, values):
                sums[column][hour_index] += value

            counts[hour_index] += 1

    if not counts:
        raise ValueError(f"No supply data found for region '{region}' in {filepath}")

    hours_sorted = sorted(counts.keys())
    hourly_data: Dict[str, np.ndarray] = {}
    for column in SUPPLY_COLUMNS:
        column_values = np.array(
            [
                sums[column][hour] / counts[hour]
                for hour in hours_sorted
            ],
            dtype=float,
        )
        hourly_data[column] = column_values

    hourly_data["hours"] = np.array(hours_sorted, dtype=int)
    return hourly_data


def load_electric_load(country: str, filepath: Path) -> np.ndarray:
    """
    Load hourly electricity demand (in GW) for a given country from loadreg.COUNTRY2030GW.
    """
    if not filepath.exists():
        raise FileNotFoundError(f"Load file not found: {filepath}")

    with filepath.open() as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith('"'):
                continue
            if not line.startswith(country):
                continue

            parts = line.split(",")
            data = [float(value) for value in parts[1:]]
            return np.asarray(data, dtype=float)

    raise ValueError(f"Country '{country}' not found in {filepath}")


def load_countrystats(country: str, filepath: Path) -> Dict[str, float]:
    """
    Load row of countrystats.dat for the given country as a dictionary of floats.
    """
    if not filepath.exists():
        raise FileNotFoundError(f"Country stats file not found: {filepath}")

    with filepath.open() as handle:
        # Skip description lines until header
        for _ in range(2):
            next(handle)
        reader = csv.DictReader(handle, delimiter="\t")
        for row in reader:
            if not row or row["Country"] != country:
                continue
            numeric_row: Dict[str, float] = {}
            for key, value in row.items():
                if value is None or value == "":
                    numeric_row[key] = 0.0
                else:
                    try:
                        numeric_row[key] = float(value)
                    except ValueError:
                        numeric_row[key] = 0.0
            return numeric_row

    raise ValueError(f"Country '{country}' not found in {filepath}")


def load_inputs(region: str, data_dir: Path | None = None) -> Dict[str, object]:
    """
    Aggregate all inputs required by the optimization model for a single region.

    Returns
    -------
    dict
        A dictionary containing time series, capacities, and storage metadata.
        All power values are returned in MW, and all energy values in MWh.
    """
    data_dir = data_dir or Path("data/raw")
    region = region.strip().upper()
    country = COUNTRY_ALIASES.get(region, region)

    supply_file = data_dir / f"wwssupworld.{region}"
    supply = load_supply_profiles(region, supply_file)

    load_file = data_dir / "loadreg.COUNTRY2030GW"
    electric_load_gw = load_electric_load(country, load_file)

    stats_file = data_dir / "countrystats.dat"
    stats = load_countrystats(country, stats_file)

    hours = supply.pop("hours")
    n_hours_supply = len(hours)
    n_hours_load = len(electric_load_gw)
    n_hours = min(n_hours_supply, n_hours_load)

    # Convert supply profiles from TW to MW
    supply_mw = {
        key: values[:n_hours] * 1_000_000.0
        for key, values in supply.items()
    }

    electric_load_mw = electric_load_gw[:n_hours] * 1_000.0
    heat_load_mw = supply_mw["heat_demand"]
    cold_load_mw = supply_mw["cold_demand"]

    base_capacities_mw = {
        "onshore_wind": stats.get("TMWONWIND", 0.0),
        "offshore_wind": stats.get("TWOFFWIND", 0.0),
        "rooftop_pv": stats.get("TMWRESPV", 0.0) + stats.get("TMWCOMPV", 0.0),
        "utility_pv": stats.get("TMWUTILPV", 0.0),
        "csp": stats.get("TMWCSPORIG", 0.0) + stats.get("TMWCSP-Additional", 0.0),
        "solar_thermal": stats.get("TMWSOLTH", 0.0),
    }

    base_capacities_detail = {
        "res_rooftop_pv": stats.get("TMWRESPV", 0.0),
        "com_rooftop_pv": stats.get("TMWCOMPV", 0.0),
        "csp_original": stats.get("TMWCSPORIG", 0.0),
        "csp_additional": stats.get("TMWCSP-Additional", 0.0),
        "solar_thermal": stats.get("TMWSOLTH", 0.0),
    }

    availability = {}
    for tech in ("onshore_wind", "offshore_wind", "rooftop_pv", "utility_pv", "csp"):
        base_capacity = base_capacities_mw[tech]
        profile = supply_mw[tech]
        if base_capacity > 0:
            availability[tech] = profile / base_capacity
        else:
            availability[tech] = np.zeros_like(profile)

    solar_thermal_profile = supply_mw["solar_thermal"]
    if base_capacities_mw["solar_thermal"] > 0:
        solar_thermal_availability = (
            solar_thermal_profile / base_capacities_mw["solar_thermal"]
        )
    else:
        solar_thermal_availability = np.zeros_like(solar_thermal_profile)

    storage_defaults = {
        "electric": {
            "base_power_mw": stats.get("PHS-GW-Exist", 0.0) * 1_000.0,
            "energy_hours": 8.0,
        },
        "heat": {
            "base_power_mw": stats.get("SolThmGW-Exist", 0.0) * 1_000.0,
            "energy_hours": 6.0,
        },
        "cold": {
            "base_power_mw": 0.0,
            "energy_hours": 6.0,
        },
    }

    supply_profiles_mw = {
        key: supply_mw[key]
        for key in (
            "onshore_wind",
            "offshore_wind",
            "rooftop_pv",
            "utility_pv",
            "csp",
            "solar_thermal",
        )
    }

    return {
        "region": region,
        "country": country,
        "hours": hours[:n_hours],
        "electric_load_mw": electric_load_mw,
        "heat_load_mw": heat_load_mw,
        "cold_load_mw": cold_load_mw,
        "availability": availability,
        "solar_thermal_availability": solar_thermal_availability,
        "base_capacities_mw": base_capacities_mw,
        "base_capacities_detail": base_capacities_detail,
        "storage": storage_defaults,
        "supply_profiles_mw": supply_profiles_mw,
    }

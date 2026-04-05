import csv
import math
import re
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

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

# Single-country regions whose name in loadreg/countrystats differs from the
# Snakemake region identifier.
COUNTRY_ALIASES: Dict[str, str] = {
    "UNITED-STATES": "UNITED-STATES-OF-AMERICA",
}

# Fortran writes scientific notation without 'E' for very small/large exponents,
# e.g. "9.59388129-109" instead of "9.59388129E-109".
_FORTRAN_FLOAT_RE = re.compile(r'(\d)([-+])(\d)')


def _parse_float(s: str) -> float:
    """Parse a float that may use Fortran scientific notation (missing E)."""
    return float(_FORTRAN_FLOAT_RE.sub(r'\1E\2\3', s))


def load_supply_profiles(region: str, filepath: Path) -> Dict[str, np.ndarray]:
    """Load wwssupworld.<region> and aggregate sub-hourly rows to hourly averages.

    Returns a dict of column name → array (TW), plus an "hours" key.
    """
    if not filepath.exists():
        raise FileNotFoundError(f"Supply file not found: {filepath}")

    sums: Dict[str, defaultdict] = {name: defaultdict(float) for name in SUPPLY_COLUMNS}
    counts: defaultdict = defaultdict(int)

    with filepath.open() as handle:
        for raw_line in handle:
            if not raw_line.startswith("WWST:"):
                continue

            parts = raw_line.split()
            time_days = _parse_float(parts[1])
            domain = parts[2].strip()

            if domain != region:
                continue

            time_hours = time_days * 24.0
            hour_index = int(math.floor(time_hours + 1e-9))

            values = [_parse_float(p) for p in parts[3:3 + len(SUPPLY_COLUMNS)]]
            for column, value in zip(SUPPLY_COLUMNS, values):
                sums[column][hour_index] += value
            counts[hour_index] += 1

    if not counts:
        raise ValueError(f"No supply data found for region '{region}' in {filepath}")

    hours_sorted = sorted(counts.keys())
    hourly_data: Dict[str, np.ndarray] = {}
    for column in SUPPLY_COLUMNS:
        hourly_data[column] = np.array(
            [sums[column][h] / counts[h] for h in hours_sorted], dtype=float
        )
    hourly_data["hours"] = np.array(hours_sorted, dtype=int)
    return hourly_data


def _read_countrystats_rows(filepath: Path) -> List[Dict[str, object]]:
    """Read all rows from countrystats.dat as a list of dicts (raw strings kept)."""
    if not filepath.exists():
        raise FileNotFoundError(f"Country stats file not found: {filepath}")

    rows = []
    with filepath.open() as handle:
        for _ in range(2):          # skip 2 description lines
            next(handle)
        reader = csv.DictReader(handle, delimiter="\t")
        for row in reader:
            if row:
                rows.append(row)
    return rows


def _row_to_floats(row: Dict[str, object]) -> Dict[str, float]:
    result: Dict[str, float] = {}
    for key, value in row.items():
        if value is None or str(value).strip() == "":
            result[key] = 0.0
        else:
            try:
                result[key] = float(value)
            except (ValueError, TypeError):
                result[key] = 0.0
    return result


def _sum_float_rows(rows: List[Dict[str, object]]) -> Dict[str, float]:
    """Sum numeric columns across multiple countrystats rows."""
    totals: Dict[str, float] = {}
    for row in rows:
        for key, value in _row_to_floats(row).items():
            totals[key] = totals.get(key, 0.0) + value
    return totals


def load_countrystats(region: str, filepath: Path) -> Dict[str, float]:
    """Return summed countrystats values for a region.

    Strategy:
      1. Look for an exact Country match (single-country regions).
      2. Fall back to summing all rows whose GRID-REGION column matches.
    """
    all_rows = _read_countrystats_rows(filepath)

    # Exact country match first
    exact = [r for r in all_rows if r.get("Country", "").strip() == region]
    if exact:
        return _row_to_floats(exact[0])

    # Aggregate by GRID-REGION
    grid_rows = [r for r in all_rows if r.get("GRID-REGION", "").strip() == region]
    if grid_rows:
        print(f"  [INFO] countrystats: aggregating {len(grid_rows)} countries "
              f"for region '{region}'")
        return _sum_float_rows(grid_rows)

    raise ValueError(
        f"Region '{region}' not found as Country or GRID-REGION in {filepath}"
    )


def load_electric_load(region: str, filepath: Path,
                       country_names: Optional[List[str]] = None) -> np.ndarray:
    """Load hourly electricity demand (GW) from loadreg.COUNTRY2030GW.

    Strategy:
      1. Exact match on the region/country name (single-country or pre-aggregated row).
      2. If not found and country_names is provided, sum loads for each country
         whose name starts with any entry in country_names (handles partial matches
         like "Slovenia-Croat" aggregating several Balkan countries).
    """
    if not filepath.exists():
        raise FileNotFoundError(f"Load file not found: {filepath}")

    def _parse_row(line: str) -> np.ndarray:
        parts = line.split(",")
        return np.asarray([float(v) for v in parts[1:]], dtype=float)

    # Build a dict of all rows keyed by their first token (country/region label)
    load_rows: Dict[str, np.ndarray] = {}
    with filepath.open() as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith('"'):
                continue
            name = line.split(",")[0].strip()
            if name:
                load_rows[name] = _parse_row(line)

    # 1. Exact match
    if region in load_rows:
        return load_rows[region]

    # 2. Prefix match across all known country names for the region
    if country_names:
        total: Optional[np.ndarray] = None
        matched: List[str] = []
        used_rows = set()
        for cname in country_names:
            for key, arr in load_rows.items():
                if key in used_rows:
                    continue
                # Match if key starts with any country name prefix (handles
                # aggregated rows like "Slovenia-Croat" that cover multiple countries)
                if key.startswith(cname) or cname.startswith(key):
                    if total is None:
                        total = arr.copy()
                    else:
                        n = min(len(total), len(arr))
                        total = total[:n] + arr[:n]
                    matched.append(key)
                    used_rows.add(key)
        if total is not None:
            print(f"  [INFO] loadreg: aggregated {len(matched)} rows for '{region}': "
                  f"{matched}")
            return total

    raise ValueError(
        f"Region/country '{region}' not found in {filepath}. "
        "If this is a multi-country region, ensure country_names is provided."
    )


def load_inputs(region: str, data_dir: Optional[Path] = None) -> Dict[str, object]:
    """Aggregate all inputs required by the LP for a single region.

    Handles both single-country regions (exact lookup) and multi-country regions
    (GRID-REGION aggregation in countrystats; summed load rows in loadreg).

    All power values returned in MW; energy in MWh.
    """
    data_dir = data_dir or Path("data/raw")
    region = region.strip().upper()

    # ── Supply profiles (already region-aggregated by IFREWRITE preprocessing) ─
    supply_file = data_dir / f"wwssupworld.{region}"
    supply = load_supply_profiles(region, supply_file)

    # ── Country stats (summed across all countries in this GRID-REGION) ────────
    stats_file = data_dir / "countrystats.dat"
    # Try the COUNTRY_ALIASES name first for single-country regions
    alias = COUNTRY_ALIASES.get(region, region)
    try:
        stats = load_countrystats(alias, stats_file)
    except ValueError:
        stats = load_countrystats(region, stats_file)

    # Collect the list of individual country names for this region (for loadreg)
    all_rows = _read_countrystats_rows(stats_file)
    country_names: List[str] = [
        r["Country"].strip()
        for r in all_rows
        if r.get("GRID-REGION", "").strip() == region
           or r.get("Country", "").strip() in (region, alias)
    ]

    # ── Electric load ──────────────────────────────────────────────────────────
    load_file = data_dir / "loadreg.COUNTRY2030GW"
    electric_load_gw = load_electric_load(
        alias, load_file, country_names=country_names or None
    )

    # ── Time alignment ─────────────────────────────────────────────────────────
    hours = supply.pop("hours")
    n_hours = min(len(hours), len(electric_load_gw))

    # Convert supply profiles from TW to MW
    supply_mw = {key: values[:n_hours] * 1_000_000.0 for key, values in supply.items()}

    electric_load_mw = electric_load_gw[:n_hours] * 1_000.0
    heat_load_mw = supply_mw["heat_demand"]
    cold_load_mw = supply_mw["cold_demand"]

    # ── Base capacities ────────────────────────────────────────────────────────
    base_capacities_mw = {
        "onshore_wind":  stats.get("TMWONWIND", 0.0),
        "offshore_wind": stats.get("TWOFFWIND", 0.0),
        "rooftop_pv":    stats.get("TMWRESPV",  0.0) + stats.get("TMWCOMPV", 0.0),
        "utility_pv":    stats.get("TMWUTILPV", 0.0),
        "csp":           stats.get("TMWCSPORIG", 0.0) + stats.get("TMWCSP-Additional", 0.0),
        "solar_thermal": stats.get("TMWSOLTH",  0.0),
    }

    base_capacities_detail = {
        "res_rooftop_pv": stats.get("TMWRESPV",           0.0),
        "com_rooftop_pv": stats.get("TMWCOMPV",           0.0),
        "csp_original":   stats.get("TMWCSPORIG",         0.0),
        "csp_additional": stats.get("TMWCSP-Additional",  0.0),
        "solar_thermal":  stats.get("TMWSOLTH",           0.0),
    }

    # ── Fixed baseload (constant dispatch, not LP decision variables) ──────────
    # SUPHYD2050 etc. are average annual supply in GW → convert to MW.
    fixed_baseload_mw = {
        "hydro":    stats.get("SUPHYD2050", 0.0) * 1_000.0,
        "tidal":    stats.get("SUPTID2050", 0.0) * 1_000.0,
        "wave":     stats.get("SUPWAV2050", 0.0) * 1_000.0,
        "geo_elec": stats.get("SUPGEL2050", 0.0) * 1_000.0,
        "geo_heat": stats.get("SUPGHT2050", 0.0) * 1_000.0,
    }

    # ── Availability factors ───────────────────────────────────────────────────
    availability = {}
    for tech in ("onshore_wind", "offshore_wind", "rooftop_pv", "utility_pv", "csp"):
        base = base_capacities_mw[tech]
        profile = supply_mw[tech]
        availability[tech] = profile / base if base > 0 else np.zeros_like(profile)

    solar_base = base_capacities_mw["solar_thermal"]
    solar_thermal_availability = (
        supply_mw["solar_thermal"] / solar_base
        if solar_base > 0 else np.zeros(n_hours)
    )

    storage_defaults = {
        "electric": {
            "base_power_mw": stats.get("PHS-GW-Exist",   0.0) * 1_000.0,
            "energy_hours":  8.0,
        },
        "heat": {
            "base_power_mw": stats.get("SolThmGW-Exist", 0.0) * 1_000.0,
            "energy_hours":  6.0,
        },
        "cold": {
            "base_power_mw": 0.0,
            "energy_hours":  6.0,
        },
    }

    supply_profiles_mw = {
        key: supply_mw[key]
        for key in ("onshore_wind", "offshore_wind", "rooftop_pv",
                    "utility_pv", "csp", "solar_thermal")
    }

    return {
        "region":                    region,
        "country":                   alias,
        "hours":                     hours[:n_hours],
        "electric_load_mw":          electric_load_mw,
        "heat_load_mw":              heat_load_mw,
        "cold_load_mw":              cold_load_mw,
        "availability":              availability,
        "solar_thermal_availability":solar_thermal_availability,
        "base_capacities_mw":        base_capacities_mw,
        "base_capacities_detail":    base_capacities_detail,
        "fixed_baseload_mw":         fixed_baseload_mw,
        "storage":                   storage_defaults,
        "supply_profiles_mw":        supply_profiles_mw,
    }

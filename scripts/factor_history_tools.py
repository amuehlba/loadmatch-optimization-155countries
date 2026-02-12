"""
Utilities for parsing, analyzing, and plotting Hooke–Jeeves factor history logs.

Usage example (after copying `data/results_verification/factor_history.log`
from the cluster to your local machine):

```python
from pathlib import Path
import matplotlib.pyplot as plt
from scripts.factor_history_tools import (
    parse_factor_history,
    records_to_dataframe,
    add_absolute_capacities,
    plot_factor_trajectories,
    plot_cost_and_feasibility,
)

records = parse_factor_history(Path("factor_history.log"))
df = records_to_dataframe(records)
df = add_absolute_capacities(df, region="UNITED-STATES")

plot_factor_trajectories(df, absolute=True)
plot_cost_and_feasibility(df)
plt.show()
```
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

import matplotlib.pyplot as plt
import pandas as pd

from src.io.data_loader import load_inputs

# All tunable parameters (lowercase).  Must stay in sync with PARAM_REGISTRY
# in scripts/run_full_workflow.py.
FACTOR_COLUMNS = [
    # Original 7 capacity factors
    "faconwin",
    "facoffwin",
    "facutilpv",
    "facrespv",
    "faccompv",
    "cspturbfac",
    "facsht",
    # CSP / storage configuration
    "cspstorgat",
    "mxhrdrm",
    "batdisch",
    "hcharcsp",
    "storhbat",
    "storhcold",
    "storhhwat",
    "storhphs",
    # UTES and hydrogen storage
    "ugfac",
    "storugdys",
    "dayh2stor",
    # Hydropower
    "hpturbrat",
    "damcaprat",
    "daybashyd",
    # Thermal storage and demand response
    "coolstes",
    "phsmin",
    "fheatflx",
    "fcoldflx",
    "frstorinit",
    "fdistheat",
    # Heat pump and health
    "cperform",
    "hcddadd",
    "fmortbau",
    # Hot-water, H2, heat battery
    "hwfac",
    "fcdisch",
    "fccharg",
    "storhhfc",
    "hbtdisch",
    "storhhbt",
    # Industrial heat flexibility
    "frcihflex",
]

# Subset used for the original capacity-based trajectory plots
CAPACITY_COLUMNS = [
    "faconwin", "facoffwin", "facutilpv", "facrespv",
    "faccompv", "cspturbfac", "facsht",
]


def parse_factor_history(log_path: Path) -> List[Dict[str, float]]:
    """Parse a `factor_history.log` file into a list of dictionaries."""
    records: List[Dict[str, float]] = []
    current: Dict[str, float] = {}

    with log_path.open() as handle:
        for raw in handle:
            line = raw.strip()
            if not line:
                if current:
                    records.append(current)
                    current = {}
                continue

            if line.startswith("LABEL:"):
                if current:
                    records.append(current)
                    current = {}
                current["label"] = line.split(":", 1)[1].strip()
            elif line.startswith("FEASIBLE:"):
                current["feasible"] = (
                    line.split(":", 1)[1].strip().lower() == "true"
                )
            elif line.startswith("COST_MN_BIL_PER_YEAR:"):
                current["cost_mn_bil_per_year"] = float(
                    line.split(":", 1)[1].strip()
                )
            elif "=" in line:
                key, value = line.split("=")
                key = key.strip().lower()
                current[key] = float(value)

    if current:
        records.append(current)

    return records


def records_to_dataframe(records: Sequence[Dict[str, float]]) -> pd.DataFrame:
    """Convert parsed records into a pandas DataFrame with a trial index."""
    df = pd.DataFrame(records)
    df.insert(0, "trial", range(len(df)))
    return df


def add_absolute_capacities(df: pd.DataFrame, region: str = "UNITED-STATES") -> pd.DataFrame:
    """Append absolute capacity columns (MW) using base capacities from inputs."""
    if any(col.endswith("_capacity_mw") for col in df.columns):
        return df

    inputs = load_inputs(region)
    base_caps = inputs["base_capacities_mw"]
    detail = inputs.get("base_capacities_detail", {})

    df = df.copy()
    df["onshore_wind_capacity_mw"] = df["faconwin"] * base_caps["onshore_wind"]
    df["offshore_wind_capacity_mw"] = df["facoffwin"] * base_caps["offshore_wind"]
    df["utility_pv_capacity_mw"] = df["facutilpv"] * base_caps["utility_pv"]

    res_base = detail.get("res_rooftop_pv", 0.0)
    com_base = detail.get("com_rooftop_pv", 0.0)
    df["res_rooftop_pv_capacity_mw"] = df["facrespv"] * res_base
    df["com_rooftop_pv_capacity_mw"] = df["faccompv"] * com_base
    df["total_rooftop_pv_capacity_mw"] = (
        df["res_rooftop_pv_capacity_mw"] + df["com_rooftop_pv_capacity_mw"]
    )

    csp_base = detail.get("csp_original", base_caps["csp"])
    df["csp_capacity_mw"] = df["cspturbfac"] * csp_base
    solar_heat_base = detail.get("solar_thermal", base_caps["solar_thermal"])
    df["solar_thermal_capacity_mw"] = df["facsht"] * solar_heat_base

    return df


def plot_factor_trajectories(
    df: pd.DataFrame,
    absolute: bool = True,
    region: str = "UNITED-STATES",
    figsize: tuple = (12, 6),
    columns: Sequence[str] | None = None,
) -> plt.Axes:
    """Plot factor or capacity trajectories over trials.

    Parameters
    ----------
    columns : list of str, optional
        Explicit column names to plot.  When *None* (default), plots
        capacity columns if *absolute* is True or the original 7 capacity
        factor columns otherwise.  Pass ``FACTOR_COLUMNS`` to plot every
        parameter.
    """
    plot_df = df.copy()
    if columns is not None:
        ylabel = "Value"
        title = "Parameter trajectory"
    elif absolute:
        try:
            plot_df = add_absolute_capacities(plot_df, region=region)
            columns = [
                "onshore_wind_capacity_mw",
                "offshore_wind_capacity_mw",
                "utility_pv_capacity_mw",
                "total_rooftop_pv_capacity_mw",
                "csp_capacity_mw",
                "solar_thermal_capacity_mw",
            ]
            ylabel = "Capacity (MW)"
            title = "Factor trajectory (absolute MW)"
        except FileNotFoundError:
            print(
                "Absolute capacities unavailable (missing raw inputs); plotting multipliers instead."
            )
            columns = CAPACITY_COLUMNS
            ylabel = "Multiplier"
            title = "Factor trajectory (capacity multipliers)"
    else:
        columns = CAPACITY_COLUMNS
        ylabel = "Multiplier"
        title = "Factor trajectory (capacity multipliers)"

    plt.figure(figsize=figsize)
    for col in columns:
        if col in plot_df.columns:
            plt.plot(plot_df["trial"], plot_df[col], label=col)
    plt.xlabel("Trial")
    plt.ylabel(ylabel)
    plt.title(title)
    plt.legend(loc="best")
    plt.tight_layout()
    return plt.gca()


def plot_cost_and_feasibility(
    df: pd.DataFrame,
    figsize: tuple = (10, 4),
) -> plt.Axes:
    """Plot MN annual cost with markers for feasibility."""
    plt.figure(figsize=figsize)
    colors = df["feasible"].map({True: "tab:green", False: "tab:red"})
    plt.scatter(df["trial"], df["cost_mn_bil_per_year"], c=colors, label="cost")
    plt.xlabel("Trial")
    plt.ylabel("MN annual cost ($Billion/yr)")
    plt.title("Cost trajectory (green = feasible, red = infeasible)")
    plt.tight_layout()
    return plt.gca()

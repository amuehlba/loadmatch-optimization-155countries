from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable

import pyomo.environ as pyo


DEFAULT_CAPITAL_COSTS = {
    "onshore_wind": 1_350_000.0,
    "offshore_wind": 3_800_000.0,
    "rooftop_pv": 1_600_000.0,
    "utility_pv": 1_100_000.0,
    "csp": 4_500_000.0,
    "solar_thermal": 800_000.0,
}

DEFAULT_VARIABLE_COSTS = {
    "onshore_wind": 12.0,
    "offshore_wind": 20.0,
    "rooftop_pv": 8.0,
    "utility_pv": 5.0,
    "csp": 15.0,
    "solar_thermal": 5.0,
}

DEFAULT_STORAGE_COSTS = {
    "electric": {"power": 450_000.0, "energy": 180_000.0},
    "heat": {"power": 150_000.0, "energy": 40_000.0},
    "cold": {"power": 150_000.0, "energy": 40_000.0},
}

DEFAULT_CAPACITY_LIMITS_MW = {
    "onshore_wind": 5_000_000.0,
    "offshore_wind": 5_000_000.0,
    "rooftop_pv": 5_000_000.0,
    "utility_pv": 5_000_000.0,
    "csp": 5_000.0,
    "solar_thermal": 5_000_000.0,
}

LOAD_SHEDDING_PENALTY = 1_000_000.0
CURTAILMENT_PENALTY = 1.0


@dataclass(frozen=True)
class StorageParameters:
    base_power_mw: float
    energy_hours: float
    charge_efficiency: float
    discharge_efficiency: float

    @property
    def base_energy_mwh(self) -> float:
        return self.base_power_mw * self.energy_hours


def _dict_from_series(values: Iterable[float]) -> Dict[int, float]:
    return {idx: float(val) for idx, val in enumerate(values)}


def build_model(inputs: Dict[str, object]) -> pyo.ConcreteModel:
    """
    Construct a linear programming approximation of the LoadMatch dispatch problem.

    Parameters
    ----------
    inputs
        Dictionary produced by ``src.io.data_loader.load_inputs``.
        All powers are assumed to be in MW and energy in MWh.
    """

    hours = inputs["hours"]
    n_hours = len(hours)
    if n_hours == 0:
        raise ValueError("No hourly data supplied to build_model")

    availability = inputs["availability"]
    electric_techs = tuple(availability.keys())
    base_caps = inputs["base_capacities_mw"]

    solar_availability = inputs["solar_thermal_availability"]
    solar_base_capacity = base_caps["solar_thermal"]
    supply_profiles = inputs.get("supply_profiles_mw", {})

    electric_load = inputs["electric_load_mw"]
    heat_load = inputs["heat_load_mw"]
    cold_load = inputs["cold_load_mw"]

    storage_meta = inputs["storage"]
    storage_costs = inputs.get("storage_costs", DEFAULT_STORAGE_COSTS)

    # Heat pumps: convert electricity to thermal and cooling energy.
    heat_cop = inputs.get("heat_cop", 4.0)
    cold_cop = inputs.get("cold_cop", 3.0)

    user_capacity_limits = inputs.get("capacity_limits_mw", {})
    capacity_upper_bounds = {}
    for tech in electric_techs:
        default_limit = DEFAULT_CAPACITY_LIMITS_MW.get(tech, base_caps[tech])
        upper_bound = user_capacity_limits.get(tech, default_limit)
        if upper_bound <= 0.0:
            upper_bound = default_limit
        if upper_bound < base_caps[tech]:
            upper_bound = base_caps[tech]
        capacity_upper_bounds[tech] = upper_bound

    solar_capacity_max = inputs.get(
        "solar_capacity_limit_mw",
        max(
            DEFAULT_CAPACITY_LIMITS_MW.get("solar_thermal", solar_base_capacity),
            solar_base_capacity,
        ),
    )

    # Storage parameters for each carrier
    storage_params = {
        "electric": StorageParameters(
            base_power_mw=storage_meta["electric"]["base_power_mw"],
            energy_hours=storage_meta["electric"]["energy_hours"],
            charge_efficiency=0.95,
            discharge_efficiency=0.95,
        ),
        "heat": StorageParameters(
            base_power_mw=storage_meta["heat"]["base_power_mw"],
            energy_hours=storage_meta["heat"]["energy_hours"],
            charge_efficiency=0.95,
            discharge_efficiency=0.95,
        ),
        "cold": StorageParameters(
            base_power_mw=storage_meta["cold"]["base_power_mw"],
            energy_hours=storage_meta["cold"]["energy_hours"],
            charge_efficiency=0.9,
            discharge_efficiency=0.9,
        ),
    }

    capital_costs = {**DEFAULT_CAPITAL_COSTS, **inputs.get("capital_costs", {})}
    variable_costs = {**DEFAULT_VARIABLE_COSTS, **inputs.get("variable_costs", {})}

    model_name = f"LoadMatchLP_{inputs['region']}"
    m = pyo.ConcreteModel(name=model_name)
    m.T = pyo.RangeSet(0, n_hours - 1)
    m.electric_techs = pyo.Set(initialize=electric_techs, ordered=True)

    # Parameters for generation availability
    availability_data = {
        (tech, t): float(availability[tech][t])
        for tech in electric_techs
        for t in range(n_hours)
    }
    m.availability = pyo.Param(
        m.electric_techs,
        m.T,
        initialize=availability_data,
        within=pyo.NonNegativeReals,
        mutable=False,
    )

    m.base_capacity = pyo.Param(
        m.electric_techs,
        initialize={tech: float(base_caps[tech]) for tech in electric_techs},
        within=pyo.NonNegativeReals,
        mutable=False,
    )

    m.capital_cost = pyo.Param(
        m.electric_techs,
        initialize={tech: float(capital_costs[tech]) for tech in electric_techs},
        within=pyo.NonNegativeReals,
        mutable=False,
    )

    m.variable_cost = pyo.Param(
        m.electric_techs,
        initialize={tech: float(variable_costs.get(tech, 0.0)) for tech in electric_techs},
        mutable=False,
    )

    m.electric_load = pyo.Param(m.T, initialize=_dict_from_series(electric_load))
    m.heat_load = pyo.Param(m.T, initialize=_dict_from_series(heat_load))
    m.cold_load = pyo.Param(m.T, initialize=_dict_from_series(cold_load))

    # Decision variables
    m.capacity = pyo.Var(
        m.electric_techs,
        within=pyo.NonNegativeReals,
        bounds=lambda _m, tech: (0.0, capacity_upper_bounds[tech]),
    )
    m.gen = pyo.Var(m.electric_techs, m.T, within=pyo.NonNegativeReals)

    m.load_shed_electric = pyo.Var(m.T, within=pyo.NonNegativeReals)
    m.excess_electric = pyo.Var(m.T, within=pyo.NonNegativeReals)

    # Electricity storage
    elec_params = storage_params["electric"]
    m.storage_power_add_electric = pyo.Var(within=pyo.NonNegativeReals)
    m.charge_electric = pyo.Var(m.T, within=pyo.NonNegativeReals)
    m.discharge_electric = pyo.Var(m.T, within=pyo.NonNegativeReals)
    m.soc_electric = pyo.Var(m.T, within=pyo.NonNegativeReals)

    base_energy_electric = elec_params.base_energy_mwh
    m.soc0_electric = pyo.Param(initialize=0.5 * base_energy_electric, mutable=False)

    m.electric_power_limit = pyo.Constraint(
        m.T,
        rule=lambda _m, t: _m.charge_electric[t]
        <= elec_params.base_power_mw + _m.storage_power_add_electric,
    )
    m.electric_discharge_limit = pyo.Constraint(
        m.T,
        rule=lambda _m, t: _m.discharge_electric[t]
        <= elec_params.base_power_mw + _m.storage_power_add_electric,
    )

    electric_energy_capacity = (
        elec_params.base_power_mw + m.storage_power_add_electric
    ) * elec_params.energy_hours

    m.electric_soc_limit = pyo.Constraint(
        m.T,
        rule=lambda _m, t: _m.soc_electric[t] <= electric_energy_capacity,
    )

    def electric_soc_rule(_m, t):
        charge = elec_params.charge_efficiency * _m.charge_electric[t]
        discharge = _m.discharge_electric[t] / elec_params.discharge_efficiency
        if t == 0:
            return _m.soc_electric[t] == _m.soc0_electric + charge - discharge
        return _m.soc_electric[t] == _m.soc_electric[t - 1] + charge - discharge

    m.electric_soc_balance = pyo.Constraint(m.T, rule=electric_soc_rule)

    m.electric_ending_soc = pyo.Constraint(
        expr=m.soc_electric[n_hours - 1] == m.soc0_electric
    )

    # Heat sector variables
    solar_base_capacity = base_caps["solar_thermal"]
    solar_cap_cost = capital_costs["solar_thermal"]
    solar_var_cost = variable_costs.get("solar_thermal", 0.0)

    m.solar_capacity = pyo.Var(
        within=pyo.NonNegativeReals,
        bounds=(0.0, solar_capacity_max),
    )
    m.solar_gen = pyo.Var(m.T, within=pyo.NonNegativeReals)

    m.solar_availability = pyo.Param(
        m.T,
        initialize=_dict_from_series(solar_availability),
        within=pyo.NonNegativeReals,
        mutable=False,
    )

    m.electric_to_heat = pyo.Var(m.T, within=pyo.NonNegativeReals)
    m.load_shed_heat = pyo.Var(m.T, within=pyo.NonNegativeReals)
    m.excess_heat = pyo.Var(m.T, within=pyo.NonNegativeReals)

    heat_params = storage_params["heat"]
    m.storage_power_add_heat = pyo.Var(within=pyo.NonNegativeReals)
    m.charge_heat = pyo.Var(m.T, within=pyo.NonNegativeReals)
    m.discharge_heat = pyo.Var(m.T, within=pyo.NonNegativeReals)
    m.soc_heat = pyo.Var(m.T, within=pyo.NonNegativeReals)
    m.soc0_heat = pyo.Param(
        initialize=0.5 * heat_params.base_energy_mwh, mutable=False
    )

    def heat_generation_limit(_m, t):
        return _m.solar_gen[t] <= _m.solar_availability[t] * _m.solar_capacity

    m.heat_generation_limit = pyo.Constraint(m.T, rule=heat_generation_limit)

    m.heat_charge_limit = pyo.Constraint(
        m.T,
        rule=lambda _m, t: _m.charge_heat[t]
        <= heat_params.base_power_mw + _m.storage_power_add_heat,
    )
    m.heat_discharge_limit = pyo.Constraint(
        m.T,
        rule=lambda _m, t: _m.discharge_heat[t]
        <= heat_params.base_power_mw + _m.storage_power_add_heat,
    )

    heat_energy_capacity = (
        heat_params.base_power_mw + m.storage_power_add_heat
    ) * heat_params.energy_hours

    m.heat_soc_limit = pyo.Constraint(
        m.T,
        rule=lambda _m, t: _m.soc_heat[t] <= heat_energy_capacity,
    )

    def heat_soc_rule(_m, t):
        charge = heat_params.charge_efficiency * _m.charge_heat[t]
        discharge = _m.discharge_heat[t] / heat_params.discharge_efficiency
        if t == 0:
            return _m.soc_heat[t] == _m.soc0_heat + charge - discharge
        return _m.soc_heat[t] == _m.soc_heat[t - 1] + charge - discharge

    m.heat_soc_balance = pyo.Constraint(m.T, rule=heat_soc_rule)
    m.heat_ending_soc = pyo.Constraint(expr=m.soc_heat[n_hours - 1] == m.soc0_heat)

    # Cold sector variables
    m.electric_to_cold = pyo.Var(m.T, within=pyo.NonNegativeReals)
    m.load_shed_cold = pyo.Var(m.T, within=pyo.NonNegativeReals)
    m.excess_cold = pyo.Var(m.T, within=pyo.NonNegativeReals)

    cold_params = storage_params["cold"]
    m.storage_power_add_cold = pyo.Var(within=pyo.NonNegativeReals)
    m.charge_cold = pyo.Var(m.T, within=pyo.NonNegativeReals)
    m.discharge_cold = pyo.Var(m.T, within=pyo.NonNegativeReals)
    m.soc_cold = pyo.Var(m.T, within=pyo.NonNegativeReals)
    m.soc0_cold = pyo.Param(
        initialize=0.5 * cold_params.base_energy_mwh, mutable=False
    )

    m.cold_charge_limit = pyo.Constraint(
        m.T,
        rule=lambda _m, t: _m.charge_cold[t]
        <= cold_params.base_power_mw + _m.storage_power_add_cold,
    )
    m.cold_discharge_limit = pyo.Constraint(
        m.T,
        rule=lambda _m, t: _m.discharge_cold[t]
        <= cold_params.base_power_mw + _m.storage_power_add_cold,
    )

    cold_energy_capacity = (
        cold_params.base_power_mw + m.storage_power_add_cold
    ) * cold_params.energy_hours

    m.cold_soc_limit = pyo.Constraint(
        m.T,
        rule=lambda _m, t: _m.soc_cold[t] <= cold_energy_capacity,
    )

    def cold_soc_rule(_m, t):
        charge = cold_params.charge_efficiency * _m.charge_cold[t]
        discharge = _m.discharge_cold[t] / cold_params.discharge_efficiency
        if t == 0:
            return _m.soc_cold[t] == _m.soc0_cold + charge - discharge
        return _m.soc_cold[t] == _m.soc_cold[t - 1] + charge - discharge

    m.cold_soc_balance = pyo.Constraint(m.T, rule=cold_soc_rule)
    m.cold_ending_soc = pyo.Constraint(expr=m.soc_cold[n_hours - 1] == m.soc0_cold)

    # Generation capacity limits
    def generation_limit_rule(_m, tech, t):
        return _m.gen[tech, t] <= _m.availability[tech, t] * _m.capacity[tech]

    m.generation_limits = pyo.Constraint(
        m.electric_techs, m.T, rule=generation_limit_rule
    )

    # Energy balance constraints
    def electric_balance_rule(_m, t):
        supply = sum(_m.gen[tech, t] for tech in _m.electric_techs)
        supply += _m.discharge_electric[t]
        supply += _m.load_shed_electric[t]

        demand = _m.electric_load[t]
        demand += _m.charge_electric[t]
        demand += _m.electric_to_heat[t]
        demand += _m.electric_to_cold[t]
        demand += _m.excess_electric[t]
        return supply == demand

    m.electric_balance = pyo.Constraint(m.T, rule=electric_balance_rule)

    def heat_balance_rule(_m, t):
        supply = _m.solar_gen[t]
        supply += _m.discharge_heat[t]
        supply += heat_cop * _m.electric_to_heat[t]
        supply += _m.load_shed_heat[t]

        demand = _m.heat_load[t]
        demand += _m.charge_heat[t]
        demand += _m.excess_heat[t]
        return supply == demand

    m.heat_balance = pyo.Constraint(m.T, rule=heat_balance_rule)

    def cold_balance_rule(_m, t):
        supply = cold_cop * _m.electric_to_cold[t]
        supply += _m.discharge_cold[t]
        supply += _m.load_shed_cold[t]

        demand = _m.cold_load[t]
        demand += _m.charge_cold[t]
        demand += _m.excess_cold[t]
        return supply == demand

    m.cold_balance = pyo.Constraint(m.T, rule=cold_balance_rule)

    # Objective components
    capex_electric = sum(
        m.capital_cost[tech] * m.capacity[tech] for tech in m.electric_techs
    )

    capex_heat = solar_cap_cost * m.solar_capacity

    storage_capex = (
        storage_costs["electric"]["power"] * m.storage_power_add_electric
        + storage_costs["electric"]["energy"]
        * elec_params.energy_hours
        * m.storage_power_add_electric
        + storage_costs["heat"]["power"] * m.storage_power_add_heat
        + storage_costs["heat"]["energy"]
        * heat_params.energy_hours
        * m.storage_power_add_heat
        + storage_costs["cold"]["power"] * m.storage_power_add_cold
        + storage_costs["cold"]["energy"]
        * cold_params.energy_hours
        * m.storage_power_add_cold
    )

    var_costs_electric = sum(
        m.variable_cost[tech] * m.gen[tech, t]
        for tech in m.electric_techs
        for t in range(n_hours)
    )
    var_costs_heat = solar_var_cost * sum(m.solar_gen[t] for t in range(n_hours))

    penalty_costs = LOAD_SHEDDING_PENALTY * (
        sum(m.load_shed_electric[t] for t in range(n_hours))
        + sum(m.load_shed_heat[t] for t in range(n_hours))
        + sum(m.load_shed_cold[t] for t in range(n_hours))
    )

    curtail_costs = CURTAILMENT_PENALTY * (
        sum(m.excess_electric[t] for t in range(n_hours))
        + sum(m.excess_heat[t] for t in range(n_hours))
        + sum(m.excess_cold[t] for t in range(n_hours))
    )

    m.total_cost = pyo.Objective(
        expr=capex_electric
        + capex_heat
        + storage_capex
        + var_costs_electric
        + var_costs_heat
        + penalty_costs
        + curtail_costs,
        sense=pyo.minimize,
    )

    # Record additional items for downstream use if desired
    m.metadata = {
        "hours": hours,
        "supply_profiles_mw": supply_profiles,
        "heat_cop": heat_cop,
        "cold_cop": cold_cop,
        "solar_base_capacity_mw": solar_base_capacity,
        "capacity_upper_bounds_mw": capacity_upper_bounds,
        "solar_capacity_limit_mw": solar_capacity_max,
        "storage_base_power_mw": {
            "electric": elec_params.base_power_mw,
            "heat": heat_params.base_power_mw,
            "cold": cold_params.base_power_mw,
        },
    }

    return m

from __future__ import annotations

from pathlib import Path

import pyomo.environ as pyo

from src.io.data_loader import load_inputs
from src.io.dat_parser import write_dat
from src.optimization.model_builder import build_model


def collect_results(model: pyo.ConcreteModel) -> dict:
    """Extract a concise summary of the optimization outcome."""
    results = {}

    for tech in model.electric_techs:
        base_capacity = pyo.value(model.base_capacity[tech])
        added_capacity = pyo.value(model.cap_add[tech])
        results[f"{tech}_capacity_base_mw"] = base_capacity
        results[f"{tech}_capacity_add_mw"] = added_capacity
        results[f"{tech}_capacity_total_mw"] = base_capacity + added_capacity

    solar_base = model.metadata.get("solar_base_capacity_mw", 0.0)
    solar_add = pyo.value(model.solar_capacity_add)
    results["solar_thermal_capacity_base_mw"] = solar_base
    results["solar_thermal_capacity_add_mw"] = solar_add
    results["solar_thermal_capacity_total_mw"] = solar_base + solar_add

    storage_base = model.metadata.get("storage_base_power_mw", {})
    for carrier, var in [
        ("electric", model.storage_power_add_electric),
        ("heat", model.storage_power_add_heat),
        ("cold", model.storage_power_add_cold),
    ]:
        added = pyo.value(var)
        base_power = storage_base.get(carrier, 0.0)
        results[f"storage_power_{carrier}_base_mw"] = base_power
        results[f"storage_power_{carrier}_add_mw"] = added
        results[f"storage_power_{carrier}_total_mw"] = base_power + added

    results["objective_cost"] = pyo.value(model.total_cost)

    for carrier, var in [
        ("electric", model.load_shed_electric),
        ("heat", model.load_shed_heat),
        ("cold", model.load_shed_cold),
    ]:
        shed = sum(pyo.value(var[t]) for t in model.T)
        results[f"load_shed_{carrier}_mwh"] = shed

    return results


def main():
    inputs = load_inputs(region="UNITED-STATES")
    model = build_model(inputs)

    solver = pyo.SolverFactory("gurobi")
    solution = solver.solve(model, tee=True)
    if (solution.solver.status != pyo.SolverStatus.ok) or (
        solution.solver.termination_condition not in (pyo.TerminationCondition.optimal, pyo.TerminationCondition.feasible)
    ):
        raise RuntimeError(f"Solver failed: {solution.solver}")

    results = collect_results(model)

    output_dir = Path("data/results_python")
    output_dir.mkdir(parents=True, exist_ok=True)
    write_dat(results, output_dir / "summary.dat")
    print("Python optimization complete. Results stored in data/results_python/summary.dat")


if __name__ == "__main__":
    main()

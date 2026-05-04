import csv
import json
from pathlib import Path

import pyomo.environ as pyo

from src.io.data_loader import load_inputs
from src.io.dat_parser import write_dat
from src.optimization.model_builder import build_model, collect_results, collect_lp_solution


def main(region: str = "UNITED-STATES", output_dir: Path = None):
    if output_dir is None:
        output_dir = Path("data/results_python")
        if region != "UNITED-STATES":
            output_dir = output_dir / region

    inputs = load_inputs(region=region)
    model = build_model(inputs)

    solver = None
    for solver_name in ("gurobi", "appsi_highs", "highs", "glpk"):
        candidate = pyo.SolverFactory(solver_name)
        try:
            if candidate.available():
                solver = candidate
                print(f"Using solver: {solver_name}")
                break
        except Exception:
            pass
    if solver is None:
        raise RuntimeError(
            "No LP solver found. Install one of: gurobipy, highspy, glpk."
        )

    solution = solver.solve(model, tee=True)
    if (solution.solver.status != pyo.SolverStatus.ok) or (
        solution.solver.termination_condition not in (
            pyo.TerminationCondition.optimal, pyo.TerminationCondition.feasible)
    ):
        raise RuntimeError(f"Solver failed: {solution.solver}")

    output_dir.mkdir(parents=True, exist_ok=True)

    # ── Factor values + LP objective → summary.dat (existing format) ─────────
    results = collect_results(model)
    results["objective_cost"] = pyo.value(model.obj)
    write_dat(results, output_dir / "summary.dat")
    print(f"Python optimisation complete. Results stored in {output_dir / 'summary.dat'}")

    # ── Full LP solution → lp_solution.json + lp_dispatch_<month>.csv ────────
    lp_sol, dispatch_rows = collect_lp_solution(model, inputs)

    sol_path = output_dir / "lp_solution.json"
    with open(sol_path, "w") as f:
        json.dump(lp_sol, f, indent=2)
    print(f"  LP solution summary  → {sol_path}")

    month_name = lp_sol["exemplary_month"]["month_name"].lower()
    csv_path   = output_dir / f"lp_dispatch_{month_name}.csv"
    if dispatch_rows:
        fieldnames = list(dispatch_rows[0].keys())
        with open(csv_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(dispatch_rows)
        print(f"  LP dispatch ({lp_sol['exemplary_month']['month_name']}) → {csv_path}")


if __name__ == "__main__":
    main()

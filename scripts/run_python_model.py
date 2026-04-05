from pathlib import Path

import pyomo.environ as pyo

from src.io.data_loader import load_inputs
from src.io.dat_parser import write_dat
from src.optimization.model_builder import build_model, collect_results


def main(region: str = "UNITED-STATES", output_dir: Path = None):
    if output_dir is None:
        output_dir = Path("data/results_python")
        if region != "UNITED-STATES":
            output_dir = output_dir / region

    inputs = load_inputs(region=region)
    model = build_model(inputs)

    solver = None
    for solver_name in ("gurobi", "highs", "glpk"):
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

    results = collect_results(model)
    results["objective_cost"] = pyo.value(model.obj)

    output_dir.mkdir(parents=True, exist_ok=True)
    write_dat(results, output_dir / "summary.dat")
    print(f"Python optimization complete. Results stored in {output_dir / 'summary.dat'}")


if __name__ == "__main__":
    main()

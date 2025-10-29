from src.io.dat_parser import read_dat, write_dat
from src.optimization.model_builder import build_model
import pyomo.environ as pyo
from pathlib import Path

if __name__ == "__main__":
    params = read_dat("data/raw/sample_input.dat")
    model = build_model(params)
    solver = pyo.SolverFactory("highs")
    solver.solve(model, tee=True)

    results = {f"gen_{h}": pyo.value(model.gen[h]) for h in model.hours}
    Path("data/results_python").mkdir(parents=True, exist_ok=True)
    write_dat(results, "data/results_python/output.dat")
    print("Python optimization complete.")

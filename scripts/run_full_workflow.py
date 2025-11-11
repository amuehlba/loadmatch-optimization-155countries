import argparse
import multiprocessing
import os
import re
import shutil
import subprocess
import tempfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from src.io.dat_parser import read_dat, write_dat
from scripts import run_python_model, export_fortran_factors

LP_SUMMARY = Path("data/results_python/summary.dat")
FACTOR_RESULT = Path("data/results_python/fortran_factors.dat")
FACTOR_DEST = Path("fortran/fortran_factors.dat")
FACTOR_PATHHOME = Path("data/raw/fortran_factors.dat")
FORTRAN_EXE = Path("fortran/bin/powerworld").resolve()
BASE_RAW_DIR = Path("data/raw").resolve()
WORKSPACE_BASE = Path("data/tmp_workspaces")
RESULTS_DIR = Path("data/results_verification")
FORTRAN_LOG = RESULTS_DIR / "fortran_stdout.log"
FORTRAN_ERR = RESULTS_DIR / "fortran_stderr.log"
FORTRAN_OUT = RESULTS_DIR / "fortran_last_run.out"
HISTORY_FILE = RESULTS_DIR / "factor_history.log"
FACTOR_KEYS = [
    "FACONWIN",
    "FACOFFWIN",
    "FACUTILPV",
    "FACRESPV",
    "FACCOMPV",
    "CSPTURBFAC",
    "FACSHT",
]
ANNUAL_COST_PATTERN = re.compile(
    r"ANNUAL TOT ENERGY COST.*?=\s+([-0-9\.Ee+]+)\s+([-0-9\.Ee+]+)\s+([-0-9\.Ee+]+)"
)


def run_fortran():
    result = subprocess.run(
        [str(FORTRAN_EXE)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
    )
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    FORTRAN_LOG.write_text(result.stdout)
    FORTRAN_ERR.write_text(result.stderr)
    combined = result.stdout
    if result.stderr:
        combined += "\n----- STDERR -----\n" + result.stderr
    FORTRAN_OUT.write_text(combined)

    if result.returncode != 0:
        raise RuntimeError("Fortran run failed:\n{}".format(result.stderr))
    return result.stdout


def write_factor_files(factors):
    write_dat(factors, FACTOR_RESULT)
    write_dat(factors, FACTOR_DEST)
    write_dat(factors, FACTOR_PATHHOME)


def check_feasibility(fortran_output):
    text = fortran_output.upper()
    if "REMAINING INFLEX LOAD" in text or "EXCESIN)>0" in text:
        return False
    if "UNMET" in text or "UNSERVED" in text:
        return False
    return True

def log_candidate(factors, feasible, cost, label="candidate"):
    lines = [
        "LABEL: {}".format(label),
        "FEASIBLE: {}".format(feasible),
        "COST_MN_BIL_PER_YEAR: {:.6f}".format(cost),
    ]
    lines.extend(
        "{} = {:.10f}".format(k, float(factors.get(k, 0.0))) for k in FACTOR_KEYS
    )
    lines.append("")
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    with HISTORY_FILE.open("a") as handle:
        handle.write("\n".join(lines))


def parse_cost(stdout):
    match = ANNUAL_COST_PATTERN.search(stdout)
    if not match:
        return float("inf")
    try:
        return float(match.group(2))
    except (ValueError, IndexError):
        return float("inf")


def evaluate_factors(factors, label="candidate"):
    write_factor_files(factors)
    stdout = run_fortran()
    feasible = check_feasibility(stdout)
    cost = parse_cost(stdout)
    log_candidate(factors, feasible, cost, label)
    print("--- Fortran output tail ({}) ---".format(label))
    lines = stdout.strip().splitlines()
    tail = "\n".join(lines[-20:]) if lines else ""
    print(tail)
    print("----------------------------------------")
    return feasible, cost, stdout


def inflate_factors(factors, step):
    inflated = {}
    for key, value in factors.items():
        if value > 0:
            inflated[key] = value * (1.0 + step)
        else:
            inflated[key] = max(step, 0.05)
    return inflated


def raise_subunity_factors(factors, step):
    updated = factors.copy()
    changed = False
    for key, value in updated.items():
        if value <= 0:
            updated[key] = max(step, 0.05)
            changed = True
        elif value < 1.0:
            updated[key] = min(1.0, value * (1.0 + step))
            changed = True
    return updated, changed


def inflate_until_feasible(base_factors, initial_step=0.1, growth=1.5, max_attempts=25):
    candidate = base_factors.copy()
    step = initial_step

    # Phase 1: raise sub-unity factors toward 1.0
    for attempt in range(1, max_attempts + 1):
        candidate, changed = raise_subunity_factors(candidate, step)
        label = "inflate-subunity{}".format(attempt)
        feasible, cost, _ = evaluate_factors(candidate, label=label)
        if feasible:
            return candidate, cost
        if not changed:
            break

    # Phase 2: expand all factors once everything is >= 1
    candidate = {k: max(1.0, v) for k, v in candidate.items()}
    for attempt in range(1, max_attempts + 1):
        candidate = inflate_factors(candidate, step)
        label = "inflate-step{}".format(attempt)
        feasible, cost, _ = evaluate_factors(candidate, label=label)
        if feasible:
            return candidate, cost
        step *= growth

    raise RuntimeError("Unable to inflate factors to achieve feasibility.")


def prepare_workspace():
    WORKSPACE_BASE.mkdir(parents=True, exist_ok=True)
    workspace_path = Path(
        tempfile.mkdtemp(prefix="loadmatch_run_", dir=str(WORKSPACE_BASE))
    )
    data_raw = workspace_path / "data" / "raw"
    data_raw.mkdir(parents=True, exist_ok=True)
    for item in BASE_RAW_DIR.iterdir():
        if item.name == "fortran_factors.dat":
            continue
        target = data_raw / item.name
        if target.exists():
            continue
        if item.is_dir():
            os.symlink(str(item), str(target), target_is_directory=True)
        else:
            os.symlink(str(item), str(target))
    return workspace_path, data_raw


def run_fortran_worker(label, factors):
    workspace, data_raw = prepare_workspace()
    try:
        write_dat(factors, data_raw / "fortran_factors.dat")
        result = subprocess.run(
            [str(FORTRAN_EXE)],
            cwd=str(workspace),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
        )
        combined = result.stdout
        if result.stderr:
            combined += "\n----- STDERR -----\n" + result.stderr
        if result.returncode != 0:
            raise RuntimeError("Fortran run failed:\n{}".format(result.stderr))
        return label, factors, combined
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def hooke_jeeves_search(
    feasible_factors,
    feasible_cost,
    initial_step=0.1,
    shrink=0.5,
    max_iter=20,
    parallel_evals=1,
):
    step = initial_step
    candidate = feasible_factors.copy()
    best_cost = feasible_cost
    best_factors = feasible_factors.copy()

    for iteration in range(max_iter):
        print("Hooke-Jeeves iteration {} step {:.4f}".format(iteration + 1, step))
        improved = False
        trial_specs = []
        for key in FACTOR_KEYS:
            current = candidate.get(key, 0.0)
            deltas = []
            if current > 0:
                deltas.append(-current * step)
            deltas.append(current * step if current > 0 else step)

            for delta in deltas:
                trial_value = current + delta
                if trial_value <= 0:
                    continue
                trial = candidate.copy()
                trial[key] = trial_value
                label = "HJ-iter{}-{}-{}".format(
                    iteration + 1, key, "inc" if delta > 0 else "dec"
                )
                trial_specs.append(
                    {"key": key, "label": label, "factors": trial}
                )

        if not trial_specs:
            break

        if parallel_evals > 1:
            evaluation_results = evaluate_trials_parallel(
                trial_specs, parallel_evals
            )
        else:
            evaluation_results = evaluate_trials_sequential(trial_specs)

        for spec, result in zip(trial_specs, evaluation_results):
            feasible = result["feasible"]
            cost = result["cost"]
            trial = spec["factors"]
            if feasible and cost < best_cost:
                candidate = trial
                best_cost = cost
                best_factors = trial.copy()
                improved = True
                print(
                    "  Improved {} -> {:.6f}, cost {:.3f}".format(
                        spec["key"], trial[spec["key"]], cost
                    )
                )
                break

        if not improved:
            step *= shrink
            if step < 1e-4:
                break
    return best_factors, best_cost


def evaluate_trials_sequential(specs):
    results = []
    for spec in specs:
        feasible, cost, _ = evaluate_factors(spec["factors"], label=spec["label"])
        results.append({"feasible": feasible, "cost": cost})
    return results


def evaluate_trials_parallel(specs, max_workers):
    results = []
    with ProcessPoolExecutor(max_workers=max_workers) as pool:
        futures = [
            pool.submit(run_fortran_worker, spec["label"], spec["factors"])
            for spec in specs
        ]
        for spec, future in zip(specs, futures):
            label, factors, output = future.result()
            feasible = check_feasibility(output)
            cost = parse_cost(output)
            log_candidate(factors, feasible, cost, label)
            lines = output.strip().splitlines()
            tail = "\n".join(lines[-20:]) if lines else ""
            print("--- Fortran output tail ({}) ---".format(label))
            print(tail)
            print("----------------------------------------")
            results.append({"feasible": feasible, "cost": cost})
    return results


def run_workflow(region="UNITED-STATES", parallel_evals=1):
    run_python_model.main()
    export_fortran_factors.main([])
    base_factors = read_dat(str(FACTOR_RESULT))
    write_factor_files(base_factors)
    stdout = run_fortran()
    print("Fortran output written to {}".format(FORTRAN_OUT))
    feasible_initial = check_feasibility(stdout)
    initial_cost = parse_cost(stdout)
    log_candidate(base_factors, feasible_initial, initial_cost, label="LP")
    if feasible_initial:
        print(
            "Fortran verification succeeded with LP factors (cost {:.3f}).".format(
                initial_cost
            )
        )
        return

    print("LP factors infeasible; inflating to obtain a feasible starting point.")
    candidate, cost = inflate_until_feasible(base_factors)

    print(
        "Hooke-Jeeves search starting from feasible point with cost {:.3f}.".format(
            cost
        )
    )
    hooke_factors, best_cost = hooke_jeeves_search(
        candidate, cost, parallel_evals=parallel_evals
    )
    print(
        "Hooke-Jeeves search produced feasible factors with cost {:.3f}.".format(
            best_cost
        )
    )
    write_dat(hooke_factors, RESULTS_DIR / "hooke_jeeves_factors.dat")
    write_factor_files(hooke_factors)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Run optimisation + Fortran verification with optional parallel Hooke-Jeeves."
    )
    parser.add_argument(
        "--parallel-evals",
        type=int,
        default=1,
        help="Number of simultaneous Fortran evaluations per Hooke-Jeeves iteration (default: 1).",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    run_workflow(parallel_evals=max(1, args.parallel_evals))


if __name__ == "__main__":
    try:
        multiprocessing.set_start_method("spawn")
    except RuntimeError:
        pass
    main()

import re
import shutil
import subprocess
from pathlib import Path

from src.io.dat_parser import read_dat, write_dat
from scripts import run_python_model, export_fortran_factors

LP_SUMMARY = Path("data/results_python/summary.dat")
FACTOR_RESULT = Path("data/results_python/fortran_factors.dat")
FACTOR_DEST = Path("fortran/fortran_factors.dat")
FACTOR_PATHHOME = Path("data/raw/fortran_factors.dat")
FORTRAN_EXE = Path("fortran/bin/powerworld")
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


def copy_factor_file():
    FACTOR_DEST.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(str(FACTOR_RESULT), str(FACTOR_DEST))
    FACTOR_PATHHOME.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(str(FACTOR_RESULT), str(FACTOR_PATHHOME))


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
    write_dat(factors, FACTOR_RESULT)
    copy_factor_file()
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


def inflate_until_feasible(base_factors, initial_step=0.1, growth=1.5, max_attempts=25):
    candidate = base_factors.copy()
    step = initial_step
    for attempt in range(1, max_attempts + 1):
        candidate = inflate_factors(candidate, step)
        label = "inflate-step{}".format(attempt)
        feasible, cost, _ = evaluate_factors(candidate, label=label)
        if feasible:
            return candidate, cost
        step *= growth
    raise RuntimeError("Unable to inflate factors to achieve feasibility.")


def hooke_jeeves_search(
    feasible_factors, feasible_cost, initial_step=0.1, shrink=0.5, max_iter=20
):
    step = initial_step
    candidate = feasible_factors.copy()
    best_cost = feasible_cost
    best_factors = feasible_factors.copy()

    for iteration in range(max_iter):
        print("Hooke-Jeeves iteration {} step {:.4f}".format(iteration + 1, step))
        improved = False
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
                label = "HJ-iter{}-{}".format(iteration + 1, key)
                feasible, cost, _ = evaluate_factors(trial, label=label)
                if feasible and cost < best_cost:
                    candidate = trial
                    best_cost = cost
                    best_factors = trial.copy()
                    improved = True
                    print(
                        "  Improved {} -> {:.6f}, cost {:.3f}".format(
                            key, trial_value, cost
                        )
                    )
                    break
            if improved:
                break
        if not improved:
            step *= shrink
            if step < 1e-4:
                break
    return best_factors, best_cost


def run_workflow(region="UNITED-STATES"):
    run_python_model.main()
    export_fortran_factors.main()
    copy_factor_file()
    stdout = run_fortran()
    print("Fortran output written to {}".format(FORTRAN_OUT))
    base_factors = read_dat(str(FACTOR_RESULT))
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
    hooke_factors, best_cost = hooke_jeeves_search(candidate, cost)
    print(
        "Hooke-Jeeves search produced feasible factors with cost {:.3f}.".format(
            best_cost
        )
    )
    write_dat(hooke_factors, RESULTS_DIR / "hooke_jeeves_factors.dat")
    write_dat(hooke_factors, FACTOR_RESULT)
    copy_factor_file()


def main():
    run_workflow()


if __name__ == "__main__":
    main()

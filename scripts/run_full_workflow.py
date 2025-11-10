import shutil
import subprocess
from pathlib import Path

from src.io.dat_parser import read_dat, write_dat
from scripts import run_python_model, export_fortran_factors

LP_SUMMARY = Path("data/results_python/summary.dat")
FACTOR_RESULT = Path("data/results_python/fortran_factors.dat")
FACTOR_DEST = Path("fortran/fortran_factors.dat")
FORTRAN_EXE = Path("fortran/bin/powerworld")
RESULTS_DIR = Path("data/results_verification")
FORTRAN_LOG = RESULTS_DIR / "fortran_stdout.log"
FORTRAN_ERR = RESULTS_DIR / "fortran_stderr.log"
FORTRAN_OUT = RESULTS_DIR / "fortran_last_run.out"
FACTOR_KEYS = [
    "FACONWIN",
    "FACOFFWIN",
    "FACUTILPV",
    "FACRESPV",
    "FACCOMPV",
    "CSPTURBFAC",
    "FACSHT",
]


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


def check_feasibility(fortran_output):
    text = fortran_output.upper()
    if "REMAINING INFLEX LOAD" in text or "EXCESIN)>0" in text:
        return False
    if "UNMET" in text or "UNSERVED" in text:
        return False
    return True

def log_candidate(factors, path=None):
    if path is None:
        path = RESULTS_DIR / "factors_history.txt"
    lines = ["{} = {:.10f}".format(k, factors.get(k, 0.0)) for k in FACTOR_KEYS]
    lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        handle.write("\n".join(lines))


def evaluate_factors(factors):
    log_candidate(factors)
    write_dat(factors, FACTOR_RESULT)
    copy_factor_file()
    stdout = run_fortran()
    feasible = check_feasibility(stdout)
    return feasible, stdout


def inflate_factors(factors, step):
    inflated = {}
    for key, value in factors.items():
        if value > 0:
            inflated[key] = value * (1.0 + step)
        else:
            inflated[key] = max(step, 0.05)
    return inflated


def hooke_jeeves_search(base_factors, initial_step=0.1, shrink=0.5, max_iter=10):
    step = initial_step
    candidate = base_factors.copy()
    for iteration in range(max_iter):
        print(
            "Hooke-Jeeves iteration {} with step {:.4f}".format(iteration + 1, step)
        )
        feasible, stdout = evaluate_factors(candidate)
        if feasible:
            return candidate, stdout

        improved = False
        for key in FACTOR_KEYS:
            current = candidate.get(key, 0.0)
            if current <= 0:
                delta = max(step, 0.05)
            else:
                delta = current * step
            trial = candidate.copy()
            trial[key] = current + delta
            print(
                "  Testing {} -> {:.6f}".format(
                    key, trial[key]
                )
            )
            feasible, stdout = evaluate_factors(trial)
            if feasible:
                candidate = trial
                improved = True
                print("  Found feasible update for {}".format(key))
                break
        if not improved:
            step *= shrink
            if step < 1e-4:
                break

    raise RuntimeError("Hooke-Jeeves search failed to find a feasible factor set.")


def run_workflow(region="UNITED-STATES"):
    run_python_model.main()
    export_fortran_factors.main()
    copy_factor_file()
    stdout = run_fortran()
    print("Fortran output written to {}".format(FORTRAN_OUT))
    if check_feasibility(stdout):
        print("Fortran verification succeeded with LP factors.")
        return

    base_factors = read_dat(str(FACTOR_RESULT))
    candidate = inflate_factors(base_factors, 0.5)
    print("LP factors infeasible; starting Hooke-Jeeves search.")
    hooke_factors, _ = hooke_jeeves_search(candidate)
    print("Hooke-Jeeves search produced a feasible factor set.")
    write_dat(hooke_factors, RESULTS_DIR / "hooke_jeeves_factors.dat")


def main():
    run_workflow()


if __name__ == "__main__":
    main()

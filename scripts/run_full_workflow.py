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
    return "UNMET" not in text and "UNSERVED" not in text


def evaluate_factors(factors):
    write_dat(factors, FACTOR_RESULT)
    copy_factor_file()
    stdout = run_fortran()
    feasible = check_feasibility(stdout)
    return feasible, stdout


def inflate_factors(factors, step):
    return {k: max(v * (1.0 + step), 0.0) for k, v in factors.items()}


def hooke_jeeves_search(base_factors, initial_step=0.1, shrink=0.5, max_iter=10):
    step = initial_step
    candidate = base_factors.copy()
    feasible, stdout = evaluate_factors(candidate)
    if feasible:
        return candidate, stdout

    while step > 1e-4:
        improved = False
        trial = inflate_factors(candidate, step)
        feasible, stdout = evaluate_factors(trial)
        if feasible:
            candidate = trial
            improved = True
            break
        step *= shrink
    if not improved:
        raise RuntimeError(
            "Hooke-Jeeves search failed to find a feasible factor set."
        )
    return candidate, stdout


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
    inflated = inflate_factors(base_factors, 0.1)
    print("LP factors infeasible; starting Hooke-Jeeves search.")
    hooke_jeeves_search(inflated)
    print("Hooke-Jeeves search produced a feasible factor set.")


def main():
    run_workflow()


if __name__ == "__main__":
    main()

"""LOADMATCH-O driver: genetic-algorithm optimization of the LOADMATCH design
parameters for one region.

Each candidate is evaluated by a full LOADMATCH run (fortran/bin/powerworld).
Candidate values reach the binary through data/raw/fortran_factors.dat, which
the READ_FACTOR_OVERRIDES subroutine appended to powerworld.f reads after the
region defaults are set.  Parallel evaluations run in isolated workspaces under
data/tmp_workspaces/.

Outputs go to data/results_verification/<REGION><SUFFIX>/ (summaries, raw
Fortran reports, factor_history.log, genetic_factors.dat) and the optimized xx
report to data/results_verification/xx_optimized<SUFFIX>/xx.<SHORTCODE>.
The suffix isolates alternative runs (_scratch2, _dc1, _dc2rc, ...).

Run from the repo root:
    python -m scripts.run_full_workflow --region EUROPE --baseline-start defaults ...

Set LOADMATCH_FORTRAN_EXE to use a binary other than fortran/bin/powerworld.
"""
import argparse
import contextlib
import fcntl
import multiprocessing
import os
import random
import re
import shutil
import subprocess
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

from src.dat_parser import read_dat, write_dat
from src.regions import REGION_SHORTCODE
from src.xx_tools import strip_override_echo
from scripts.parse_fortran_output import (
    check_feasibility,
    parse_and_save,
    parse_annual_cost,
    parse_land_area,
    save_summary,
)

FORTRAN_EXE = Path(os.environ.get("LOADMATCH_FORTRAN_EXE",
                                  "fortran/bin/powerworld")).resolve()
BASE_RAW_DIR = Path("data/raw").resolve()
RESULTS_ROOT = Path("data/results_verification")
WORKSPACE_BASE = Path("data/tmp_workspaces")
_FORTRAN_SRC = Path("fortran/src/powerworld.f")

# Wall-clock ceilings for Fortran subprocesses.  A model evaluation takes
# minutes; one that exceeds FORTRAN_EVAL_TIMEOUT_S is hung and is killed (and
# counted as infeasible) so it cannot stall the GA until the job walltime.
# Supply preprocessing (IFREWRITE=1,2) is legitimately much slower.
FORTRAN_EVAL_TIMEOUT_S = 3600
FORTRAN_PREP_TIMEOUT_S = 6 * 3600

# Data-center scenario (Fortran IFDATCEN): 0 = base WWS, 1 = EGS-powered data
# centers, 2 = WWS-powered data centers.  Passed to the binary as argument 3.
_IFDATCEN = 0

# Land-use cap: new wind spacing + footprint as percent of regional land area
# (from the xx LANDNEWTECH table).  None = no cap.  Enforced as a graded cost
# penalty rather than hard infeasibility, so a starting point above the cap
# still has a selection gradient back into compliance: the effective
# (selection) cost is multiplied by (1 + _LAND_PENALTY_PER_PP * excess_pp).
# The penalty is soft: if no compliant feasible solution is found, the least
# penalized feasible one is returned and can exceed the cap.
_MAX_LAND_PCT = None
_LAND_PENALTY_PER_PP = 5.0

# Results-isolation suffix for the current run ("", "_scratch2", "_dc1", ...),
# appended to the results dir and the xx_optimized dir.
_RUN_SUFFIX = ""

# Serializes direct (non-workspace) Fortran runs, which share
# data/raw/fortran_factors.dat, across concurrent jobs.
_FORTRAN_LOCK_PATH = Path("data/.fortran_run.lock")


def _apply_land_cap(cost, stdout):
    """Effective (selection) cost after the land-use penalty.  Reported costs
    in all summaries stay the raw parsed values."""
    if _MAX_LAND_PCT is None or cost == float("inf"):
        return cost
    land = parse_land_area(stdout).get("new_land_pct_regland")
    if land is None or land <= _MAX_LAND_PCT:
        return cost
    return cost * (1.0 + _LAND_PENALTY_PER_PP * (land - _MAX_LAND_PCT))


def _xx_report_path(region):
    """Destination of the optimized xx report.  Kept out of data/raw/ so the
    reference xx.<SHORTCODE> files there are never overwritten."""
    dest_dir = RESULTS_ROOT / ("xx_optimized" + _RUN_SUFFIX)
    dest_dir.mkdir(parents=True, exist_ok=True)
    return dest_dir / "xx.{}".format(REGION_SHORTCODE.get(region, region))


@contextlib.contextmanager
def _fortran_global_lock():
    """Exclusive file lock around write-factor-file + run-Fortran."""
    _FORTRAN_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_FORTRAN_LOCK_PATH, "w") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


def _region_paths(results_label: str):
    """Output paths for one results directory (<REGION><SUFFIX>)."""
    results_dir = RESULTS_ROOT / results_label
    return dict(
        factor_file=BASE_RAW_DIR / "fortran_factors.dat",
        results_dir=results_dir,
        fortran_log=results_dir / "fortran_stdout.log",
        fortran_err=results_dir / "fortran_stderr.log",
        fortran_out=results_dir / "fortran_last_run.out",
        fortran_baseline_out=results_dir / "fortran_baseline_run.out",
        fortran_optimal_out=results_dir / "fortran_optimal_run.out",
        history_file=results_dir / "factor_history.log",
        baseline_summary=results_dir / "baseline_summary.json",
        optimal_summary=results_dir / "optimal_summary.json",
    )


# ---------------------------------------------------------------------------
# Parameter registry: every Fortran parameter the override file can set.
#
#   KEY -> (default, category, description)
#
# Categories of the design variables (mutated by the GA):
#   "capacity"  dimensionless capacity scaling factor
#   "tw"        storage charge/discharge power (TW)
#   "hours"     storage duration (hours)
#   "days"      storage duration (days)
# "fixed" parameters are not design variables and are never written to the
# factor file, so the Fortran keeps its own (region-specific) values.
# ---------------------------------------------------------------------------
PARAM_REGISTRY: Dict[str, Tuple[float, str, str]] = {
    # --- Generation capacity ---
    "FACONWIN":    (1.0,          "capacity",  "Onshore wind capacity scaling"),
    "FACOFFWIN":   (1.0,          "capacity",  "Offshore wind capacity scaling"),
    "FACUTILPV":   (1.0,          "capacity",  "Utility-scale PV capacity scaling"),
    "FACRESPV":    (1.0,          "capacity",  "Residential rooftop PV scaling"),
    "FACCOMPV":    (1.0,          "capacity",  "Commercial rooftop PV scaling"),
    # CSP is not treated as a growth technology: its turbine capacity stays at
    # the model baseline.
    "CSPTURBFAC":  (1.0,          "fixed",     "CSP turbine capacity ratio"),
    "FACSHT":      (1.0,          "capacity",  "Solar thermal heat scaling"),
    # --- CSP / storage configuration ---
    "CSPSTORGAT":  (2.61244594,   "fixed",     "CSP storage charge/discharge ratio"),
    "HCHARCSP":    (14.0,         "fixed",     "CSP max charge hours"),
    "BATDISCH":    (1.55,         "tw",        "Battery max discharge rate (TW)"),
    "STORHBAT":    (4.0,          "hours",     "Battery storage duration hours"),
    "STORHCOLD":   (14.0,         "hours",     "Cold storage hours (PCM-ice + CW-STES)"),
    "STORHHWAT":   (14.0,         "hours",     "Hot-water STES hours"),
    "STORHPHS":    (14.0,         "hours",     "Pumped hydro storage hours"),
    # --- UTES and hydrogen storage ---
    "UGFAC":       (3.0,          "fixed",     "UTES charge rate factor"),
    "STORUGDYS":   (60.0,         "days",      "UTES seasonal heat storage days"),
    "DAYH2STOR":   (40.0,         "days",      "H2 storage days"),
    # --- Hydropower (region-specific in powerworld.f) ---
    "HPTURBRAT":   (10.0,         "fixed",     "Hydro turbine discharge ratio"),
    "DAMCAPRAT":   (0.583,        "fixed",     "Hydro dam capacity / annual output"),
    "DAYBASHYD":   (360.0,        "fixed",     "Baseload hydro storage days"),
    # --- Demand response (region-specific in powerworld.f) ---
    "MXHRDRM":     (11.0,         "fixed",     "Max demand-response shift hours"),
    # --- Thermal storage and demand response ---
    "COOLSTES":    (0.4,          "fixed",     "Fraction AC from CW-STES vs ice"),
    "PHSMIN":      (0.016,        "fixed",     "Min PHS nameplate capacity (TW)"),
    "FHEATFLX":    (0.15,         "fixed",     "Flexible heat load fraction"),
    "FCOLDFLX":    (0.15,         "fixed",     "Flexible cold load fraction"),
    "FRSTORINIT":  (0.5,          "fixed",     "Initial storage fill fraction"),
    "FDISTHEAT":   (0.2,          "fixed",     "District heating fraction"),
    # --- Heat pump and health ---
    # The heat-pump COP feeds the exogenous heat/electricity demand split
    # (FISHEAT = FHTBUILD/CPERFORM), so it is a fixed physical constant, not a
    # design variable: every run keeps the same demand structure.
    "CPERFORM":    (4.0,          "fixed",     "Heat pump COP (kWh-th/kWh-el)"),
    "HCDDADD":     (1.0,          "fixed",     "HDD/CDD daily minimum (numerical safeguard)"),
    "FMORTBAU":    (0.9,          "fixed",     "BAU air-pollution mortality fraction"),
    # --- Hot-water, H2, heat battery ---
    "HWFAC":       (1.0,          "fixed",     "HW-STES charge rate factor"),
    "FCDISCH":     (0.091,        "tw",        "H2 fuel-cell discharge rate (TW)"),
    "FCCHARG":     (0.091,        "tw",        "H2 electrolyser charge rate (TW)"),
    # Inert at runtime: STORHHFC is only read when IMERGH2=2 (the model runs
    # IMERGH2=1), and HBTDISCH (hence STORHHBT) is overwritten from the
    # industrial high-temperature heat demand.
    "STORHHFC":    (0.0,          "fixed",     "H2 elec storage hours (inert, IMERGH2=1)"),
    "HBTDISCH":    (0.0,          "fixed",     "Heat battery discharge rate (inert)"),
    "STORHHBT":    (15.0,         "fixed",     "Heat battery storage hours (inert)"),
    # --- Industrial heat flexibility ---
    "FRCIHFLEX":   (0.5,          "fixed",     "Flexible industrial heat fraction"),
}

FACTOR_KEYS: List[str] = [k for k, (_, cat, _) in PARAM_REGISTRY.items() if cat != "fixed"]

CAPACITY_FACTOR_KEYS = [k for k in FACTOR_KEYS if PARAM_REGISTRY[k][1] == "capacity"]
# Electric generation only: scaled first when bootstrapping feasibility, since
# electric shortfalls are far more common than heat shortfalls.
ELECTRIC_CAPACITY_FACTOR_KEYS = [k for k in CAPACITY_FACTOR_KEYS if k != "FACSHT"]
_STORAGE_CATS = ("tw", "hours", "days")
_STORAGE_RATE_KEYS = tuple(k for k in FACTOR_KEYS if PARAM_REGISTRY[k][1] == "tw")
_WIND_KEYS = ("FACONWIN", "FACOFFWIN")

# Relative mutation scale per category (multiplies --ga-mutation-scale).
_CATEGORY_SCALES = {"capacity": 1.0, "tw": 0.3, "hours": 0.3, "days": 0.3}
# Mutations are damped by 1 / max(1, |value|)**_MAGNITUDE_DAMPING.
_MAGNITUDE_DAMPING = 0.5
# Capacity floor used only when the bootstrap inflates capacity upward.  Design
# variables themselves are bounded below by zero (technology excluded).
_INFLATE_FLOOR = 0.05


def _clamp(key: str, value: float) -> float:
    """Design variables are bounded below by zero."""
    if PARAM_REGISTRY.get(key.upper(), (0, "capacity", ""))[1] == "fixed":
        return value
    return max(0.0, value)


# ---------------------------------------------------------------------------
# Starting points
# ---------------------------------------------------------------------------

def _parse_hardcoded_int(text: str, name: str) -> int:
    """Value of an active (uncommented) integer assignment in powerworld.f."""
    m = re.search(
        r"^(?!C)\s+" + re.escape(name) + r"\s*=\s*(\d+)",
        text,
        re.MULTILINE | re.IGNORECASE,
    )
    return int(m.group(1)) if m else 0


def extract_fortran_region_defaults(region: str) -> Dict[str, float]:
    """Design-variable values hardcoded in powerworld.f for a region, i.e. the
    expert trial-and-error solution.

    Evaluates the IF/ELSEIF/ELSE/ENDIF blocks conditioned on IMERGH2 and IFEGS
    (both hardcoded in powerworld.f), so only the branch that executes at
    runtime contributes.  Blocks conditioned on anything else (e.g. FRCLDEGS,
    IFNEWLOAD) are skipped, so they cannot overwrite values set by an enclosing
    recognized branch.  Keys not found fall back to the PARAM_REGISTRY default.
    """
    text = _FORTRAN_SRC.read_text()
    ctrl_val = {"IMERGH2": _parse_hardcoded_int(text, "IMERGH2"),
                "IFEGS": _parse_hardcoded_int(text, "IFEGS")}

    block_re = re.compile(
        r"GRIDUSE\.EQ\.'{}'\s*\)(.*?)"
        r"(?=ELSEIF\s*\(GRIDUSE\.EQ\.|C\s+ENDIF\s+GRIDUSE)".format(re.escape(region)),
        re.DOTALL | re.IGNORECASE | re.MULTILINE,
    )
    m = block_re.search(text)
    if not m:
        print("  [WARN] No region block found for '{}' in powerworld.f; "
              "using PARAM_REGISTRY defaults.".format(region))
        return {k: PARAM_REGISTRY[k][0] for k in FACTOR_KEYS}

    cond = r"(IMERGH2|IFEGS)\s*\.(EQ|NE)\.\s*(\d+)"
    if_re = re.compile(r"IF\s*\(\s*" + cond + r"\s*\)\s*THEN", re.IGNORECASE)
    elif_re = re.compile(r"ELSEIF\s*\(\s*" + cond + r"\s*\)\s*THEN", re.IGNORECASE)
    if_or_re = re.compile(r"IF\s*\(\s*" + cond + r"\s*\.OR\.\s*" + cond + r"\s*\)\s*THEN",
                          re.IGNORECASE)
    elif_or_re = re.compile(r"ELSEIF\s*\(\s*" + cond + r"\s*\.OR\.\s*" + cond
                            + r"\s*\)\s*THEN", re.IGNORECASE)
    any_if_re = re.compile(r"^IF\s*\(.*\)\s*THEN\b", re.IGNORECASE)
    any_elif_re = re.compile(r"^ELSEIF\s*\(.*\)\s*THEN\b", re.IGNORECASE)
    else_re = re.compile(r"^ELSE\b", re.IGNORECASE)
    endif_re = re.compile(r"^ENDIF\b", re.IGNORECASE)
    assign_re = re.compile(
        r"^\s+([A-Za-z]\w*)\s*=\s*([-+]?(?:\d+\.?\d*|\d*\.\d+)(?:[Ee][-+]?\d+)?)\s*$")

    def _holds(groups):
        """Truth value of one or more (VAR, OP, N) triples joined by .OR."""
        out = False
        for i in range(0, len(groups), 3):
            var, op, val = groups[i].upper(), groups[i + 1].upper(), int(groups[i + 2])
            out = out or ((ctrl_val[var] == val) if op == "EQ" else (ctrl_val[var] != val))
        return out

    # cond_stack frames: [branch_active, any_branch_in_chain_matched]
    cond_stack: list = []
    opaque_depth = 0     # nesting depth of skipped (unrecognized) IF blocks
    result: Dict[str, float] = {}
    factor_set = set(FACTOR_KEYS)

    for raw_line in m.group(1).splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.upper().startswith("C"):
            continue
        su = stripped.upper()
        outer_ok = all(frame[0] for frame in cond_stack[:-1])

        mif = if_or_re.match(su) or if_re.match(su)
        if mif and opaque_depth == 0:
            ok = all(frame[0] for frame in cond_stack) and _holds(mif.groups())
            cond_stack.append([ok, ok])
            continue
        melif = elif_or_re.match(su) or elif_re.match(su)
        if melif and cond_stack and opaque_depth == 0:
            ok = outer_ok and not cond_stack[-1][1] and _holds(melif.groups())
            cond_stack[-1][1] = cond_stack[-1][1] or ok
            cond_stack[-1][0] = ok
            continue
        if any_if_re.match(su):
            opaque_depth += 1
            continue
        if any_elif_re.match(su):
            continue
        if else_re.match(su) and opaque_depth == 0 and cond_stack:
            cond_stack[-1][0] = outer_ok and not cond_stack[-1][1]
            continue
        if endif_re.match(su):
            if opaque_depth > 0:
                opaque_depth -= 1
            elif cond_stack:
                cond_stack.pop()
            continue
        if opaque_depth == 0 and all(frame[0] for frame in cond_stack):
            ma = assign_re.match(raw_line)
            if ma and ma.group(1).upper() in factor_set:
                result[ma.group(1).upper()] = float(ma.group(2))

    for k in FACTOR_KEYS:
        result.setdefault(k, PARAM_REGISTRY[k][0])
    found = [k for k in FACTOR_KEYS if result[k] != PARAM_REGISTRY[k][0]]
    print("Extracted {} region-specific defaults from powerworld.f for '{}' "
          "(keys differ from registry: {}).".format(len(found), region, found or "none"))
    return result


def load_baseline_start(path: Path) -> Dict[str, float]:
    """Design-variable dict from a KEY = VALUE factor file (e.g. a saved
    genetic_factors.dat).  Missing design variables take their registry
    default; fixed parameters in the file are ignored, so the Fortran keeps
    its own values for them."""
    if "POWERWORLD.F LOADMATCH" in path.read_text(errors="replace")[:2000]:
        raise ValueError("{} is an xx report, not a factor file; seed from the "
                         "powerworld.f region values with --baseline-start defaults."
                         .format(path))
    values = {k.upper(): float(v) for k, v in read_dat(str(path)).items()}
    parsed = {k: v for k, v in values.items() if k in FACTOR_KEYS}
    if not parsed:
        raise ValueError("No design variables found in {}".format(path))
    full = {k: PARAM_REGISTRY[k][0] for k in FACTOR_KEYS}
    full.update(parsed)

    print(f"Loaded {len(parsed)} baseline factors from {path}")
    for k, v in sorted(parsed.items()):
        default = PARAM_REGISTRY[k][0]
        marker = "" if abs(v - default) < 1e-9 else " *"
        print(f"  {k:12s} = {v:.6f}  (default {default:.6f}){marker}")
    return full


def build_scratch_start() -> Dict[str, float]:
    """Spreadsheet starting point: all capacity factors = 1.0 and all storage
    design variables = 0, the state that manual LOADMATCH tuning starts from."""
    factors = {}
    for key in FACTOR_KEYS:
        factors[key] = 1.0 if PARAM_REGISTRY[key][1] == "capacity" else 0.0
    return factors


# ---------------------------------------------------------------------------
# Fortran I/O
# ---------------------------------------------------------------------------

def preprocess_region(region):
    """Make sure the region's supply file is preprocessed.

    Prerequisites in data/raw/: wwssupworld.<REGION> (aggregated supply) or
    wwssupworld.dat (raw GATOR-GCMOM supply, reformatted and aggregated here
    with IFREWRITE=1 and 2).  The first normal run (IFREWRITE=3) then creates
    wwsmonthly/wwshourly/pkflex.<REGION>.  No-op once wwsmonthly.<REGION>
    exists.
    """
    monthly_file = BASE_RAW_DIR / "wwsmonthly.{}".format(region)
    supply_agg = BASE_RAW_DIR / "wwssupworld.{}".format(region)
    supply_raw = BASE_RAW_DIR / "wwssupworld.dat"

    if monthly_file.exists() or supply_agg.exists():
        return
    if not supply_raw.exists():
        raise FileNotFoundError(
            "Cannot preprocess region '{}': neither {} (aggregated) nor {} (raw "
            "supply) exists.".format(region, supply_agg, supply_raw))
    for step, what in (("1", "reformat raw supply"), ("2", "aggregate by region")):
        print("Preprocessing {}: IFREWRITE={} ({}) ...".format(region, step, what))
        result = subprocess.run(
            [str(FORTRAN_EXE), region, step],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
            timeout=FORTRAN_PREP_TIMEOUT_S,
        )
        if result.returncode != 0:
            raise RuntimeError("IFREWRITE={} failed for {}:\n{}".format(
                step, region, result.stderr))


def _fortran_region_args(region):
    """Command-line arguments for the binary: arg1 = region (always passed; the
    binary's own default region is AFRICA-EAST); for data-center scenarios also
    arg2 = IFREWRITE (3, a normal run) and arg3 = IFDATCEN."""
    args = [region]
    if _IFDATCEN:
        args += ["3", str(_IFDATCEN)]
    return args


def _run_fortran_raw(cmd, paths):
    """Run the binary in the repo root, write the logs, return stdout."""
    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
        timeout=FORTRAN_EVAL_TIMEOUT_S,
    )
    paths["results_dir"].mkdir(parents=True, exist_ok=True)
    paths["fortran_log"].write_text(result.stdout)
    paths["fortran_err"].write_text(result.stderr)
    combined = result.stdout
    if result.stderr:
        combined += "\n----- STDERR -----\n" + result.stderr
    paths["fortran_out"].write_text(combined)
    if result.returncode != 0:
        raise RuntimeError("Fortran run failed:\n{}".format(result.stderr))
    return result.stdout


def run_fortran(region, paths):
    preprocess_region(region)
    return _run_fortran_raw([str(FORTRAN_EXE)] + _fortran_region_args(region), paths)


def write_factor_files(factors, paths):
    """Write the design variables to the factor file the binary reads.  Fixed
    parameters are omitted so the Fortran keeps its region-specific values
    (e.g. HPTURBRAT and MXHRDRM differ between the US and other regions)."""
    write_dat({k: v for k, v in factors.items() if k in FACTOR_KEYS},
              paths["factor_file"])


def prepare_workspace():
    """Isolated working directory for one parallel evaluation: data/raw/ inputs
    are symlinked, while the factor file and every file the model writes
    (wwsmonthly/wwshourly/pkflex, countrydata.out) stay private."""
    WORKSPACE_BASE.mkdir(parents=True, exist_ok=True)
    workspace_path = Path(
        tempfile.mkdtemp(prefix="loadmatch_run_", dir=str(WORKSPACE_BASE))
    )
    data_raw = workspace_path / "data" / "raw"
    data_raw.mkdir(parents=True, exist_ok=True)
    for item in BASE_RAW_DIR.iterdir():
        name = item.name
        if name == "fortran_factors.dat":
            continue
        if (name.startswith("wwsmonthly.")
                or name.startswith("wwshourly.")
                or name.startswith("pkflex.")
                or name == "countrydata.out"):
            continue
        target = data_raw / name
        if target.exists():
            continue
        os.symlink(str(item), str(target), target_is_directory=item.is_dir())
    return workspace_path, data_raw


def run_fortran_worker(label, factors, region):
    workspace, data_raw = prepare_workspace()
    try:
        write_dat(factors, data_raw / "fortran_factors.dat")
        result = subprocess.run(
            [str(FORTRAN_EXE)] + _fortran_region_args(region),
            cwd=str(workspace),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=FORTRAN_EVAL_TIMEOUT_S,
        )
        combined = result.stdout
        if result.stderr:
            combined += "\n----- STDERR -----\n" + result.stderr
        if result.returncode != 0:
            raise RuntimeError("Fortran run failed:\n{}".format(result.stderr))
        return label, factors, combined
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def run_single_isolated(label, factors, region, paths):
    """One evaluation in an isolated workspace (no global lock), writing the
    same logs as a direct run.  Needs explicit factors, so it cannot express
    the 'defaults' mode (which relies on the factor file being absent)."""
    preprocess_region(region)
    _, _, output = run_fortran_worker(label, factors, region)
    paths["results_dir"].mkdir(parents=True, exist_ok=True)
    paths["fortran_log"].write_text(output)
    paths["fortran_err"].write_text("")
    paths["fortran_out"].write_text(output)
    return output


def parse_cost(stdout):
    cost = parse_annual_cost(stdout)
    return float("inf") if cost is None else cost


def log_candidate(factors, feasible, cost, label, history_file):
    lines = [
        "LABEL: {}".format(label),
        "FEASIBLE: {}".format(feasible),
        "COST_MN_BIL_PER_YEAR: {:.6f}".format(cost),
    ]
    lines.extend(
        "{} = {:.10f}".format(k, float(factors.get(k, 0.0))) for k in FACTOR_KEYS
    )
    lines.append("")
    history_file.parent.mkdir(parents=True, exist_ok=True)
    with history_file.open("a") as handle:
        handle.write("\n".join(lines))


def _print_tail(label, output):
    lines = output.strip().splitlines()
    print("--- Fortran output tail ({}) ---".format(label))
    print("\n".join(lines[-20:]) if lines else "")
    print("----------------------------------------")


def evaluate_factors(factors, label, region, paths):
    """One direct (locked) evaluation; returns (feasible, cost, stdout)."""
    with _fortran_global_lock():
        write_factor_files(factors, paths)
        stdout = run_fortran(region, paths)
    feasible = check_feasibility(stdout)
    cost = _apply_land_cap(parse_cost(stdout), stdout)
    log_candidate(factors, feasible, cost, label, paths["history_file"])
    _print_tail(label, stdout)
    return feasible, cost, stdout


def evaluate_trials_sequential(specs, region, paths):
    results = []
    for spec in specs:
        feasible, cost, _ = evaluate_factors(spec["factors"], spec["label"], region, paths)
        results.append({"feasible": feasible, "cost": cost})
    return results


def _worker_init(ifdatcen):
    """Per-worker initializer: spawned workers re-import this module, so the
    data-center scenario must be re-established or they would run the base
    case."""
    global _IFDATCEN
    _IFDATCEN = ifdatcen


def evaluate_trials_parallel(specs, max_workers, region, paths):
    results = []
    with ProcessPoolExecutor(max_workers=max_workers,
                             initializer=_worker_init,
                             initargs=(_IFDATCEN,)) as pool:
        futures = [
            pool.submit(run_fortran_worker, spec["label"], spec["factors"], region)
            for spec in specs
        ]
        for spec, future in zip(specs, futures):
            try:
                label, factors, output = future.result()
            except Exception as exc:
                # A failed or timed-out evaluation (e.g. a transient shared-
                # filesystem error while preparing the workspace) counts as
                # infeasible, so one bad candidate cannot abort the whole GA.
                label = spec["label"]
                print("WARNING: evaluation '{}' failed: {}: {} -- treating as "
                      "infeasible (run continues).".format(
                          label, type(exc).__name__, exc))
                log_candidate(spec["factors"], False, float("inf"), label,
                              paths["history_file"])
                results.append({"feasible": False, "cost": float("inf")})
                continue
            feasible = check_feasibility(output)
            cost = _apply_land_cap(parse_cost(output), output)
            log_candidate(factors, feasible, cost, label, paths["history_file"])
            _print_tail(label, output)
            results.append({"feasible": feasible, "cost": cost})
    return results


def _eval_batch(specs, parallel_evals, region, paths):
    """Evaluate candidate specs in parallel isolated workspaces when
    parallel_evals > 1, otherwise one after another.  Returns
    [{feasible, cost}, ...] in specs order."""
    if parallel_evals > 1 and len(specs) > 1:
        return evaluate_trials_parallel(specs, min(parallel_evals, len(specs)),
                                        region, paths)
    return evaluate_trials_sequential(specs, region, paths)


# ---------------------------------------------------------------------------
# Feasibility bootstrap: find a first feasible point from an infeasible start
# ---------------------------------------------------------------------------

def inflate_factors(factors, step, locked=()):
    """Inflate the unlocked capacity factors by (1 + step)."""
    locked_lower = {k.lower() for k in locked}
    inflated = factors.copy()
    for key in CAPACITY_FACTOR_KEYS:
        if key.lower() in locked_lower:
            continue
        value = inflated.get(key, 1.0)
        if value > 0:
            inflated[key] = max(_INFLATE_FLOOR, value * (1.0 + step))
        else:
            inflated[key] = max(step, _INFLATE_FLOOR)
    return inflated


def raise_subunity_factors(factors, step, locked=()):
    """Raise the unlocked sub-unity capacity factors toward 1.0."""
    locked_lower = {k.lower() for k in locked}
    updated = factors.copy()
    changed = False
    for key in CAPACITY_FACTOR_KEYS:
        if key.lower() in locked_lower:
            continue
        value = updated.get(key, 1.0)
        if value <= 0:
            updated[key] = max(step, _INFLATE_FLOOR)
            changed = True
        elif value < 1.0:
            updated[key] = min(1.0, value * (1.0 + step))
            changed = True
    return updated, changed


def _inflation_candidates(base_factors, initial_step, growth, max_attempts,
                          locked, extra_keys):
    """The deterministic candidate schedule of the inflation bootstrap.

    Phase 1 raises sub-unity capacity factors toward 1.0; phase 2A scales the
    electric generation factors; phase 2B scales all capacity factors.  In both
    phase-2 stages the extra keys (free storage of a data-center case) grow
    with the same step.  The schedule does not depend on evaluation outcomes,
    so it can be evaluated in parallel batches."""
    locked_lower = {k.lower() for k in locked}

    def _scale_extra(cand, step_val):
        for key in extra_keys:
            val = cand.get(key, PARAM_REGISTRY[key][0])
            if val > 0:
                cand[key] = _clamp(key, val * (1.0 + step_val))
            else:
                # A zero seed cannot grow multiplicatively; start it from a
                # fraction of its default.
                cand[key] = _clamp(key, PARAM_REGISTRY[key][0] * step_val)

    cands = []
    c = base_factors.copy()
    for i in range(max_attempts):
        c, changed = raise_subunity_factors(c, initial_step, locked)
        if not changed:
            break
        cands.append(("inflate-subunity{}".format(i + 1), c.copy()))
    c = c.copy()
    for key in CAPACITY_FACTOR_KEYS:
        if key.lower() not in locked_lower:
            c[key] = max(1.0, c.get(key, 1.0))
    step = initial_step
    for i in range(max(max_attempts // 2, 4)):
        for key in ELECTRIC_CAPACITY_FACTOR_KEYS:
            if key.lower() not in locked_lower:
                c[key] = max(_INFLATE_FLOOR, c.get(key, 1.0) * (1.0 + step))
        _scale_extra(c, step)
        cands.append(("inflate-electric{}".format(i + 1), c.copy()))
        step *= growth
    step = initial_step
    for i in range(max_attempts):
        c = inflate_factors(c, step, locked)
        _scale_extra(c, step)
        cands.append(("inflate-all{}".format(i + 1), c.copy()))
        step *= growth
    return cands


def _refine_between(infeasible_factors, feasible_factors, region, paths,
                    parallel_evals, points=8):
    """Evaluate evenly spaced points between a known-infeasible and a known-
    feasible factor dict in one batch; return the leanest feasible one as
    (factors, cost, None), or None.  Trims the overshoot of the geometric
    bootstrap steps."""
    n = max(3, min(points, parallel_evals if parallel_evals > 1 else points))
    alphas = [(i + 1) / (n + 1) for i in range(n)]
    specs = []
    for i, a in enumerate(alphas):
        cand = {
            k: _clamp(k, infeasible_factors.get(k, v) + a * (v - infeasible_factors.get(k, v)))
            for k, v in feasible_factors.items()
        }
        specs.append({"label": "bootstrap-refine{}".format(i + 1), "factors": cand})
    results = _eval_batch(specs, parallel_evals, region, paths)
    for spec, res in zip(specs, results):
        if res["feasible"]:
            return spec["factors"], res["cost"], None
    return None


def inflate_until_feasible(base_factors, region, paths, locked=(), scale_extra_keys=(),
                           parallel_evals=1, initial_step=0.1, growth=1.5, max_attempts=25):
    """Raise capacity factors (and *scale_extra_keys*, e.g. the free storage
    of a data-center case) until the model is feasible, then refine back toward
    the last infeasible candidate.  Returns (factors, cost, stdout_or_None).

    scale_extra_keys matters when the shortfall is storage POWER: a constant
    added load cannot be served by more generation alone once the battery/H2
    discharge rate is saturated."""
    locked = tuple(locked or ())
    locked_lower = {k.lower() for k in locked}
    extra_keys = [k for k in (scale_extra_keys or ()) if k.lower() not in locked_lower]
    cands = _inflation_candidates(base_factors, initial_step, growth, max_attempts,
                                  locked, extra_keys)
    for start in range(0, len(cands), parallel_evals):
        chunk = cands[start:start + parallel_evals]
        specs = [{"label": lbl, "factors": fac} for lbl, fac in chunk]
        results = _eval_batch(specs, parallel_evals, region, paths)
        for j, res in enumerate(results):
            if res["feasible"]:
                idx = start + j
                winner = cands[idx][1]
                lo = base_factors if idx == 0 else cands[idx - 1][1]
                leaner = _refine_between(lo, winner, region, paths, parallel_evals)
                return leaner if leaner else (winner, res["cost"], None)
    raise RuntimeError("Unable to inflate factors to achieve feasibility.")


# Storage anchors for the scratch bootstrap, derived from input data only
# (independent of the trial-and-error solution):
#   * power (TW) scales with the region's average all-purpose 2050 load L
#     (sum of countrystats.dat TLOADTOT): battery discharge ~ 1.0 x L (peak load
#     is ~1.5-2x average and batteries share peak duty with hydro/PHS/CSP);
#     H2 fuel cell / electrolyser ~ 0.1 x L (long-duration backup);
#   * durations are technology-typical: battery 4 h; PHS / cold / hot-water
#     12 h (diurnal); H2 30 days and UTES 90 days (seasonal).
_SCRATCH_TW_PER_AVG_LOAD = {"BATDISCH": 1.0, "FCDISCH": 0.10, "FCCHARG": 0.10}
_SCRATCH_DURATION_ANCHOR = {
    "STORHBAT": 4.0, "STORHPHS": 12.0, "STORHCOLD": 12.0, "STORHHWAT": 12.0,
    "DAYH2STOR": 30.0, "STORUGDYS": 90.0,
}


def _region_avg_load_tw(region: str):
    """Average all-purpose 2050 load (TW): sum of TLOADTOT (GW) over the
    region's countries in countrystats.dat.  Region names are matched on 14
    characters (Fortran CHARACTER(14), e.g. CENTRAL-AMERICA -> CENTRAL-AMERIC).
    Returns None if unavailable."""
    stats = BASE_RAW_DIR / "countrystats.dat"
    if not stats.exists():
        return None
    total = 0.0
    found = False
    try:
        for line in stats.read_text(encoding="ascii", errors="replace").splitlines():
            parts = line.split("\t")
            if len(parts) < 47 or parts[0] in ("", "Country"):
                continue
            if parts[1].strip()[:14] != region[:14]:
                continue
            try:
                total += float(parts[46])   # TLOADTOT (GW)
                found = True
            except ValueError:
                continue
    except OSError:
        return None
    return total / 1000.0 if found else None


def _scratch_storage_anchor(region: str) -> Dict[str, float]:
    """Anchor value of every storage design variable for the scratch ramp."""
    avg_load_tw = _region_avg_load_tw(region)
    anchors: Dict[str, float] = {}
    for key in FACTOR_KEYS:
        cat = PARAM_REGISTRY[key][1]
        if cat not in _STORAGE_CATS:
            continue
        if cat == "tw":
            if avg_load_tw is not None:
                anchors[key] = _SCRATCH_TW_PER_AVG_LOAD.get(key, 0.5) * avg_load_tw
            else:
                anchors[key] = PARAM_REGISTRY[key][0]
        else:
            anchors[key] = _SCRATCH_DURATION_ANCHOR.get(key, PARAM_REGISTRY[key][0])
    if avg_load_tw is None:
        print("  [WARN] Could not derive region load from countrystats.dat; "
              "scratch storage anchor falls back to registry defaults.")
    else:
        print("Scratch storage anchor (avg load {:.4f} TW): {}".format(
            avg_load_tw,
            ", ".join("{}={:.4g}".format(k, v) for k, v in sorted(anchors.items()))))
    return anchors


def scratch_bootstrap(base_factors, region, paths, parallel_evals=1,
                      ramp=(0.25, 0.5, 1.0, 1.5, 2.0)):
    """First feasible point from the scratch start, following the manual
    procedure: ramp storage toward the load-derived anchors with capacity
    factors held at 1.0 (all ramp steps in one batch), refine back toward the
    last infeasible step; if the whole ramp is infeasible, keep storage at the
    top of the ramp and inflate capacity factors."""
    anchors = _scratch_storage_anchor(region)
    ramp_cands = []
    specs = []
    for frac in ramp:
        cand = base_factors.copy()
        for key, anchor in anchors.items():
            cand[key] = anchor * frac
        ramp_cands.append(cand)
        specs.append({"label": "scratch-storage-ramp{:g}".format(frac), "factors": cand})
    results = _eval_batch(specs, parallel_evals, region, paths)
    for i, res in enumerate(results):
        if res["feasible"]:
            lo = base_factors if i == 0 else ramp_cands[i - 1]
            leaner = _refine_between(lo, ramp_cands[i], region, paths, parallel_evals)
            return leaner if leaner else (ramp_cands[i], res["cost"], None)
    return inflate_until_feasible(ramp_cands[-1], region, paths,
                                  parallel_evals=parallel_evals)


# Data-center feasibility probes.  The added data-center load is constant, and
# hand-tuned solutions meet it cheaply by raising the storage discharge/charge
# rate (plus a little generation) or with wind, whose output matches a constant
# load better than solar.  The generic inflation raises all free variables
# together and never visits these directions, so they are probed first.
_DC_PROBE_STEPS = (1.15, 1.3, 1.5, 1.75, 2.0, 2.5, 3.0)
# Rate levers start near zero, so they need larger multiplicative steps.
_DC_RATE_PROBE_STEPS = (1.5, 2.0, 3.0, 5.0, 8.0, 12.0)


def _dc_directional_bootstrap(base_factors, locked, region, paths, parallel_evals):
    """Directional feasibility search for data-center runs.

    Scales one lever group of the seed at a time (one batch per stage): the
    free storage rate keys, then the free wind factors, then all free electric
    generation factors.  Storage energy is left untouched.  The first feasible
    candidate is refined toward its predecessor.  Returns (factors, cost,
    None), or None when no stage reaches feasibility."""
    locked_lower = {k.lower() for k in (locked or ())}
    stages = [
        ("rate", [k for k in _STORAGE_RATE_KEYS if k.lower() not in locked_lower],
         _DC_RATE_PROBE_STEPS),
        ("wind", [k for k in _WIND_KEYS if k.lower() not in locked_lower],
         _DC_PROBE_STEPS),
        ("gen",  [k for k in ELECTRIC_CAPACITY_FACTOR_KEYS
                  if k.lower() not in locked_lower], _DC_PROBE_STEPS),
    ]
    for name, keys, steps in stages:
        if not keys:
            continue
        cands = []
        for f in steps:
            cand = base_factors.copy()
            for k in keys:
                cand[k] = _clamp(k, cand.get(k, PARAM_REGISTRY[k][0]) * f)
            cands.append(cand)
        specs = [{"label": "dc-probe-{}{:g}".format(name, f), "factors": c}
                 for f, c in zip(steps, cands)]
        results = _eval_batch(specs, parallel_evals, region, paths)
        for i, res in enumerate(results):
            if res["feasible"]:
                lo = base_factors if i == 0 else cands[i - 1]
                leaner = _refine_between(lo, cands[i], region, paths, parallel_evals)
                return leaner if leaner else (cands[i], res["cost"], None)
    return None


# ---------------------------------------------------------------------------
# Genetic algorithm
# ---------------------------------------------------------------------------

def _scratch_seed_population(feasible_factors: Dict[str, float],
                             locked: Sequence[str] = (),
                             rate_up: bool = False) -> List[Dict[str, float]]:
    """Deliberately spread starting individuals around a bootstrap point.

    A population of small mutants of one bootstrap point collapses onto its
    storage/capacity levels (elitist truncation plus blend crossover cannot
    create material no individual carries).  These variants scale the storage
    block down and the capacity factors up/down; infeasible ones die in the
    first generation.  With rate_up (data-center runs) they also raise the
    storage discharge/charge rate, a direction mutation climbs only slowly
    because the rate levers start near zero.  Locked keys are never changed.
    """
    locked_lower = {k.lower() for k in (locked or ())}
    storage_keys = [k for k in FACTOR_KEYS
                    if PARAM_REGISTRY[k][1] in _STORAGE_CATS
                    and k.lower() not in locked_lower]
    capacity_keys = [k for k in FACTOR_KEYS if PARAM_REGISTRY[k][1] == "capacity"
                     and k.lower() not in locked_lower]
    rate_keys = [k for k in FACTOR_KEYS if PARAM_REGISTRY[k][1] == "tw"
                 and k.lower() not in locked_lower]

    def scaled(s_mult=1.0, f_mult=1.0):
        v = feasible_factors.copy()
        for k in storage_keys:
            v[k] = _clamp(k, v[k] * s_mult)
        for k in capacity_keys:
            v[k] = _clamp(k, v[k] * f_mult)
        return v

    def rate_scaled(variant, r_mult):
        v = variant.copy()
        for k in rate_keys:
            v[k] = _clamp(k, v[k] * r_mult)
        return v

    variants = []
    for s in (0.25, 0.5, 0.75):
        variants.append(scaled(s_mult=s))
    for f in (0.7, 0.85, 1.15, 1.3):
        variants.append(scaled(f_mult=f))
    for s, f in ((0.5, 1.2), (0.25, 1.4), (0.75, 0.9), (0.5, 0.8)):
        variants.append(scaled(s_mult=s, f_mult=f))
    if rate_up and rate_keys:
        for r in (2.0, 4.0, 8.0):
            variants.append(rate_scaled(feasible_factors, r))
        variants.append(rate_scaled(scaled(f_mult=0.7), 8.0))   # power up, capacity down
        variants.append(rate_scaled(scaled(f_mult=1.3), 4.0))   # power up, capacity up
    return variants


def _zero_mutation_refs(region: str) -> Dict[str, float]:
    """Reference magnitudes for mutating a variable away from exactly zero.

    Storage-power (tw) keys scale with the region's average load, so the first
    step is proportionate in regions spanning orders of magnitude in size
    (a US-scale 0.09 TW step is ~50x Greenland's entire load).  Other keys use
    the registry default.  Empty when the region load cannot be derived."""
    refs: Dict[str, float] = {}
    avg_load_tw = _region_avg_load_tw(region)
    if avg_load_tw:
        for key in FACTOR_KEYS:
            if PARAM_REGISTRY[key][1] == "tw":
                refs[key] = _SCRATCH_TW_PER_AVG_LOAD.get(key, 0.5) * avg_load_tw
    return refs


def _mutation_step(key, current, mutation_scale, zero_refs):
    """Magnitude of one mutation of *key* at value *current*: relative to the
    value itself, or to a reference magnitude when the value is zero, damped
    for large values."""
    scale = _CATEGORY_SCALES.get(PARAM_REGISTRY[key][1], 1.0)
    damping = 1.0 / (max(1.0, abs(current)) ** _MAGNITUDE_DAMPING)
    if abs(current) < 1e-12:
        ref = zero_refs.get(key)
        if ref is None:
            ref = max(abs(PARAM_REGISTRY[key][0]), 0.01)
        return max(ref, 1e-9) * mutation_scale * scale * damping
    return abs(current) * mutation_scale * scale * damping


def mutate_factors(base, mutation_rate, mutation_scale, locked, zero_refs):
    """Mutate each unlocked design variable with probability mutation_rate by
    a random-sign step; if none was mutated, nudge one random variable up."""
    mutated = base.copy()
    keys = [k for k in FACTOR_KEYS if k.lower() not in locked]
    changed = False
    for key in keys:
        if random.random() < mutation_rate:
            current = mutated.get(key, PARAM_REGISTRY[key][0])
            delta = _mutation_step(key, current, mutation_scale, zero_refs)
            delta = delta if random.random() < 0.5 else -delta
            mutated[key] = _clamp(key, current + delta)
            changed = True
    if not changed and keys:
        key = random.choice(keys)
        current = mutated.get(key, PARAM_REGISTRY[key][0])
        delta = _mutation_step(key, current, mutation_scale, zero_refs)
        mutated[key] = _clamp(key, current + delta)
    return mutated


def crossover_factors(parent1: Dict[str, float], parent2: Dict[str, float]) -> Dict[str, float]:
    """Arithmetic (blend) crossover with an independent weight per variable."""
    child = {}
    for key in FACTOR_KEYS:
        default = PARAM_REGISTRY[key][0]
        w = random.random()
        child[key] = w * parent1.get(key, default) + (1 - w) * parent2.get(key, default)
    return child


def genetic_search(
    feasible_factors: Dict[str, float],
    feasible_cost: float,
    region: str,
    paths: dict,
    population_size: int = 24,
    generations: int = 50,
    mutation_rate: float = 0.15,
    mutation_scale: float = 0.2,
    elite_frac: float = 0.2,
    mutation_cooling: float = 0.98,
    parallel_evals: int = 1,
    locked_factors: Sequence[str] = (),
    seed_population: Sequence[Dict[str, float]] = None,
) -> Tuple[Dict[str, float], float]:
    """Elitist GA from a feasible seed.  Infeasible individuals get infinite
    cost; the best elite_frac survive, and the rest of each generation is
    blend crossover of two random elites plus mutation.  Mutation rate and
    scale decay by mutation_cooling per generation."""
    locked = {f.lower() for f in locked_factors or []}
    zero_refs = _zero_mutation_refs(region)
    population: List[Dict[str, float]] = [feasible_factors.copy()]
    for extra in (seed_population or []):
        if len(population) < population_size:
            population.append(extra.copy())
    while len(population) < population_size:
        population.append(mutate_factors(feasible_factors, mutation_rate, mutation_scale,
                                         locked, zero_refs))

    best_factors = feasible_factors.copy()
    best_cost = feasible_cost

    for gen in range(generations):
        cooling_factor = mutation_cooling ** gen
        eff_rate = max(0.0, min(1.0, mutation_rate * cooling_factor))
        eff_scale = mutation_scale * cooling_factor

        specs = [{"label": f"GA-gen{gen+1}-ind{idx+1}", "factors": indiv}
                 for idx, indiv in enumerate(population)]
        if parallel_evals > 1:
            evals = evaluate_trials_parallel(specs, parallel_evals, region, paths)
        else:
            evals = evaluate_trials_sequential(specs, region, paths)

        scored = []
        for spec, res in zip(specs, evals):
            cost = res["cost"] if res["feasible"] else float("inf")
            scored.append((cost, spec["factors"]))
            if res["feasible"] and cost < best_cost:
                best_cost = cost
                best_factors = spec["factors"].copy()

        scored.sort(key=lambda x: x[0])
        elites = [f for c, f in scored if c < float("inf")]
        elites = elites[:max(1, int(elite_frac * population_size))]
        if not elites:
            elites = [best_factors.copy()]

        new_population: List[Dict[str, float]] = elites.copy()
        while len(new_population) < population_size:
            parents = random.sample(elites, 2) if len(elites) >= 2 else elites * 2
            child = crossover_factors(parents[0], parents[1])
            new_population.append(mutate_factors(child, eff_rate, eff_scale,
                                                 locked, zero_refs))
        population = new_population

    return best_factors, best_cost


# Vestigial capacity: the GA mutates multiplicatively, so a shrinking variable
# approaches zero but essentially never reaches it, leaving capital paid for
# idle hardware.  The polish tests the discrete jumps to zero.  FCCHARG and
# FCDISCH are zeroed as a pair (the GA mutates them independently).
_POLISH_GROUPS = [
    ("h2fc",   ("FCCHARG", "FCDISCH")),
    ("soltherm", ("FACSHT",)),
]


def polish_optimum(best_factors, best_cost, region, paths, parallel_evals,
                   seed_factors=None, locked=()):
    """Cheap improvement tests on the GA optimum, in one batch.

    Always: zero-out variants for vestigial capacity (H2 pair, solar-thermal
    factor, both).  With *seed_factors* (data-center runs, seeded from the
    no-data-center optimum): revert-to-seed variants that undo the storage
    increase, the non-wind generation increase, or both, and power-for-energy
    variants that raise the storage rate with storage energy held at the seed.
    Locked keys are never changed.  A variant is adopted only when it is
    feasible and cheaper.  Returns (factors, cost, adopted_label_or_None).
    """
    locked_lower = {k.lower() for k in (locked or ())}
    combos = []
    active = [(name, keys) for name, keys in _POLISH_GROUPS
              if any(best_factors.get(k, 0.0) > 0.0 for k in keys)
              and not any(k.lower() in locked_lower for k in keys)]
    combos.extend(active)
    if len(active) > 1:
        combos.append(("all", tuple(k for _, keys in active for k in keys)))

    specs = []
    for name, keys in combos:
        variant = best_factors.copy()
        for k in keys:
            variant[k] = 0.0
        specs.append({"label": "polish-zero-{}".format(name), "factors": variant})

    if seed_factors:
        def _changed(cats, exclude=()):
            return [k for k in FACTOR_KEYS
                    if PARAM_REGISTRY[k][1] in cats and k not in exclude
                    and k.lower() not in locked_lower
                    and abs(best_factors.get(k, 0.0) - seed_factors.get(k, 0.0)) > 1e-12]
        storage_keys = _changed(_STORAGE_CATS)
        nonwind_keys = _changed(("capacity",), exclude=_WIND_KEYS)
        revert_groups = []
        if storage_keys:
            revert_groups.append(("storage", storage_keys))
        if nonwind_keys:
            revert_groups.append(("nonwind", nonwind_keys))
        if len(revert_groups) > 1:
            revert_groups.append(("both", storage_keys + nonwind_keys))
        for name, keys in revert_groups:
            variant = best_factors.copy()
            for k in keys:
                variant[k] = seed_factors[k]
            specs.append({"label": "polish-revert-{}".format(name), "factors": variant})

        rate_keys = [k for k in _STORAGE_RATE_KEYS if k.lower() not in locked_lower]
        if rate_keys and storage_keys:
            for rmult in (2.0, 4.0):
                variant = best_factors.copy()
                for k in storage_keys:
                    variant[k] = seed_factors[k]
                for k in rate_keys:
                    variant[k] = _clamp(k, seed_factors.get(k, variant.get(k, 0.0)) * rmult)
                specs.append({"label": "polish-rate-x{:g}".format(rmult),
                              "factors": variant})

    if not specs:
        return best_factors, best_cost, None
    results = _eval_batch(specs, parallel_evals, region, paths)

    factors, cost, adopted = best_factors, best_cost, None
    for spec, res in zip(specs, results):
        if res["feasible"] and res["cost"] < cost:
            factors, cost, adopted = spec["factors"], res["cost"], spec["label"]
    return factors, cost, adopted


# ---------------------------------------------------------------------------
# Main workflow
# ---------------------------------------------------------------------------

def run_workflow(
    region,
    baseline_start,
    parallel_evals=1,
    locked=(),
    ga_population=24,
    ga_generations=50,
    ga_mutation_rate=0.15,
    ga_mutation_scale=0.2,
    ga_elite_frac=0.2,
    ga_mutation_cooling=0.98,
    datacenter=0,
    evaluate_only=False,
    scratch_label="scratch",
    dc_label="",
    out_suffix="",
    max_land_pct=None,
    land_penalty_per_pp=None,
):
    global _IFDATCEN, _RUN_SUFFIX, _MAX_LAND_PCT, _LAND_PENALTY_PER_PP
    _IFDATCEN = int(datacenter)
    _MAX_LAND_PCT = max_land_pct
    if land_penalty_per_pp is not None:
        _LAND_PENALTY_PER_PP = land_penalty_per_pp
    if _MAX_LAND_PCT is not None:
        print("Land-use cap active: new spacing+footprint <= {:.2f}% of regional "
              "land (graded cost penalty, slope {:.1f}x/pp).".format(
                  _MAX_LAND_PCT, _LAND_PENALTY_PER_PP))
    locked = tuple(locked or ())
    t_workflow_start = time.perf_counter()

    # Scratch starts and data-center scenarios write to isolated results dirs
    # (the real region name is still passed to the binary).  dc_label
    # distinguishes data-center cases sharing an IFDATCEN value (_dc2, _dc2rc,
    # _dc2bat, _dc2h2); out_suffix overrides the suffix entirely, e.g. to
    # re-evaluate a saved optimum into its own directory.
    scratch = (baseline_start == "scratch")
    if out_suffix:
        _RUN_SUFFIX = "_" + out_suffix
    else:
        _RUN_SUFFIX = (("_" + scratch_label if scratch else "")
                       + ("_dc{}{}".format(_IFDATCEN, dc_label) if _IFDATCEN else ""))
    paths = _region_paths(region + _RUN_SUFFIX)
    paths["results_dir"].mkdir(parents=True, exist_ok=True)

    # A new GA run starts a new history; the previous one is kept as .prev.
    # Evaluate-only runs append to (never replace) an existing history.
    if not evaluate_only and paths["history_file"].exists():
        prev = paths["history_file"].with_suffix(".log.prev")
        paths["history_file"].replace(prev)
        print("Archived previous factor history to {}".format(prev))

    # Reference xx report (the expert trial-and-error result), if present:
    # summarized for cross-checks only, never modified.
    reference_xx = BASE_RAW_DIR / "xx.{}".format(REGION_SHORTCODE.get(region, region))
    if reference_xx.exists():
        print(f"Parsing reference xx report: {reference_xx.name}")
        parse_and_save(
            reference_xx.read_text(),
            factors=None,
            region=region,
            run_type="canonical_baseline",
            out_path=paths["results_dir"] / "canonical_baseline_summary.json",
        )
    else:
        print(f"  [INFO] No reference xx report at {reference_xx}; skipping.")

    if baseline_start == "defaults":
        base_factors = extract_fortran_region_defaults(region)
        print("Using Fortran hardcoded region defaults as baseline factors.")
    elif baseline_start == "scratch":
        base_factors = build_scratch_start()
        print("Using spreadsheet scratch start (capacity factors = 1.0, "
              "storage design variables = 0).")
    else:
        baseline_path = Path(baseline_start)
        if not baseline_path.exists():
            raise FileNotFoundError(f"Baseline file not found: {baseline_path}")
        print(f"Using baseline factors from {baseline_path}.")
        base_factors = load_baseline_start(baseline_path)

    # In evaluate-only mode the seeded factors ARE the result, so the run goes
    # to the optimal output and the baseline files are left untouched.
    baseline_paths = dict(paths)
    baseline_paths["fortran_out"] = (paths["fortran_optimal_out"] if evaluate_only
                                     else paths["fortran_baseline_out"])
    t_bl = time.perf_counter()
    if parallel_evals > 1 and baseline_start != "defaults":
        stdout = run_single_isolated("baseline", base_factors, region, baseline_paths)
    else:
        with _fortran_global_lock():
            if baseline_start == "defaults":
                # No factor file: the binary keeps its hardcoded region values.
                if paths["factor_file"].exists():
                    paths["factor_file"].unlink()
            else:
                write_factor_files(base_factors, paths)
            stdout = run_fortran(region, baseline_paths)
    baseline_eval_seconds = time.perf_counter() - t_bl
    print("Fortran output written to {}".format(baseline_paths["fortran_out"]))

    if evaluate_only:
        # Single evaluation of the seeded factors (e.g. the EGS data-center
        # case, where the Fortran adds EGS supply and no re-optimization is
        # needed): save it as the optimal output and xx report.
        paths["fortran_optimal_out"].write_text(stdout)
        xx_out = _xx_report_path(region)
        xx_out.write_text(strip_override_echo(stdout))
        opt_data = parse_and_save(
            stdout, factors=base_factors, region=region,
            run_type="evaluate", out_path=paths["optimal_summary"],
        )
        opt_data["timing"] = {
            "datacenter_scenario":   _IFDATCEN,
            "start_mode":            "scratch" if scratch else baseline_start,
            "optimizer":             "evaluate",
            "baseline_eval_seconds": round(baseline_eval_seconds, 3),
            "optimize_seconds":      0.0,
            "final_eval_seconds":    0.0,
            "total_seconds":         round(time.perf_counter() - t_workflow_start, 3),
            "n_evaluations":         1,
            "ga_population":         None,
            "ga_generations":        None,
            "parallel_evals":        parallel_evals,
        }
        save_summary(opt_data, paths["optimal_summary"])
        print("Evaluate-only: cost {:.3f} $B/yr  →  {}  (xx: {})".format(
            parse_cost(stdout), paths["optimal_summary"], xx_out))
        return

    parse_and_save(stdout, factors=base_factors, region=region,
                   run_type="baseline", out_path=paths["baseline_summary"])

    feasible_initial = check_feasibility(stdout)
    initial_cost = _apply_land_cap(parse_cost(stdout), stdout)
    log_candidate(base_factors, feasible_initial, initial_cost, "baseline",
                  paths["history_file"])

    if feasible_initial:
        print("Baseline feasible (cost {:.3f}). Proceeding to GA optimisation.".format(
            initial_cost))
        candidate, cost = base_factors.copy(), initial_cost
    elif scratch:
        print("Scratch start infeasible (expected); ramping storage toward feasibility.")
        candidate, cost, _ = scratch_bootstrap(base_factors, region, paths,
                                               parallel_evals=parallel_evals)
    else:
        probe = None
        if _IFDATCEN:
            print("Starting point infeasible; probing storage rate, wind, then all "
                  "free generation before joint inflation.")
            probe = _dc_directional_bootstrap(base_factors, locked, region, paths,
                                              parallel_evals)
        if probe:
            candidate, cost, _ = probe
            print("Directional bootstrap found feasibility (cost {:.3f}).".format(cost))
        else:
            # For data-center runs the free storage variables grow with the
            # capacity factors (see inflate_until_feasible).
            extra_scale = ()
            if _IFDATCEN:
                locked_lower = {f.lower() for f in locked}
                extra_scale = [k for k in FACTOR_KEYS
                               if PARAM_REGISTRY[k][1] in _STORAGE_CATS
                               and k.lower() not in locked_lower]
                print("Directional probes infeasible; inflating capacity factors "
                      "and free storage variables: {}".format(" ".join(extra_scale)))
            else:
                print("Starting point infeasible; inflating capacity factors.")
            candidate, cost, _ = inflate_until_feasible(
                base_factors, region, paths, locked=locked,
                scale_extra_keys=extra_scale, parallel_evals=parallel_evals)

    print("Genetic algorithm starting from feasible point (cost {:.3f}).".format(cost))

    t_opt = time.perf_counter()
    # Scratch and data-center runs seed part of the population with spread
    # variants of the bootstrap point.
    seed_pop = (_scratch_seed_population(candidate, locked, rate_up=bool(_IFDATCEN))
                if (scratch or _IFDATCEN) else None)
    best_factors, best_cost = genetic_search(
        candidate,
        cost,
        region,
        paths,
        population_size=ga_population,
        generations=ga_generations,
        mutation_rate=ga_mutation_rate,
        mutation_scale=ga_mutation_scale,
        elite_frac=ga_elite_frac,
        mutation_cooling=ga_mutation_cooling,
        parallel_evals=parallel_evals,
        locked_factors=locked,
        seed_population=seed_pop,
    )
    print("GA produced best feasible solution (cost {:.3f}).".format(best_cost))
    best_factors, best_cost, polish_label = polish_optimum(
        best_factors, best_cost, region, paths, parallel_evals,
        seed_factors=base_factors if _IFDATCEN else None, locked=locked)
    if polish_label:
        print("Polish adopted {} (cost {:.3f}).".format(polish_label, best_cost))
    write_dat(best_factors, paths["results_dir"] / "genetic_factors.dat")
    optimize_seconds = time.perf_counter() - t_opt

    # Final evaluation of the optimum: fortran_optimal_run.out, the summary
    # JSON and the xx report all come from this run.
    print("Running final Fortran evaluation with optimal factors...")
    t_fin = time.perf_counter()
    if parallel_evals > 1:
        final_stdout = run_single_isolated("final-optimal", best_factors, region, paths)
    else:
        with _fortran_global_lock():
            write_factor_files(best_factors, paths)
            final_stdout = run_fortran(region, paths)
    final_eval_seconds = time.perf_counter() - t_fin
    paths["fortran_optimal_out"].write_text(final_stdout)

    xx_out = _xx_report_path(region)
    xx_out.write_text(strip_override_echo(final_stdout))

    opt_data = parse_and_save(
        final_stdout,
        factors=best_factors,
        region=region,
        run_type="ga_optimal",
        out_path=paths["optimal_summary"],
    )

    try:
        n_evals = paths["history_file"].read_text().count("COST_MN_BIL_PER_YEAR")
    except OSError:
        n_evals = None
    opt_data["timing"] = {
        "datacenter_scenario":   _IFDATCEN,
        "start_mode":            "scratch" if scratch else baseline_start,
        "max_land_pct":          _MAX_LAND_PCT,
        "land_penalty_per_pp":   _LAND_PENALTY_PER_PP if _MAX_LAND_PCT is not None else None,
        "polish":                polish_label,
        "optimizer":             "ga",
        "baseline_eval_seconds": round(baseline_eval_seconds, 3),
        "optimize_seconds":      round(optimize_seconds, 3),
        "final_eval_seconds":    round(final_eval_seconds, 3),
        "total_seconds":         round(time.perf_counter() - t_workflow_start, 3),
        "n_evaluations":         n_evals,
        "ga_population":         ga_population,
        "ga_generations":        ga_generations,
        "parallel_evals":        parallel_evals,
    }
    save_summary(opt_data, paths["optimal_summary"])

    print("Final evaluation cost: {:.3f} $B/yr  →  {}".format(
        parse_cost(final_stdout), paths["optimal_summary"]))
    print("Solve time: optimize={:.1f}s  total={:.1f}s  evals={}  →  xx file: {}".format(
        optimize_seconds, opt_data["timing"]["total_seconds"], n_evals, xx_out))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(
        description="Optimize the LOADMATCH design variables of one region with a "
                    "genetic algorithm, seeded via --baseline-start.")
    parser.add_argument(
        "--region", required=True, metavar="REGION",
        help="Grid region (a GRIDUSE name in powerworld.f, e.g. EUROPE, CHINA).")
    parser.add_argument(
        "--baseline-start", required=True, metavar="defaults|scratch|PATH",
        help="Starting point: 'defaults' = the region values hardcoded in "
             "powerworld.f (the expert trial-and-error solution); 'scratch' = the "
             "spreadsheet start (capacity factors 1, storage 0), results in "
             "<REGION>_<scratch-label>/; PATH = a KEY = VALUE factor file, e.g. a "
             "saved genetic_factors.dat.")
    parser.add_argument(
        "--parallel-evals", type=int, default=1,
        help="Simultaneous Fortran evaluations (default: %(default)s).")
    parser.add_argument(
        "--lock", nargs="*", default=[], metavar="FACTOR",
        help="Design variables held at their starting value (case-insensitive).")
    parser.add_argument("--ga-population", type=int, default=24,
                        help="Population size (default: %(default)s).")
    parser.add_argument("--ga-generations", type=int, default=50,
                        help="Number of generations (default: %(default)s).")
    parser.add_argument("--ga-mutation-rate", type=float, default=0.15,
                        help="Per-variable mutation probability (default: %(default)s).")
    parser.add_argument("--ga-mutation-scale", type=float, default=0.2,
                        help="Relative mutation scale (default: %(default)s).")
    parser.add_argument("--ga-elite-frac", type=float, default=0.2,
                        help="Elite fraction kept each generation (default: %(default)s).")
    parser.add_argument("--ga-mutation-cooling", type=float, default=0.98,
                        help="Per-generation decay of mutation rate and scale "
                             "(default: %(default)s).")
    parser.add_argument(
        "--seed", type=int, default=12345,
        help="Random seed of the GA; the Fortran evaluations are deterministic, so "
             "a fixed seed makes a run reproducible (default: %(default)s).")
    parser.add_argument(
        "--max-land-pct", type=float, default=None,
        help="Cap on new land (wind spacing + footprint, %% of regional land), "
             "enforced as a graded cost penalty on the selection cost; reported "
             "costs stay unpenalized.  Default: no cap.")
    parser.add_argument(
        "--land-penalty-per-pp", type=float, default=None,
        help="Penalty slope: each percentage point above --max-land-pct multiplies "
             "the selection cost by (1 + slope x pp) (default {}).".format(
                 _LAND_PENALTY_PER_PP))
    parser.add_argument(
        "--datacenter", type=int, choices=[0, 1, 2], default=0,
        help="Data-center scenario (Fortran IFDATCEN): 0 = none, 1 = EGS-powered, "
             "2 = WWS-powered.  Results in <REGION>_dc<N><LABEL>/.")
    parser.add_argument(
        "--dc-label", type=str, default="",
        help="Label appended to the data-center suffix, e.g. --datacenter 2 "
             "--dc-label rc -> _dc2rc.")
    parser.add_argument(
        "--scratch-label", type=str, default="scratch",
        help="Suffix label for --baseline-start scratch runs (default: %(default)s).")
    parser.add_argument(
        "--out-suffix", type=str, default="",
        help="Force the output suffix (<REGION>_<SUFFIX>/, xx_optimized_<SUFFIX>/), "
             "overriding the scratch/data-center suffix.")
    parser.add_argument(
        "--evaluate-only", action="store_true",
        help="Evaluate the --baseline-start factors once and stop (no GA); writes "
             "optimal_summary.json and the xx report from that run.")
    args = parser.parse_args()
    unknown = [k for k in args.lock if k.upper() not in PARAM_REGISTRY]
    if unknown:
        parser.error("--lock: unknown parameter(s): {}".format(" ".join(unknown)))
    return args


def main():
    args = parse_args()
    # Seeding the parent process makes the GA reproducible: the Fortran runs are
    # deterministic and the spawned evaluation workers draw no random numbers.
    random.seed(args.seed)
    print(f"[ga] random seed = {args.seed}")
    run_workflow(
        region=args.region,
        baseline_start=args.baseline_start,
        parallel_evals=max(1, args.parallel_evals),
        locked=args.lock,
        ga_population=args.ga_population,
        ga_generations=args.ga_generations,
        ga_mutation_rate=args.ga_mutation_rate,
        ga_mutation_scale=args.ga_mutation_scale,
        ga_elite_frac=args.ga_elite_frac,
        ga_mutation_cooling=args.ga_mutation_cooling,
        datacenter=args.datacenter,
        evaluate_only=args.evaluate_only,
        scratch_label=args.scratch_label,
        dc_label=args.dc_label,
        out_suffix=args.out_suffix,
        max_land_pct=args.max_land_pct,
        land_penalty_per_pp=args.land_penalty_per_pp,
    )


if __name__ == "__main__":
    try:
        multiprocessing.set_start_method("spawn")
    except RuntimeError:
        pass
    main()

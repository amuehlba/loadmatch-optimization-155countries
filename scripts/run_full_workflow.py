import argparse
import multiprocessing
import os
import random
import re
import shutil
import subprocess
import tempfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Dict, Sequence, List, Tuple

from src.io.dat_parser import read_dat, write_dat
from scripts import run_python_model, export_fortran_factors

LP_SUMMARY = Path("data/results_python/summary.dat")
FACTOR_RESULT = Path("data/results_python/fortran_factors.dat")
FACTOR_DEST = Path("fortran/fortran_factors.dat")
FACTOR_PATHHOME = Path("data/raw/fortran_factors.dat")
FACTOR_PATHS = [FACTOR_RESULT, FACTOR_DEST, FACTOR_PATHHOME]
MIN_FACTOR = 0.05
FORTRAN_EXE = Path("fortran/bin/powerworld").resolve()
BASE_RAW_DIR = Path("data/raw").resolve()
WORKSPACE_BASE = Path("data/tmp_workspaces")
RESULTS_DIR = Path("data/results_verification")
FORTRAN_LOG = RESULTS_DIR / "fortran_stdout.log"
FORTRAN_ERR = RESULTS_DIR / "fortran_stderr.log"
FORTRAN_OUT = RESULTS_DIR / "fortran_last_run.out"
HISTORY_FILE = RESULTS_DIR / "factor_history.log"

# ---------------------------------------------------------------------------
# Parameter registry: every tunable Fortran parameter with CONUS defaults.
#
# Each entry:  KEY -> (default_conus, category, description)
#
# Categories control mutation behaviour:
#   "capacity"  – dimensionless scaling factor (typically 0.05–10)
#   "ratio"     – dimensionless ratio
#   "factor"    – dimensionless multiplier
#   "hours"     – storage / DR duration in hours  (>= 0)
#   "days"      – storage duration in days         (>= 0)
#   "tw"        – power rate in TW                 (>= 0)
#   "fraction"  – value between 0 and 1
#   "cop"       – coefficient of performance       (1–6)
#   "fixed"     – locked by default, not a design variable
# ---------------------------------------------------------------------------
PARAM_REGISTRY: Dict[str, Tuple[float, str, str]] = {
    # --- Original 7 capacity factors ---
    "FACONWIN":    (1.0,          "capacity",  "Onshore wind capacity scaling"),
    "FACOFFWIN":   (1.0,          "capacity",  "Offshore wind capacity scaling"),
    "FACUTILPV":   (1.0,          "capacity",  "Utility-scale PV capacity scaling"),
    "FACRESPV":    (1.0,          "capacity",  "Residential rooftop PV scaling"),
    "FACCOMPV":    (1.0,          "capacity",  "Commercial rooftop PV scaling"),
    "CSPTURBFAC":  (1.0,          "capacity",  "CSP turbine capacity ratio"),
    "FACSHT":      (1.0,          "capacity",  "Solar thermal heat scaling"),
    # --- CSP / storage configuration ---
    "CSPSTORGAT":  (2.61244594,   "ratio",     "CSP storage charge/discharge ratio"),
    "MXHRDRM":     (11.0,         "hours",     "Max demand-response shift hours"),
    "BATDISCH":    (1.55,         "tw",        "Battery max discharge rate (TW)"),
    "HCHARCSP":    (14.0,         "hours",     "CSP max charge hours"),
    "STORHBAT":    (4.0,          "hours",     "Battery storage duration hours"),
    "STORHCOLD":   (14.0,         "hours",     "Cold storage hours (PCM-ice + CW-STES)"),
    "STORHHWAT":   (14.0,         "hours",     "Hot-water STES hours"),
    "STORHPHS":    (14.0,         "hours",     "Pumped hydro storage hours"),
    # --- UTES and hydrogen storage ---
    "UGFAC":       (3.0,          "factor",    "UTES charge rate factor"),
    "STORUGDYS":   (60.0,         "days",      "UTES seasonal heat storage days"),
    "DAYH2STOR":   (40.0,         "days",      "H2 storage days"),
    # --- Hydropower ---
    "HPTURBRAT":   (10.0,         "ratio",     "Hydro turbine discharge ratio"),
    "DAMCAPRAT":   (0.583,        "ratio",     "Hydro dam capacity / annual output"),
    "DAYBASHYD":   (360.0,        "days",      "Baseload hydro storage days"),
    # --- Thermal storage and demand response ---
    "COOLSTES":    (0.4,          "fraction",  "Fraction AC from CW-STES vs ice"),
    "PHSMIN":      (0.016,        "tw",        "Min PHS nameplate capacity (TW)"),
    "FHEATFLX":    (0.15,         "fraction",  "Flexible heat load fraction"),
    "FCOLDFLX":    (0.15,         "fraction",  "Flexible cold load fraction"),
    "FRSTORINIT":  (0.5,          "fraction",  "Initial storage fill fraction"),
    "FDISTHEAT":   (0.2,          "fraction",  "District heating fraction"),
    # --- Heat pump and health ---
    "CPERFORM":    (4.0,          "cop",       "Heat pump COP (kWh-th/kWh-el)"),
    "HCDDADD":     (1.0,          "fixed",     "HDD/CDD daily minimum (numerical safeguard)"),
    "FMORTBAU":    (0.9,          "fixed",     "BAU air-pollution mortality fraction"),
    # --- Hot-water, H2, heat battery ---
    "HWFAC":       (1.0,          "factor",    "HW-STES charge rate factor"),
    "FCDISCH":     (0.091,        "tw",        "H2 fuel-cell discharge rate (TW)"),
    "FCCHARG":     (0.091,        "tw",        "H2 electrolyser charge rate (TW)"),
    "STORHHFC":    (13.0,         "hours",     "H2 electricity storage hours"),
    "HBTDISCH":    (0.0,          "tw",        "Heat battery discharge rate (TW)"),
    "STORHHBT":    (15.0,         "hours",     "Heat battery storage hours"),
    # --- Industrial heat flexibility ---
    "FRCIHFLEX":   (0.5,          "fraction",  "Flexible industrial heat fraction"),
}

FACTOR_KEYS: List[str] = list(PARAM_REGISTRY.keys())

# The original 7 capacity-scaling factors (used to decide what inflate_until_feasible touches)
CAPACITY_FACTOR_KEYS = [
    "FACONWIN", "FACOFFWIN", "FACUTILPV", "FACRESPV",
    "FACCOMPV", "CSPTURBFAC", "FACSHT",
]

# Parameters locked by default (not engineering design variables)
DEFAULT_LOCKED = {"HCDDADD", "FMORTBAU"}

# Per-category default mutation scale multiplier.
# Scales are relative — they multiply the global --ga-mutation-scale.
_CATEGORY_SCALES = {
    "capacity":  1.0,
    "ratio":     0.5,
    "factor":    0.5,
    "hours":     0.3,
    "days":      0.3,
    "tw":        0.3,
    "fraction":  0.5,
    "cop":       0.2,
    "fixed":     0.0,   # never mutated (also locked)
}

DEFAULT_FACTOR_SCALES: Dict[str, float] = {
    key.lower(): _CATEGORY_SCALES.get(cat, 1.0)
    for key, (_, cat, _) in PARAM_REGISTRY.items()
}

# Bounds: (min, max) for each category.  None = no bound.
_CATEGORY_BOUNDS = {
    "capacity":  (MIN_FACTOR, None),
    "ratio":     (0.0,        None),
    "factor":    (0.0,        None),
    "hours":     (0.0,        None),
    "days":      (0.0,        None),
    "tw":        (0.0,        None),
    "fraction":  (0.0,        1.0),
    "cop":       (1.0,        6.0),
    "fixed":     (None,       None),
}


def _clamp(key: str, value: float) -> float:
    """Clamp *value* to the valid bounds for *key*."""
    cat = PARAM_REGISTRY.get(key.upper(), (0, "capacity", ""))[1]
    lo, hi = _CATEGORY_BOUNDS.get(cat, (None, None))
    if lo is not None and value < lo:
        value = lo
    if hi is not None and value > hi:
        value = hi
    return value


ANNUAL_COST_PATTERN = re.compile(
    r"ANNUAL TOT ENERGY COST.*?=\s+([-0-9\.Ee+]+)\s+([-0-9\.Ee+]+)\s+([-0-9\.Ee+]+)"
)


# ---------------------------------------------------------------------------
# Fortran I/O helpers
# ---------------------------------------------------------------------------

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
    for path in FACTOR_PATHS:
        write_dat(factors, path)
    _verify_factor_files(factors)


def _read_factor_file(path: Path) -> Dict[str, float]:
    if not path.exists():
        return {}
    data = read_dat(str(path))
    return {k.lower(): float(v) for k, v in data.items()}


def _verify_factor_files(factors: Dict[str, float]):
    expected = {k.lower(): float(v) for k, v in factors.items()}
    for path in FACTOR_PATHS:
        data = _read_factor_file(path)
        missing = [k for k in expected if k not in data]
        if missing:
            raise RuntimeError(
                f"Factor file {path} missing keys: {', '.join(missing)}"
            )
        for key, val in expected.items():
            if abs(data.get(key, float("nan")) - val) > 1e-9:
                raise RuntimeError(
                    f"Factor mismatch in {path} for {key}: "
                    f"expected {val}, found {data.get(key)}"
                )


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


# ---------------------------------------------------------------------------
# Feasibility inflation  (only touches the original capacity factors)
# ---------------------------------------------------------------------------

def inflate_factors(factors, step):
    """Inflate only the capacity-scaling factors; leave others untouched."""
    inflated = factors.copy()
    for key in CAPACITY_FACTOR_KEYS:
        value = inflated.get(key, 1.0)
        if value > 0:
            inflated[key] = max(MIN_FACTOR, value * (1.0 + step))
        else:
            inflated[key] = max(step, MIN_FACTOR)
    return inflated


def raise_subunity_factors(factors, step):
    """Raise sub-unity capacity factors toward 1.0; leave others untouched."""
    updated = factors.copy()
    changed = False
    for key in CAPACITY_FACTOR_KEYS:
        value = updated.get(key, 1.0)
        if value <= 0:
            updated[key] = max(step, MIN_FACTOR)
            changed = True
        elif value < 1.0:
            updated[key] = min(1.0, value * (1.0 + step))
            changed = True
    return updated, changed


def inflate_until_feasible(base_factors, initial_step=0.1, growth=1.5, max_attempts=25):
    candidate = base_factors.copy()
    step = initial_step

    # Phase 1: raise sub-unity capacity factors toward 1.0
    for attempt in range(1, max_attempts + 1):
        candidate, changed = raise_subunity_factors(candidate, step)
        label = "inflate-subunity{}".format(attempt)
        feasible, cost, _ = evaluate_factors(candidate, label=label)
        if feasible:
            return candidate, cost
        if not changed:
            break

    # Phase 2: expand capacity factors once everything is >= 1
    for key in CAPACITY_FACTOR_KEYS:
        candidate[key] = max(1.0, candidate.get(key, 1.0))
    for attempt in range(1, max_attempts + 1):
        candidate = inflate_factors(candidate, step)
        label = "inflate-step{}".format(attempt)
        feasible, cost, _ = evaluate_factors(candidate, label=label)
        if feasible:
            return candidate, cost
        step *= growth

    raise RuntimeError("Unable to inflate factors to achieve feasibility.")


# ---------------------------------------------------------------------------
# Parallel workspace helpers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Hooke-Jeeves search
# ---------------------------------------------------------------------------

def hooke_jeeves_search(
    feasible_factors,
    feasible_cost,
    initial_step=0.2,
    shrink=0.7,
    max_iter=40,
    min_step=1e-5,
    parallel_evals=1,
    direction="both",
    locked_factors=None,
):
    step = initial_step
    candidate = feasible_factors.copy()
    best_cost = feasible_cost
    best_factors = feasible_factors.copy()
    locked = {name.lower() for name in (locked_factors or [])}

    for iteration in range(max_iter):
        print("Hooke-Jeeves iteration {} step {:.4f}".format(iteration + 1, step))
        improved = False
        trial_specs = []
        for key in FACTOR_KEYS:
            if key.lower() in locked:
                continue
            current = candidate.get(key, 0.0)
            deltas = []
            if direction in ("dec", "both") and current > 0:
                deltas.append(-current * step)
            if direction in ("inc", "both"):
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
            if step < min_step:
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


# ---------------------------------------------------------------------------
# GA: mutation, crossover, search
# ---------------------------------------------------------------------------

def mutate_factors(
    base: Dict[str, float],
    mutation_rate: float,
    mutation_scale: float,
    direction: str,
    locked: set,
    factor_scales: Dict[str, float],
    magnitude_damping: float,
) -> Dict[str, float]:
    mutated = base.copy()
    keys = [k for k in FACTOR_KEYS if k.lower() not in locked]
    changed = False
    for key in keys:
        if random.random() < mutation_rate:
            current = mutated.get(key, PARAM_REGISTRY[key][0])
            scale = factor_scales.get(key.lower(), DEFAULT_FACTOR_SCALES.get(key.lower(), 1.0))
            if scale == 0.0:
                continue  # locked via scale
            magnitude_scale = 1.0 / (max(1.0, abs(current)) ** magnitude_damping)
            # Use the parameter default as reference when current is near zero
            if abs(current) < 1e-12:
                ref = abs(PARAM_REGISTRY.get(key, (1.0,))[0])
                ref = max(ref, 0.01)
                delta = ref * mutation_scale * scale * magnitude_scale
            else:
                delta = abs(current) * mutation_scale * scale * magnitude_scale
            if direction == "dec":
                delta = -abs(delta)
            elif direction == "inc":
                delta = abs(delta)
            else:
                delta = delta if random.random() < 0.5 else -delta
            trial = current + delta
            trial = _clamp(key, trial)
            mutated[key] = trial
            changed = True
    if not changed and keys:
        key = random.choice(keys)
        current = mutated.get(key, PARAM_REGISTRY[key][0])
        scale = factor_scales.get(key.lower(), DEFAULT_FACTOR_SCALES.get(key.lower(), 1.0))
        if scale == 0.0:
            return mutated
        magnitude_scale = 1.0 / (max(1.0, abs(current)) ** magnitude_damping)
        if abs(current) < 1e-12:
            ref = abs(PARAM_REGISTRY.get(key, (1.0,))[0])
            ref = max(ref, 0.01)
            delta = ref * mutation_scale * scale * magnitude_scale
        else:
            delta = abs(current) * mutation_scale * scale * magnitude_scale
        delta = -abs(delta) if direction == "dec" else abs(delta)
        trial = current + delta
        trial = _clamp(key, trial)
        mutated[key] = trial
    return mutated


def crossover_factors(parent1: Dict[str, float], parent2: Dict[str, float]) -> Dict[str, float]:
    child = {}
    for key in FACTOR_KEYS:
        default = PARAM_REGISTRY[key][0]
        w = random.random()
        child[key] = w * parent1.get(key, default) + (1 - w) * parent2.get(key, default)
    return child


def genetic_search(
    feasible_factors: Dict[str, float],
    feasible_cost: float,
    population_size: int = 24,
    generations: int = 50,
    mutation_rate: float = 0.15,
    mutation_scale: float = 0.2,
    elite_frac: float = 0.2,
    direction: str = "both",
    parallel_evals: int = 1,
    locked_factors: Sequence[str] = (),
    mutation_cooling: float = 0.98,
    factor_scales: Dict[str, float] = None,
    magnitude_damping: float = 0.5,
) -> Tuple[Dict[str, float], float]:
    locked = {f.lower() for f in locked_factors or []}
    # Merge user-supplied scales on top of category defaults
    merged_scales = DEFAULT_FACTOR_SCALES.copy()
    if factor_scales:
        merged_scales.update({k.lower(): v for k, v in factor_scales.items()})
    population: List[Dict[str, float]] = [feasible_factors.copy()]
    while len(population) < population_size:
        population.append(
            mutate_factors(
                feasible_factors,
                mutation_rate,
                mutation_scale,
                direction,
                locked,
                merged_scales,
                magnitude_damping,
            )
        )

    best_factors = feasible_factors.copy()
    best_cost = feasible_cost

    for gen in range(generations):
        cooling_factor = mutation_cooling ** gen
        eff_rate = max(0.0, min(1.0, mutation_rate * cooling_factor))
        eff_scale = mutation_scale * cooling_factor

        specs = []
        for idx, indiv in enumerate(population):
            specs.append(
                {
                    "key": "GA",
                    "label": f"GA-gen{gen+1}-ind{idx+1}",
                    "factors": indiv,
                }
            )

        if parallel_evals > 1:
            evals = evaluate_trials_parallel(specs, parallel_evals)
        else:
            evals = evaluate_trials_sequential(specs)

        scored = []
        for spec, res in zip(specs, evals):
            cost = res["cost"]
            feasible = res["feasible"]
            if not feasible:
                cost = float("inf")
            scored.append((cost, spec["factors"]))
            if feasible and cost < best_cost:
                best_cost = cost
                best_factors = spec["factors"].copy()

        scored.sort(key=lambda x: x[0])
        elites = [f for c, f in scored if c < float("inf")]
        elite_count = max(1, int(elite_frac * population_size))
        elites = elites[:elite_count]
        if not elites:
            elites = [best_factors.copy()]

        new_population: List[Dict[str, float]] = elites.copy()
        while len(new_population) < population_size:
            parents = random.sample(elites, 2) if len(elites) >= 2 else elites * 2
            child = crossover_factors(parents[0], parents[1])
            child = mutate_factors(
                child,
                eff_rate,
                eff_scale,
                direction,
                locked,
                merged_scales,
                magnitude_damping,
            )
            new_population.append(child)
        population = new_population

    return best_factors, best_cost


# ---------------------------------------------------------------------------
# Main workflow
# ---------------------------------------------------------------------------

def _build_full_factors(lp_factors: Dict[str, float]) -> Dict[str, float]:
    """Merge LP capacity factors with Fortran CONUS defaults for all other params."""
    all_factors = {k: PARAM_REGISTRY[k][0] for k in FACTOR_KEYS}
    all_factors.update({k: float(v) for k, v in lp_factors.items()})
    return all_factors


def run_workflow(
    region="UNITED-STATES",
    parallel_evals=1,
    hj_initial_step=0.2,
    hj_shrink=0.7,
    hj_max_iter=40,
    hj_min_step=1e-5,
    hj_direction="both",
    hj_locked_factors=None,
    optimizer="hj",
    ga_population=24,
    ga_generations=50,
    ga_mutation_rate=0.15,
    ga_mutation_scale=0.2,
    ga_elite_frac=0.2,
    ga_mutation_cooling=0.98,
    ga_factor_scales=None,
    ga_magnitude_damping=0.5,
):
    run_python_model.main()
    export_fortran_factors.main([])
    lp_factors = read_dat(str(FACTOR_RESULT))
    base_factors = _build_full_factors(lp_factors)
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

    if optimizer == "ga":
        print(
            "Genetic algorithm starting from feasible point with cost {:.3f}.".format(
                cost
            )
        )
    else:
        print(
            "Hooke-Jeeves search starting from feasible point with cost {:.3f}.".format(
                cost
            )
        )
    if optimizer == "ga":
        best_factors, best_cost = genetic_search(
            candidate,
            cost,
            population_size=ga_population,
            generations=ga_generations,
            mutation_rate=ga_mutation_rate,
            mutation_scale=ga_mutation_scale,
            elite_frac=ga_elite_frac,
            direction=hj_direction,
            parallel_evals=parallel_evals,
            locked_factors=hj_locked_factors,
            mutation_cooling=ga_mutation_cooling,
            factor_scales=ga_factor_scales,
            magnitude_damping=ga_magnitude_damping,
        )
        print(
            "Genetic algorithm produced feasible factors with cost {:.3f}.".format(
                best_cost
            )
        )
        write_dat(best_factors, RESULTS_DIR / "genetic_factors.dat")
        write_factor_files(best_factors)
    else:
        hooke_factors, best_cost = hooke_jeeves_search(
            candidate,
            cost,
            initial_step=hj_initial_step,
            shrink=hj_shrink,
            max_iter=hj_max_iter,
            min_step=hj_min_step,
            parallel_evals=parallel_evals,
            direction=hj_direction,
            locked_factors=hj_locked_factors,
        )
        print(
            "Hooke-Jeeves search produced feasible factors with cost {:.3f}.".format(
                best_cost
            )
        )
        write_dat(hooke_factors, RESULTS_DIR / "hooke_jeeves_factors.dat")
        write_factor_files(hooke_factors)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Run LP optimisation + Fortran verification with Hooke-Jeeves or genetic algorithm.\n\n"
            "GA STRATEGY WITH MANY PARAMETERS:\n"
            "  The model exposes ~35 tunable parameters. For staged optimisation:\n"
            "  Phase 1: Lock new params, optimise capacity factors only:\n"
            "    --hj-lock CSPSTORGAT MXHRDRM BATDISCH ... (all non-capacity params)\n"
            "  Phase 2: Lock capacity factors, optimise storage/DR params.\n"
            "  Phase 3: Unlock all.\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--parallel-evals",
        type=int,
        default=1,
        help="Number of simultaneous Fortran evaluations (default: 1).",
    )
    parser.add_argument(
        "--hj-initial-step",
        type=float,
        default=0.2,
        help="Initial relative step size for Hooke-Jeeves (default: 0.2).",
    )
    parser.add_argument(
        "--hj-shrink",
        type=float,
        default=0.7,
        help="Shrink factor applied when no improvement is found (default: 0.7).",
    )
    parser.add_argument(
        "--hj-max-iter",
        type=int,
        default=40,
        help="Maximum Hooke-Jeeves iterations (default: 40).",
    )
    parser.add_argument(
        "--hj-min-step",
        type=float,
        default=1e-5,
        help="Minimum step size before terminating Hooke-Jeeves (default: 1e-5).",
    )
    parser.add_argument(
        "--hj-direction",
        choices=["inc", "dec", "both"],
        default="both",
        help="Direction of factor perturbations (default: both).",
    )
    parser.add_argument(
        "--hj-lock",
        nargs="*",
        default=list(DEFAULT_LOCKED),
        metavar="FACTOR",
        help="Factor names to keep fixed during optimisation. "
        "Case-insensitive. Default: %(default)s.",
    )
    parser.add_argument(
        "--optimizer",
        choices=["hj", "ga"],
        default="hj",
        help="Optimizer: hj (Hooke-Jeeves) or ga (genetic algorithm). Default: hj.",
    )
    parser.add_argument(
        "--ga-population",
        type=int,
        default=24,
        help="GA population size (default: 24).",
    )
    parser.add_argument(
        "--ga-generations",
        type=int,
        default=50,
        help="GA generations (default: 50).",
    )
    parser.add_argument(
        "--ga-mutation-rate",
        type=float,
        default=0.15,
        help="GA per-factor mutation probability (default: 0.15). "
        "With ~35 params, 0.15 -> ~5 mutations per individual.",
    )
    parser.add_argument(
        "--ga-mutation-scale",
        type=float,
        default=0.2,
        help="Relative mutation scale (default: 0.2).",
    )
    parser.add_argument(
        "--ga-elite-frac",
        type=float,
        default=0.2,
        help="Elite fraction preserved each GA generation (default: 0.2).",
    )
    parser.add_argument(
        "--ga-mutation-cooling",
        type=float,
        default=0.98,
        help="Per-generation decay applied to GA mutation rate/scale (default: 0.98).",
    )
    parser.add_argument(
        "--ga-factor-scale",
        action="append",
        default=[],
        metavar="NAME=SCALE",
        help="Per-factor mutation scale multiplier, e.g., FACONWIN=0.5. "
        "Overrides category defaults. May be repeated.",
    )
    parser.add_argument(
        "--ga-magnitude-damping",
        type=float,
        default=0.5,
        help="Exponent for 1/max(1,value) to damp mutations on large values (default: 0.5).",
    )
    return parser.parse_args()

def _parse_factor_scales(raw_list: List[str]) -> Dict[str, float]:
    scales: Dict[str, float] = {}
    for item in raw_list:
        if "=" not in item:
            raise ValueError(f"Invalid --ga-factor-scale entry '{item}'. Expected NAME=SCALE.")
        name, val = item.split("=", 1)
        name = name.strip()
        try:
            scales[name.lower()] = float(val)
        except ValueError:
            raise ValueError(f"Invalid scale '{val}' for factor '{name}'.")
    return scales


def main():
    args = parse_args()
    factor_scales = _parse_factor_scales(args.ga_factor_scale)
    run_workflow(
        parallel_evals=max(1, args.parallel_evals),
        hj_initial_step=args.hj_initial_step,
        hj_shrink=args.hj_shrink,
        hj_max_iter=args.hj_max_iter,
        hj_min_step=args.hj_min_step,
        hj_direction=args.hj_direction,
        hj_locked_factors=args.hj_lock,
        optimizer=args.optimizer,
        ga_population=args.ga_population,
        ga_generations=args.ga_generations,
        ga_mutation_rate=args.ga_mutation_rate,
        ga_mutation_scale=args.ga_mutation_scale,
        ga_elite_frac=args.ga_elite_frac,
        ga_mutation_cooling=args.ga_mutation_cooling,
        ga_factor_scales=factor_scales,
        ga_magnitude_damping=args.ga_magnitude_damping,
    )


if __name__ == "__main__":
    try:
        multiprocessing.set_start_method("spawn")
    except RuntimeError:
        pass
    main()

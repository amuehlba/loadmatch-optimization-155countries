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
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Dict, Sequence, List, Tuple

from src.io.dat_parser import read_dat, write_dat
from scripts import run_python_model, export_fortran_factors
from scripts.parse_fortran_output import parse_and_save as _parse_and_save, _ANNUAL_COST_RE as _ANNUAL_COST_PATTERN
# plot_results is imported lazily inside run_workflow() to avoid a circular
# import (plot_results imports PARAM_REGISTRY etc. from this module).

MIN_FACTOR = 0.05
FORTRAN_EXE = Path("fortran/bin/powerworld").resolve()
BASE_RAW_DIR = Path("data/raw").resolve()
WORKSPACE_BASE = Path("data/tmp_workspaces")
_DEFAULT_REGION = "UNITED-STATES"
# Lock file used to serialise all direct (non-workspace) Fortran calls so that
# concurrent cluster jobs writing to the shared fortran/fortran_factors.dat do
# not clobber each other.
_FORTRAN_LOCK_PATH = Path("fortran/.fortran_run.lock")


@contextlib.contextmanager
def _fortran_global_lock():
    """Exclusive file lock around write-factor + run-Fortran to prevent races."""
    _FORTRAN_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_FORTRAN_LOCK_PATH, "w") as _lf:
        fcntl.flock(_lf, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(_lf, fcntl.LOCK_UN)


def _region_paths(region: str):
    """Return per-region output paths.  Every region gets its own sub-folder."""
    lp_dir = Path("data/results_python") / region
    results_dir = Path("data/results_verification") / region
    return dict(
        lp_summary=lp_dir / "summary.dat",
        factor_result=lp_dir / "fortran_factors.dat",
        factor_dest=Path("fortran/fortran_factors.dat"),
        factor_pathhome=Path("data/raw/fortran_factors.dat"),
        results_dir=results_dir,
        fortran_log=results_dir / "fortran_stdout.log",
        fortran_err=results_dir / "fortran_stderr.log",
        fortran_out=results_dir / "fortran_last_run.out",
        fortran_baseline_out=results_dir / "fortran_baseline_run.out",
        history_file=results_dir / "factor_history.log",
        # Parsed JSON summaries (raw .out files are always kept as well)
        baseline_summary=results_dir / "baseline_summary.json",
        optimal_summary=results_dir / "optimal_summary.json",
        # Final optimal Fortran output (consumed by plot_results.py)
        fortran_optimal_out=results_dir / "fortran_optimal_run.out",
        # LP warm-start GA: separate outputs so they don't overwrite the baseline-GA results
        lp_eval_summary=results_dir / "lp_summary.json",
        lp_ga_summary=results_dir / "lp_ga_summary.json",
        fortran_lp_out=results_dir / "fortran_lp_run.out",
        fortran_lp_ga_out=results_dir / "fortran_lp_ga_run.out",
        lp_ga_history_file=results_dir / "lp_ga_factor_history.log",
        first_feasible_summary=results_dir / "first_feasible_summary.json",
    )


# Module-level defaults kept for backward compatibility (US region).
_paths = _region_paths(_DEFAULT_REGION)
LP_SUMMARY = _paths["lp_summary"]
FACTOR_RESULT = _paths["factor_result"]
FACTOR_DEST = _paths["factor_dest"]
FACTOR_PATHHOME = _paths["factor_pathhome"]
FACTOR_PATHS = [FACTOR_RESULT, FACTOR_DEST, FACTOR_PATHHOME]
RESULTS_DIR = _paths["results_dir"]
FORTRAN_LOG = _paths["fortran_log"]
FORTRAN_ERR = _paths["fortran_err"]
FORTRAN_OUT = _paths["fortran_out"]
HISTORY_FILE = _paths["history_file"]

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
    # --- Hydropower ---
    # HPTURBRAT: 10.0 for CONUS; 1.0 for every other region in powerworld.f.
    # MXHRDRM:   11   for CONUS; 8   for every other region in powerworld.f.
    # These values are NOT written to factor files (write_factor_files omits fixed
    # params), so Fortran always uses its region-specific hardcoded value.
    "HPTURBRAT":   (10.0,         "fixed",     "Hydro turbine discharge ratio (CONUS=10; all others=1)"),
    "DAMCAPRAT":   (0.583,        "fixed",     "Hydro dam capacity / annual output"),
    "DAYBASHYD":   (360.0,        "fixed",     "Baseload hydro storage days"),
    # --- Demand response ---
    "MXHRDRM":     (11.0,         "fixed",     "Max demand-response shift hours (CONUS=11; all others=8)"),
    # --- Thermal storage and demand response ---
    "COOLSTES":    (0.4,          "fixed",     "Fraction AC from CW-STES vs ice"),
    "PHSMIN":      (0.016,        "fixed",     "Min PHS nameplate capacity (TW)"),
    "FHEATFLX":    (0.15,         "fixed",     "Flexible heat load fraction"),
    "FCOLDFLX":    (0.15,         "fixed",     "Flexible cold load fraction"),
    "FRSTORINIT":  (0.5,          "fixed",     "Initial storage fill fraction"),
    "FDISTHEAT":   (0.2,          "fixed",     "District heating fraction"),
    # --- Heat pump and health ---
    "CPERFORM":    (4.0,          "cop",       "Heat pump COP (kWh-th/kWh-el)"),
    "HCDDADD":     (1.0,          "fixed",     "HDD/CDD daily minimum (numerical safeguard)"),
    "FMORTBAU":    (0.9,          "fixed",     "BAU air-pollution mortality fraction"),
    # --- Hot-water, H2, heat battery ---
    "HWFAC":       (1.0,          "fixed",     "HW-STES charge rate factor"),
    "FCDISCH":     (0.091,        "tw",        "H2 fuel-cell discharge rate (TW)"),
    "FCCHARG":     (0.091,        "tw",        "H2 electrolyser charge rate (TW)"),
    # STORHHFC is only meaningful when IMERGH2=2 (separate grid/non-grid H2 storage).
    # The model runs with IMERGH2=1 (merged), where STORHHFC is initialised to 0 and
    # never read back — optimising it has no effect.  Locked to avoid wasting GA budget.
    "STORHHFC":    (0.0,          "fixed",     "H2 elec storage hours [inert: IMERGH2=1 overrides to 0]"),
    # HBTDISCH is overwritten at runtime by HOTINDDEM (industrial hi-temp heat demand)
    # regardless of the value written to fortran_factors.dat.  Same applies to STORHHBT
    # (capacity = HBTDISCH × STORHHBT).  Locked so plots and GA reflect reality.
    "HBTDISCH":    (0.0,          "fixed",     "Heat battery discharge rate [inert: overwritten by HOTINDDEM]"),
    "STORHHBT":    (15.0,         "fixed",     "Heat battery storage hours [inert: depends on HBTDISCH override]"),
    # --- Industrial heat flexibility ---
    "FRCIHFLEX":   (0.5,          "fixed",     "Flexible industrial heat fraction"),
}

FACTOR_KEYS: List[str] = [k for k, (_, cat, _) in PARAM_REGISTRY.items() if cat != "fixed"]

_FORTRAN_SRC = Path("fortran/src/powerworld.f")


def _parse_hardcoded_int(text: str, name: str) -> int:
    """Return the value of an active (uncommented) integer assignment in powerworld.f."""
    # Match non-comment lines with exactly this variable name assigned
    m = re.search(
        r"^(?!C)\s+" + re.escape(name) + r"\s*=\s*(\d+)",
        text,
        re.MULTILINE | re.IGNORECASE,
    )
    return int(m.group(1)) if m else 0


def extract_fortran_region_defaults(region: str) -> Dict[str, float]:
    """Parse powerworld.f for the runtime default factor values of a region.

    Correctly evaluates IF/ELSEIF/ELSE/ENDIF blocks conditioned on IMERGH2 and
    IFEGS (both hardcoded in powerworld.f) so extracted values reflect actual
    runtime behaviour.  Only the branch that executes at runtime contributes
    assignments; the ELSE branch of an IF-chain is skipped when a prior branch
    already matched.  Unknown conditionals (not on IMERGH2 or IFEGS, e.g.
    FRCLDEGS, IFNEWLOAD) are treated as opaque blocks whose assignments are
    ignored — this prevents nested sub-branches from overwriting values set by
    the enclosing recognised branch.  Any key not found falls back to the
    PARAM_REGISTRY default.
    """
    text = _FORTRAN_SRC.read_text()

    # Global hardcoded control variables
    imergh2 = _parse_hardcoded_int(text, "IMERGH2")
    ifegs   = _parse_hardcoded_int(text, "IFEGS")

    # Find the region block
    block_re = re.compile(
        r"GRIDUSE\.EQ\.'{}'\s*\)(.*?)"
        r"(?=ELSEIF\s*\(GRIDUSE\.EQ\.|C\s+ENDIF\s+GRIDUSE)".format(
            re.escape(region)
        ),
        re.DOTALL | re.IGNORECASE | re.MULTILINE,
    )
    m = block_re.search(text)
    if not m:
        print(
            "  [WARN] No region block found for '{}' in powerworld.f; "
            "using PARAM_REGISTRY defaults.".format(region)
        )
        return {k: PARAM_REGISTRY[k][0] for k in FACTOR_KEYS}

    block = m.group(1)

    # --- branch-aware line-by-line parse ---
    # cond_stack: list of [is_active, any_branch_matched]
    #   is_active         – this frame's branch is currently executing
    #   any_branch_matched – some branch in this IF-chain already matched
    cond_stack: list = []

    # Tracks nesting depth of IF blocks whose condition variable is NOT in
    # _ctrl_val (e.g. FRCLDEGS, IFNEWLOAD).  These opaque blocks are skipped:
    # we neither push a cond_stack frame nor record assignments inside them,
    # so they cannot overwrite values set by recognised IMERGH2/IFEGS branches.
    unrecognized_depth: int = 0

    def _currently_active():
        return all(frame[0] for frame in cond_stack)

    # Regex for recognising known conditionals (IMERGH2 or IFEGS .EQ. / .NE. N)
    _IF_RE     = re.compile(r"IF\s*\(\s*(IMERGH2|IFEGS)\s*\.(EQ|NE)\.\s*(\d+)\s*\)\s*THEN",
                             re.IGNORECASE)
    _ELIF_RE   = re.compile(r"ELSEIF\s*\(\s*(IMERGH2|IFEGS)\s*\.(EQ|NE)\.\s*(\d+)\s*\)\s*THEN",
                             re.IGNORECASE)
    # Compound OR conditional on known variables: IF (VAR.OP.N1.OR.VAR.OP.N2) THEN
    _IF_OR_RE  = re.compile(
        r"IF\s*\(\s*(IMERGH2|IFEGS)\s*\.(EQ|NE)\.\s*(\d+)\s*\.OR\."
        r"\s*(IMERGH2|IFEGS)\s*\.(EQ|NE)\.\s*(\d+)\s*\)\s*THEN",
        re.IGNORECASE,
    )
    _ELIF_OR_RE = re.compile(
        r"ELSEIF\s*\(\s*(IMERGH2|IFEGS)\s*\.(EQ|NE)\.\s*(\d+)\s*\.OR\."
        r"\s*(IMERGH2|IFEGS)\s*\.(EQ|NE)\.\s*(\d+)\s*\)\s*THEN",
        re.IGNORECASE,
    )
    # Plain IF(...) THEN only (not ELSEIF) — used to open unrecognized blocks
    _ANY_PLAIN_IF_RE = re.compile(r"^IF\s*\(.*\)\s*THEN\b", re.IGNORECASE)
    # Any ELSEIF(...) THEN — used to detect unrecognized continuation branches
    _ANY_ELIF_RE = re.compile(r"^ELSEIF\s*\(.*\)\s*THEN\b", re.IGNORECASE)
    _ELSE_RE   = re.compile(r"^ELSE\b", re.IGNORECASE)
    _ENDIF_RE  = re.compile(r"^ENDIF\b", re.IGNORECASE)
    _ASSIGN_RE = re.compile(
        r"^\s+([A-Za-z]\w*)\s*=\s*([-+]?(?:\d+\.?\d*|\d*\.\d+)(?:[Ee][-+]?\d+)?)\s*$"
    )

    _ctrl_val = {"IMERGH2": imergh2, "IFEGS": ifegs}

    result: Dict[str, float] = {}
    _factor_set = set(FACTOR_KEYS)

    for raw_line in block.splitlines():
        stripped = raw_line.strip()

        # Skip blank lines and Fortran comments
        if not stripped or stripped.upper().startswith("C"):
            continue

        su = stripped.upper()

        # IF (...) THEN on a known variable — only when not inside an opaque block
        mif = _IF_RE.match(su)
        if mif and unrecognized_depth == 0:
            var, op, val = mif.group(1).upper(), mif.group(2).upper(), int(mif.group(3))
            outer_ok = _currently_active()
            cond_true = (_ctrl_val[var] == val) if op == "EQ" else (_ctrl_val[var] != val)
            branch_ok = outer_ok and cond_true
            cond_stack.append([branch_ok, branch_ok])
            continue

        # IF (VAR.OP.N1.OR.VAR.OP.N2) THEN — compound OR on known variables
        mif_or = _IF_OR_RE.match(su)
        if mif_or and unrecognized_depth == 0:
            var1, op1, val1 = mif_or.group(1).upper(), mif_or.group(2).upper(), int(mif_or.group(3))
            var2, op2, val2 = mif_or.group(4).upper(), mif_or.group(5).upper(), int(mif_or.group(6))
            outer_ok = _currently_active()
            cond1 = (_ctrl_val[var1] == val1) if op1 == "EQ" else (_ctrl_val[var1] != val1)
            cond2 = (_ctrl_val[var2] == val2) if op2 == "EQ" else (_ctrl_val[var2] != val2)
            branch_ok = outer_ok and (cond1 or cond2)
            cond_stack.append([branch_ok, branch_ok])
            continue

        # ELSEIF (...) THEN on a known variable — must be inside an open frame
        melif = _ELIF_RE.match(su)
        if melif and cond_stack and unrecognized_depth == 0:
            var, op, val = melif.group(1).upper(), melif.group(2).upper(), int(melif.group(3))
            already = cond_stack[-1][1]
            outer_ok = all(frame[0] for frame in cond_stack[:-1])
            cond_true = (_ctrl_val[var] == val) if op == "EQ" else (_ctrl_val[var] != val)
            branch_ok = outer_ok and (not already) and cond_true
            if branch_ok:
                cond_stack[-1][1] = True
            cond_stack[-1][0] = branch_ok
            continue

        # ELSEIF (VAR.OP.N1.OR.VAR.OP.N2) THEN — compound OR on known variables
        melif_or = _ELIF_OR_RE.match(su)
        if melif_or and cond_stack and unrecognized_depth == 0:
            var1, op1, val1 = melif_or.group(1).upper(), melif_or.group(2).upper(), int(melif_or.group(3))
            var2, op2, val2 = melif_or.group(4).upper(), melif_or.group(5).upper(), int(melif_or.group(6))
            already = cond_stack[-1][1]
            outer_ok = all(frame[0] for frame in cond_stack[:-1])
            cond1 = (_ctrl_val[var1] == val1) if op1 == "EQ" else (_ctrl_val[var1] != val1)
            cond2 = (_ctrl_val[var2] == val2) if op2 == "EQ" else (_ctrl_val[var2] != val2)
            branch_ok = outer_ok and (not already) and (cond1 or cond2)
            if branch_ok:
                cond_stack[-1][1] = True
            cond_stack[-1][0] = branch_ok
            continue

        # Unknown plain IF(...) THEN — opens a new opaque block
        if _ANY_PLAIN_IF_RE.match(su):
            unrecognized_depth += 1
            continue

        # Unknown ELSEIF(...) THEN — continuation of an opaque block, no depth change
        if _ANY_ELIF_RE.match(su):
            continue

        # ELSE — active only when no prior branch in this chain matched,
        # and only when we are not inside an opaque unrecognized block
        if _ELSE_RE.match(su) and unrecognized_depth == 0 and cond_stack:
            already = cond_stack[-1][1]
            outer_ok = all(frame[0] for frame in cond_stack[:-1])
            cond_stack[-1][0] = outer_ok and not already
            continue

        # ENDIF — close the innermost frame (unrecognized depth first, then cond_stack)
        if _ENDIF_RE.match(su):
            if unrecognized_depth > 0:
                unrecognized_depth -= 1
            elif cond_stack:
                cond_stack.pop()
            continue

        # Assignment — record only when in an active context and not inside
        # an unrecognized (opaque) conditional block
        if unrecognized_depth == 0 and _currently_active():
            ma = _ASSIGN_RE.match(raw_line)
            if ma:
                key = ma.group(1).upper()
                if key in _factor_set:
                    result[key] = float(ma.group(2))

    # Fill any key not found in the block with the PARAM_REGISTRY default
    for k in FACTOR_KEYS:
        if k not in result:
            result[k] = PARAM_REGISTRY[k][0]

    found = [k for k in FACTOR_KEYS if result[k] != PARAM_REGISTRY[k][0]]
    print(
        "Extracted {} region-specific defaults from powerworld.f for '{}' "
        "(keys differ from registry: {}).".format(len(found), region, found or "none")
    )
    return result


# The original 7 capacity-scaling factors (used to decide what inflate_until_feasible touches)
CAPACITY_FACTOR_KEYS = [
    "FACONWIN", "FACOFFWIN", "FACUTILPV", "FACRESPV",
    "FACCOMPV", "CSPTURBFAC", "FACSHT",
]
# Electric-only subset: scale these first during infeasibility expansion since
# electric-sector shortfalls are far more common than heat-sector shortfalls.
ELECTRIC_CAPACITY_FACTOR_KEYS = [k for k in CAPACITY_FACTOR_KEYS if k != "FACSHT"]

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


BASELINE_FILE = Path("data/raw/baseline_results.dat")

# Map from baseline_results.dat labels to PARAM_REGISTRY keys.
# Values that appear on multi-value lines need a positional index.
_BASELINE_PARSE_MAP = {
    # (line_prefix, column_index) -> registry key
    # Line 1: FACONWIN  FACOFFWIN  FACROOFPV  = v0 v1 v2
    ("FACONWIN", 0): "FACONWIN",
    ("FACONWIN", 1): "FACOFFWIN",
    # Line 2: FACRESPV  FACCOMPV   FACUTILPV  = v0 v1 v2
    ("FACRESPV", 0): "FACRESPV",
    ("FACRESPV", 1): "FACCOMPV",
    ("FACRESPV", 2): "FACUTILPV",
    # Line 3: CSPTURBFAC CSPCHARFAC   FACSHT  = v0 v1 v2
    ("CSPTURBFAC", 0): "CSPTURBFAC",
    ("CSPTURBFAC", 2): "FACSHT",
    # HCHARCSP line
    ("HCHARCSP", 0): "HCHARCSP",
    # STORHPHS line
    ("STORHPHS", 0): "STORHPHS",
    # STORHCOLD line
    ("STORHCOLD", 0): "STORHCOLD",
    # STORHBAT  BATDISCH  STORBTWH
    ("STORHBAT", 0): "STORHBAT",
    ("STORHBAT", 1): "BATDISCH",
    # STORHHFC  H2SDISCH  STORFTWH
    ("STORHHFC", 0): "STORHHFC",
    # FCCHARG  FCDISCH
    ("FCCHARG", 0): "FCCHARG",
    ("FCCHARG", 1): "FCDISCH",
    # STORHHBT  HBTDISCH  STOHBTWH
    ("STORHHBT", 0): "STORHHBT",
    ("STORHHBT", 1): "HBTDISCH",
    # STORHHWAT HOTDISCH  STORHTWH
    ("STORHHWAT", 0): "STORHHWAT",
    # STORUGDYS  TWINUTES  UTESCHARG
    ("STORUGDYS", 0): "STORUGDYS",
    # WARMMAX-TW UGFAC  MXHRDRM
    ("WARMMAX-TW", 1): "UGFAC",
    ("WARMMAX-TW", 2): "MXHRDRM",
    # H2STORMX-TWH HWFAC HPSIZE-TW
    ("H2STORMX-TWH", 1): "HWFAC",
    # DAYH2STOR
    ("DAYH2STOR", 0): "DAYH2STOR",
    # DAMCAPRAT
    ("DAMCAPRAT", 0): "DAMCAPRAT",
    # HPTURBRAT
    ("HPTURBRAT", 0): "HPTURBRAT",
}


def parse_baseline_factors(path: Path) -> Dict[str, float]:
    """Parse baseline_results.dat and return a dict of PARAM_REGISTRY keys to values.

    Only parameters that have a mapping in _BASELINE_PARSE_MAP are returned.
    Remaining parameters should be filled from PARAM_REGISTRY defaults.
    """
    factors: Dict[str, float] = {}
    with open(path, "rb") as f:
        raw = f.read().decode("ascii", errors="replace")

    for line in raw.replace("\r", "").split("\n"):
        line = line.strip()
        if not line or "=" not in line:
            continue
        left, right = line.split("=", 1)
        prefix = left.split()[0] if left.split() else ""
        values = right.split()
        for (pfx, col_idx), reg_key in _BASELINE_PARSE_MAP.items():
            if pfx == prefix and col_idx < len(values):
                try:
                    factors[reg_key] = float(values[col_idx])
                except ValueError:
                    pass
    return factors


def load_baseline_start(path: Path) -> Dict[str, float]:
    """Build a complete optimised-param dict from a baseline file.

    Accepts two formats:
    1. Simple KEY = VALUE format (same as fortran_factors.dat) — preferred for
       new region files created manually.
    2. Legacy multi-value format from the original CONUS publication
       (e.g. 'STORHBAT BATDISCH = 4.0 0.84').

    In both cases, only FACTOR_KEYS (non-fixed) params are loaded from the
    file.  Fixed params are intentionally excluded — write_factor_files()
    injects them at their PARAM_REGISTRY defaults so that all four comparison
    cases (baseline, LP, GA-bl, GA-LP) use identical fixed-param values in
    every Fortran evaluation.
    """
    _factor_key_set = set(FACTOR_KEYS)

    # Try the simple KEY = VALUE format first.
    # read_dat returns a dict; check how many keys match PARAM_REGISTRY.
    simple_raw = read_dat(str(path))
    simple_hits = {k.upper(): float(v) for k, v in simple_raw.items()
                   if k.upper() in PARAM_REGISTRY}

    if len(simple_hits) >= 5:
        # Looks like a plain fortran_factors.dat-style file.
        full = {k: PARAM_REGISTRY[k][0] for k in FACTOR_KEYS}
        # Only overlay FACTOR_KEYS values — skip fixed params from the file.
        full.update({k: v for k, v in simple_hits.items() if k in _factor_key_set})
        parsed = {k: v for k, v in simple_hits.items() if k in _factor_key_set}
    else:
        # Fall back to legacy multi-value publication format.
        raw_parsed = parse_baseline_factors(path)
        full = {k: PARAM_REGISTRY[k][0] for k in FACTOR_KEYS}
        full.update({k: v for k, v in raw_parsed.items() if k in _factor_key_set})
        parsed = {k: v for k, v in raw_parsed.items() if k in _factor_key_set}

    print(f"Loaded {len(parsed)} baseline factors from {path}")
    for k, v in sorted(parsed.items()):
        default = PARAM_REGISTRY[k][0]
        marker = "" if abs(v - default) < 1e-9 else " *"
        print(f"  {k:12s} = {v:.6f}  (default {default:.6f}){marker}")
    return full


# ---------------------------------------------------------------------------
# Fortran I/O helpers
# ---------------------------------------------------------------------------

def _run_fortran_raw(cmd, paths):
    """Run a Fortran subprocess, capture stdout/stderr, write logs, return stdout."""
    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
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


def preprocess_region(region):
    """Run the three-step preprocessing (IFREWRITE=1,2,3) for a new region.

    Prerequisites in data/raw/:
      - wwssupworld.<REGION>  (raw supply file from GATOR-GCMOM)

    Steps performed:
      1. IFREWRITE=1 : reformats wwssupworld.<REGION> → wwssupreform.dat
      2. IFREWRITE=2 : aggregates to wwssupworld.<REGION> (compressed form)
      3. IFREWRITE=3 : normal model run (also creates wwsmonthly/wwshourly/pkflex)

    Skipped entirely if wwsmonthly.<REGION> already exists (already preprocessed).
    Only step 1+2 are run if wwssupworld.<REGION> exists but wwsmonthly.<REGION> doesn't.
    """
    monthly_file  = BASE_RAW_DIR / "wwsmonthly.{}".format(region)
    supply_agg    = BASE_RAW_DIR / "wwssupworld.{}".format(region)
    supply_raw    = BASE_RAW_DIR / "wwssupworld.dat"

    if monthly_file.exists():
        return  # already fully preprocessed

    if not supply_agg.exists():
        # Need to run IFREWRITE=1 first to create wwssupreform.dat, then IFREWRITE=2
        if not supply_raw.exists():
            raise FileNotFoundError(
                "Cannot preprocess region '{}': neither\n"
                "  {}\n  (aggregated, from a previous IFREWRITE=2 run)\nnor\n"
                "  {}\n  (raw supply file, rename wwssupworld.{} to this)\n"
                "exists in data/raw/.".format(
                    region, supply_agg, supply_raw, region
                )
            )
        print("Preprocessing {}: IFREWRITE=1 (reformat raw supply) ...".format(region))
        paths = _region_paths(region)
        result = subprocess.run(
            [str(FORTRAN_EXE), region, "1"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
        )
        if result.returncode != 0:
            raise RuntimeError("IFREWRITE=1 failed for {}:\n{}".format(region, result.stderr))

        print("Preprocessing {}: IFREWRITE=2 (aggregate by region) ...".format(region))
        result = subprocess.run(
            [str(FORTRAN_EXE), region, "2"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
        )
        if result.returncode != 0:
            raise RuntimeError("IFREWRITE=2 failed for {}:\n{}".format(region, result.stderr))

    # wwssupworld.<REGION> now exists — proceed to IFREWRITE=3 (normal run).
    # wwsmonthly.*, wwshourly.*, pkflex.* will be created by that run.
    print(
        "Preprocessed supply file wwssupworld.{} found — skipping IFREWRITE=1,2.\n"
        "  (wwsmonthly.{r}, wwshourly.{r}, pkflex.{r} will be created by the model run.)".format(
            region, r=region
        )
    )


def run_fortran(region=_DEFAULT_REGION, paths=None):
    preprocess_region(region)
    if paths is None:
        paths = _region_paths(region)
    cmd = [str(FORTRAN_EXE)]
    if region != _DEFAULT_REGION:
        cmd.append(region)
    return _run_fortran_raw(cmd, paths)


def write_factor_files(factors, paths=None):
    """Write factor files to all three Fortran input paths.

    Only non-fixed params (FACTOR_KEYS) are written.  Fixed params (HPTURBRAT,
    MXHRDRM, DAMCAPRAT, DAYBASHYD, UGFAC, …) are intentionally omitted so that
    Fortran falls back to its own region-specific hardcoded values — which differ
    from the CONUS defaults stored in PARAM_REGISTRY (e.g. HPTURBRAT=1.0 for all
    non-US regions vs 10.0 for CONUS; MXHRDRM=8 for non-US vs 11 for CONUS).

    This keeps serial evaluations (baseline, LP-eval, GA final) consistent with
    the parallel workspace evaluations in run_fortran_worker, which also write
    only FACTOR_KEYS to their workspace-local factor files."""
    _factor_key_set = set(FACTOR_KEYS)
    to_write = {k: v for k, v in factors.items() if k in _factor_key_set}
    # Only write to the two paths that Fortran reads at runtime.
    # paths["factor_result"] (data/results_python/{region}/fortran_factors.dat) is
    # the LP solver output file; writing GA factors there would corrupt the LP
    # factors that run_ga_from_lp_workflow reads after run_ga completes.
    factor_paths = [
        paths["factor_dest"],
        paths["factor_pathhome"],
    ] if paths else FACTOR_PATHS
    for path in factor_paths:
        write_dat(to_write, path)


def _read_factor_file(path: Path) -> Dict[str, float]:
    if not path.exists():
        return {}
    data = read_dat(str(path))
    return {k.lower(): float(v) for k, v in data.items()}


def _verify_factor_files(factors: Dict[str, float], factor_paths=None):
    if factor_paths is None:
        factor_paths = FACTOR_PATHS
    expected = {k.lower(): float(v) for k, v in factors.items()}
    for path in factor_paths:
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

def log_candidate(factors, feasible, cost, label="candidate", history_file=None):
    if history_file is None:
        history_file = HISTORY_FILE
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


def parse_cost(stdout):
    match = _ANNUAL_COST_PATTERN.search(stdout)
    if not match:
        return float("inf")
    try:
        return float(match.group(2))
    except (ValueError, IndexError):
        return float("inf")


def evaluate_factors(factors, label="candidate", region=_DEFAULT_REGION, paths=None):
    if paths is None:
        paths = _region_paths(region)
    with _fortran_global_lock():
        write_factor_files(factors, paths)
        stdout = run_fortran(region=region, paths=paths)
    feasible = check_feasibility(stdout)
    cost = parse_cost(stdout)
    log_candidate(factors, feasible, cost, label, history_file=paths["history_file"])
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


def inflate_until_feasible(base_factors, initial_step=0.1, growth=1.5, max_attempts=25,
                           region=_DEFAULT_REGION, paths=None):
    if paths is None:
        paths = _region_paths(region)
    candidate = base_factors.copy()
    step = initial_step

    # Phase 1: raise sub-unity capacity factors toward 1.0
    for attempt in range(1, max_attempts + 1):
        candidate, changed = raise_subunity_factors(candidate, step)
        label = "inflate-subunity{}".format(attempt)
        feasible, cost, stdout = evaluate_factors(candidate, label=label, region=region, paths=paths)
        if feasible:
            return candidate, cost, stdout
        if not changed:
            break

    # Ensure all capacity factors are at least 1.0 before scaling
    for key in CAPACITY_FACTOR_KEYS:
        candidate[key] = max(1.0, candidate.get(key, 1.0))

    # Phase 2A: scale only electric generation factors (wind/PV/CSP).
    # Electric shortfalls are much more common than heat shortfalls; targeting
    # electric factors first avoids inflating solar-thermal unnecessarily.
    # Uses its own step schedule, independent of Phase 2B.
    half = max(max_attempts // 2, 4)
    step_elec = initial_step
    for attempt in range(1, half + 1):
        for key in ELECTRIC_CAPACITY_FACTOR_KEYS:
            val = candidate.get(key, 1.0)
            candidate[key] = max(MIN_FACTOR, val * (1.0 + step_elec))
        label = "inflate-electric{}".format(attempt)
        feasible, cost, stdout = evaluate_factors(candidate, label=label, region=region, paths=paths)
        if feasible:
            return candidate, cost, stdout
        step_elec *= growth

    # Phase 2B: scale all 7 capacity factors together (electric + heat).
    # Gets a full max_attempts budget with a fresh step schedule so that a heat-
    # sector shortfall (requiring FACSHT inflation) never runs out of attempts.
    step_all = initial_step
    for attempt in range(1, max_attempts + 1):
        candidate = inflate_factors(candidate, step_all)
        label = "inflate-all{}".format(attempt)
        feasible, cost, stdout = evaluate_factors(candidate, label=label, region=region, paths=paths)
        if feasible:
            return candidate, cost, stdout
        step_all *= growth

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
        # Skip fortran_factors.dat (written per-worker) and all Fortran OUTPUT
        # files that the model writes during each run.  Symlinking output files
        # causes all parallel workers to write through the same physical file,
        # corrupting every worker's state simultaneously.
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
        if item.is_dir():
            os.symlink(str(item), str(target), target_is_directory=True)
        else:
            os.symlink(str(item), str(target))
    return workspace_path, data_raw


def run_fortran_worker(label, factors, region=_DEFAULT_REGION):
    workspace, data_raw = prepare_workspace()
    try:
        write_dat(factors, data_raw / "fortran_factors.dat")
        cmd = [str(FORTRAN_EXE)]
        if region != _DEFAULT_REGION:
            cmd.append(region)
        result = subprocess.run(
            cmd,
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
    region=_DEFAULT_REGION,
    paths=None,
):
    if paths is None:
        paths = _region_paths(region)
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
                trial_specs, parallel_evals, region=region, paths=paths
            )
        else:
            evaluation_results = evaluate_trials_sequential(trial_specs, region=region, paths=paths)

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


def evaluate_trials_sequential(specs, region=_DEFAULT_REGION, paths=None):
    if paths is None:
        paths = _region_paths(region)
    results = []
    for spec in specs:
        feasible, cost, _ = evaluate_factors(spec["factors"], label=spec["label"],
                                             region=region, paths=paths)
        results.append({"feasible": feasible, "cost": cost})
    return results


def evaluate_trials_parallel(specs, max_workers, region=_DEFAULT_REGION, paths=None):
    if paths is None:
        paths = _region_paths(region)
    results = []
    with ProcessPoolExecutor(max_workers=max_workers) as pool:
        futures = [
            pool.submit(run_fortran_worker, spec["label"], spec["factors"], region)
            for spec in specs
        ]
        for spec, future in zip(specs, futures):
            label, factors, output = future.result()
            feasible = check_feasibility(output)
            cost = parse_cost(output)
            log_candidate(factors, feasible, cost, label, history_file=paths["history_file"])
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
    region: str = _DEFAULT_REGION,
    paths: dict = None,
) -> Tuple[Dict[str, float], float]:
    if paths is None:
        paths = _region_paths(region)
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
            evals = evaluate_trials_parallel(specs, parallel_evals, region=region, paths=paths)
        else:
            evals = evaluate_trials_sequential(specs, region=region, paths=paths)

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
    """Build a complete optimised-param dict from LP DAT output.
    Only FACTOR_KEYS (non-fixed) params are accepted from lp_factors.
    Fixed params are intentionally excluded — write_factor_files()
    injects them at their PARAM_REGISTRY defaults."""
    _factor_key_set = set(FACTOR_KEYS)
    all_factors = {k: PARAM_REGISTRY[k][0] for k in FACTOR_KEYS}
    lp_upper = {k.upper(): float(v) for k, v in lp_factors.items()}
    all_factors.update({k: v for k, v in lp_upper.items() if k in _factor_key_set})
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
    baseline_start=None,
    generate_plots=True,
):
    paths = _region_paths(region)
    paths["results_dir"].mkdir(parents=True, exist_ok=True)
    paths["lp_summary"].parent.mkdir(parents=True, exist_ok=True)

    # ── Clear factor history so each run starts fresh ─────────────────────────
    if paths["history_file"].exists():
        paths["history_file"].unlink()
        print("Cleared previous factor history: {}".format(paths["history_file"]))

    # ── Parse canonical baseline output if present ────────────────────────────
    # The file data/raw/xxEGS.<region> is the Jacobson publication baseline.
    # It is never modified by this workflow; we only read and summarise it.
    _canonical_bl = BASE_RAW_DIR / "xxEGS.{}".format(region)
    if _canonical_bl.exists():
        print(f"Parsing canonical baseline: {_canonical_bl.name}")
        _parse_and_save(
            _canonical_bl.read_text(),
            factors=None,
            region=region,
            run_type="canonical_baseline",
            out_path=paths["results_dir"] / "canonical_baseline_summary.json",
        )
    else:
        print(f"  [INFO] No canonical baseline found at {_canonical_bl} — skipping.")

    if baseline_start == "defaults":
        base_factors = extract_fortran_region_defaults(region)
        print("Using Fortran hardcoded region defaults as baseline factors (skipping LP).")
    elif baseline_start:
        baseline_path = Path(baseline_start)
        if not baseline_path.exists():
            raise FileNotFoundError(f"Baseline file not found: {baseline_path}")
        print(f"Using baseline factors from {baseline_path} (skipping LP).")
        base_factors = load_baseline_start(baseline_path)
    else:
        run_python_model.main(region=region, output_dir=paths["lp_summary"].parent)
        export_fortran_factors.main([
            "--summary", str(paths["lp_summary"]),
            "--output", str(paths["factor_result"]),
        ])
        lp_factors = read_dat(str(paths["factor_result"]))
        base_factors = _build_full_factors(lp_factors)
    baseline_paths = dict(paths)
    baseline_paths["fortran_out"] = paths["fortran_baseline_out"]
    with _fortran_global_lock():
        if baseline_start == "defaults":
            # Let Fortran use its hardcoded regional values — delete any stale factor
            # file so READ_FACTOR_OVERRIDES exits early and nothing is overridden.
            for _fp in [paths["factor_dest"], paths["factor_pathhome"]]:
                if _fp.exists():
                    _fp.unlink()
        else:
            write_factor_files(base_factors, paths)
        # Run the baseline Fortran evaluation, writing output to fortran_baseline_run.out
        # so that fortran_last_run.out is reserved for the final optimal evaluation.
        stdout = run_fortran(region=region, paths=baseline_paths)
    print("Baseline Fortran output written to {}".format(paths["fortran_baseline_out"]))

    # Parse and save the baseline summary for all warm-start modes.
    _parse_and_save(
        stdout,
        factors=base_factors,
        region=region,
        run_type="baseline",
        out_path=paths["baseline_summary"],
    )
    print(
        "Compare {} against data/raw/xxEGS.{} to verify correctness.".format(
            paths["fortran_baseline_out"], region
        )
    )

    feasible_initial = check_feasibility(stdout)
    initial_cost = parse_cost(stdout)
    log_candidate(base_factors, feasible_initial, initial_cost, label="LP",
                  history_file=paths["history_file"])
    if feasible_initial and baseline_start is None:
        # LP solution is already feasible — no further optimisation needed.
        print(
            "Fortran verification succeeded with LP factors (cost {:.3f}).".format(
                initial_cost
            )
        )
        return
    if not feasible_initial and baseline_start == "defaults":
        print("Default baseline factors are infeasible for region '{}'; "
              "inflating capacity factors before starting optimiser.".format(region))

    if feasible_initial:
        # baseline_start provided and feasible — use it directly as starting point.
        print(
            "Baseline feasible (cost {:.3f}). Proceeding to {} optimisation.".format(
                initial_cost, optimizer.upper()
            )
        )
        candidate, cost = base_factors.copy(), initial_cost
    else:
        print("Starting point infeasible; inflating capacity factors.")
        candidate, cost, _ = inflate_until_feasible(base_factors, region=region, paths=paths)

    print(
        "{} starting from feasible point (cost {:.3f}).".format(
            "Genetic algorithm" if optimizer == "ga" else "Hooke-Jeeves", cost
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
            region=region,
            paths=paths,
        )
        print("GA produced best feasible solution (cost {:.3f}).".format(best_cost))
        write_dat(best_factors, paths["results_dir"] / "genetic_factors.dat")
    else:
        best_factors, best_cost = hooke_jeeves_search(
            candidate,
            cost,
            initial_step=hj_initial_step,
            shrink=hj_shrink,
            max_iter=hj_max_iter,
            min_step=hj_min_step,
            parallel_evals=parallel_evals,
            direction=hj_direction,
            locked_factors=hj_locked_factors,
            region=region,
            paths=paths,
        )
        print("Hooke-Jeeves produced best feasible solution (cost {:.3f}).".format(best_cost))
        write_dat(best_factors, paths["results_dir"] / "hooke_jeeves_factors.dat")

    # ── Final evaluation with optimal factors ─────────────────────────────────
    # Run Fortran once more with the best-found factors so that:
    #   1. fortran_optimal_run.out is always present for plot_results.py
    #   2. optimal_summary.json captures the full structured results
    # The raw .out file and JSON are both saved; nothing is deleted.
    print("Running final Fortran evaluation with optimal factors...")
    with _fortran_global_lock():
        write_factor_files(best_factors, paths)
        final_stdout = run_fortran(region=region, paths=paths)
    paths["fortran_optimal_out"].write_text(final_stdout)
    _parse_and_save(
        final_stdout,
        factors=best_factors,
        region=region,
        run_type=f"{optimizer}_optimal",
        out_path=paths["optimal_summary"],
    )
    print(
        "Final evaluation cost: {:.3f} $B/yr  →  {}".format(
            parse_cost(final_stdout), paths["optimal_summary"]
        )
    )

    # ── Generate plots ────────────────────────────────────────────────────────
    if generate_plots:
        print("Generating plots for region {}...".format(region))
        try:
            import scripts.plot_results as _plot_results  # lazy to avoid circular import
            _plot_results.main(region=region)
            print("Plots saved to {}".format(paths["results_dir"]))
        except Exception as exc:
            print("  [WARN] Plot generation failed: {}".format(exc))
    else:
        print("Skipping plot generation (--no-plots). "
              "Run 'python -m scripts.plot_results --region {}' separately.".format(region))


# ---------------------------------------------------------------------------
# LP warm-start GA workflow (separate outputs, does not overwrite baseline-GA)
# ---------------------------------------------------------------------------

def run_ga_from_lp_workflow(
    region="UNITED-STATES",
    parallel_evals=1,
    ga_population=24,
    ga_generations=50,
    ga_mutation_rate=0.15,
    ga_mutation_scale=0.2,
    ga_elite_frac=0.2,
    ga_mutation_cooling=0.98,
    ga_factor_scales=None,
    ga_magnitude_damping=0.5,
    generate_plots=False,
):
    """Run GA starting from LP factors, saving to lp_summary.json and lp_ga_summary.json.

    This function intentionally does NOT overwrite baseline_summary.json or
    optimal_summary.json so that all four cases (baseline, LP-eval, GA-from-baseline,
    GA-from-LP) can coexist for per-region four-case comparison plots.
    """
    paths = _region_paths(region)
    paths["results_dir"].mkdir(parents=True, exist_ok=True)
    paths["lp_summary"].parent.mkdir(parents=True, exist_ok=True)

    # Use a separate history log so LP-GA history doesn't overwrite baseline-GA history.
    paths_ga = dict(paths)
    paths_ga["history_file"] = paths["lp_ga_history_file"]
    if paths_ga["history_file"].exists():
        paths_ga["history_file"].unlink()
        print("Cleared previous LP-GA factor history: {}".format(paths_ga["history_file"]))

    # 1. Load LP factors
    lp_factors_path = paths["factor_result"]
    if not lp_factors_path.exists():
        raise FileNotFoundError(
            f"LP factors not found: {lp_factors_path}. "
            "Run '--run-lp-only' first."
        )
    lp_factors = read_dat(str(lp_factors_path))
    base_factors = _build_full_factors(lp_factors)

    # 2. Evaluate LP solution with Fortran → lp_summary.json
    print(f"Evaluating LP factors with Fortran for region '{region}'...")
    with _fortran_global_lock():
        write_factor_files(base_factors, paths)
        lp_stdout = run_fortran(region=region, paths=paths)
    paths["fortran_lp_out"].write_text(lp_stdout)
    _parse_and_save(
        lp_stdout,
        factors=base_factors,
        region=region,
        run_type="lp_eval",
        out_path=paths["lp_eval_summary"],
    )
    lp_cost = parse_cost(lp_stdout)
    lp_feasible = check_feasibility(lp_stdout)
    log_candidate(base_factors, lp_feasible, lp_cost, label="LP-eval",
                  history_file=paths_ga["history_file"])
    print(f"  LP Fortran evaluation: feasible={lp_feasible}, cost={lp_cost:.3f}")

    if lp_feasible:
        candidate, cost = base_factors.copy(), lp_cost
        # LP itself is the first feasible point — copy its summary
        import shutil as _shutil
        _shutil.copy2(paths["lp_eval_summary"], paths["first_feasible_summary"])
    else:
        print("  LP solution infeasible in Fortran — inflating capacity factors...")
        candidate, cost, ff_stdout = inflate_until_feasible(
            base_factors, region=region, paths=paths_ga)
        _parse_and_save(
            ff_stdout,
            factors=candidate,
            region=region,
            run_type="first_feasible",
            out_path=paths["first_feasible_summary"],
        )

    # 3. GA from LP warm-start
    print(f"Running GA from LP warm-start (population={ga_population}, "
          f"generations={ga_generations})...")
    best_factors, best_cost = genetic_search(
        candidate,
        cost,
        population_size=ga_population,
        generations=ga_generations,
        mutation_rate=ga_mutation_rate,
        mutation_scale=ga_mutation_scale,
        elite_frac=ga_elite_frac,
        direction="both",
        parallel_evals=parallel_evals,
        locked_factors=None,
        mutation_cooling=ga_mutation_cooling,
        factor_scales=ga_factor_scales or {},
        magnitude_damping=ga_magnitude_damping,
        region=region,
        paths=paths_ga,
    )
    print(f"GA-from-LP best cost: {best_cost:.3f}")
    write_dat(best_factors, paths["results_dir"] / "lp_ga_factors.dat")

    # 4. Final Fortran evaluation → lp_ga_summary.json
    print("Running final Fortran evaluation with LP-GA optimal factors...")
    with _fortran_global_lock():
        write_factor_files(best_factors, paths)
        final_stdout = run_fortran(region=region, paths=paths)
    paths["fortran_lp_ga_out"].write_text(final_stdout)
    _parse_and_save(
        final_stdout,
        factors=best_factors,
        region=region,
        run_type="lp_ga_optimal",
        out_path=paths["lp_ga_summary"],
    )
    print(
        "LP-GA final cost: {:.3f}  →  {}".format(
            parse_cost(final_stdout), paths["lp_ga_summary"]
        )
    )

    if generate_plots:
        try:
            import scripts.plot_results as _plot_results
            _plot_results.main(region=region)
        except Exception as exc:
            print(f"  [WARN] Plot generation failed: {exc}")


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
        "--region",
        type=str,
        default=_DEFAULT_REGION,
        metavar="REGION",
        help=(
            "Grid region to simulate (default: UNITED-STATES).  "
            "Must match a GRIDUSE name in powerworld.f, e.g. CHINA, EUROPE, INDIA.  "
            "Results are written to data/results_verification/<REGION>/ for non-US regions."
        ),
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
    parser.add_argument(
        "--baseline-start",
        type=str,
        default=None,
        metavar="PATH|defaults",
        help="Starting point for the GA/HJ optimiser. Three options: "
        "(1) omit: run LP and stop if feasible; "
        "(2) 'defaults': use the hardcoded registry defaults (all factors=1), "
        "run Fortran once as a region baseline, save output as data/raw/xxEGS.<REGION>, "
        "then start the optimiser — use this for a new region with no existing baseline; "
        "(3) PATH to a baseline_results.dat file: load factors from that file and skip LP. "
        "Default: %(default)s.",
    )
    parser.add_argument(
        "--no-plots",
        action="store_true",
        default=False,
        help="Skip figure generation after the GA run. "
             "Useful when plots are handled as a separate Snakemake rule. "
             "Run 'python -m scripts.plot_results --region REGION' to generate figures later.",
    )
    parser.add_argument(
        "--preprocess-only",
        action="store_true",
        default=False,
        help="Run supply-file preprocessing (IFREWRITE=1,2) for the region and exit. "
             "If data/raw/wwssupworld.<REGION> already exists the step is a no-op. "
             "Used by the Snakemake preprocess_supply rule.",
    )
    parser.add_argument(
        "--run-lp-only",
        action="store_true",
        default=False,
        help="Run the LP optimisation and export factors to "
             "data/results_python/<REGION>/fortran_factors.dat, then exit. "
             "Requires a working LP solver (Gurobi/HiGHS). "
             "Used by the Snakemake run_lp rule.",
    )
    parser.add_argument(
        "--run-ga-from-lp",
        action="store_true",
        default=False,
        help="Evaluate LP factors with Fortran (→ lp_summary.json), then run GA "
             "from the LP warm-start (→ lp_ga_summary.json). "
             "Does NOT overwrite baseline_summary.json or optimal_summary.json. "
             "Requires completed '--run-lp-only' output. "
             "Used by the Snakemake run_ga_from_lp rule.",
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

    # ── Early-exit modes used by Snakemake rules ──────────────────────────────

    if args.preprocess_only:
        # Run IFREWRITE=1,2 if wwssupworld.<REGION> is missing; otherwise no-op.
        # The Snakemake preprocess_supply rule touches the sentinel on success.
        preprocess_region(args.region)
        return

    if args.run_lp_only:
        # Run LP optimisation + factor export, then exit.
        # Output: data/results_python/<REGION>/fortran_factors.dat
        #
        # On LP failure (infeasible, no solver, etc.) we write PARAM_REGISTRY
        # defaults for all FACTOR_KEYS to fortran_factors.dat and a placeholder
        # summary.dat so that run_ga_from_lp can always proceed.  This keeps
        # the Snakemake dependency graph unblocked; the lp_summary.json written
        # by run_ga_from_lp will carry lp_failed=1 to flag the fallback.
        paths = _region_paths(args.region)
        paths["lp_summary"].parent.mkdir(parents=True, exist_ok=True)
        try:
            run_python_model.main(region=args.region, output_dir=paths["lp_summary"].parent)
            export_fortran_factors.main([
                "--summary", str(paths["lp_summary"]),
                "--output",  str(paths["factor_result"]),
            ])
            print(f"LP factors written to: {paths['factor_result']}")
        except Exception as exc:
            print(f"[WARN] LP solver failed for {args.region}: {exc}")
            print(f"  Writing PARAM_REGISTRY defaults as fallback to {paths['factor_result']}")
            fallback_factors = {k: PARAM_REGISTRY[k][0] for k in FACTOR_KEYS}
            write_dat(fallback_factors, str(paths["factor_result"]))
            write_dat({"objective_cost": float("inf"), "lp_failed": 1},
                      str(paths["lp_summary"]))
            # Write stub lp_solution.json so Snakemake's declared output is satisfied
            import json as _json
            lp_sol_path = paths["lp_summary"].parent / "lp_solution.json"
            with open(lp_sol_path, "w") as _f:
                _json.dump({"region": args.region, "lp_failed": True,
                            "error": str(exc)}, _f, indent=2)
            print(f"  Fallback factor file written; downstream run_ga_from_lp will proceed.")
        return

    if args.run_ga_from_lp:
        # Evaluate LP factors with Fortran → lp_summary.json
        # Run GA from LP warm-start → lp_ga_summary.json
        factor_scales = _parse_factor_scales(args.ga_factor_scale)
        run_ga_from_lp_workflow(
            region=args.region,
            parallel_evals=max(1, args.parallel_evals),
            ga_population=args.ga_population,
            ga_generations=args.ga_generations,
            ga_mutation_rate=args.ga_mutation_rate,
            ga_mutation_scale=args.ga_mutation_scale,
            ga_elite_frac=args.ga_elite_frac,
            ga_mutation_cooling=args.ga_mutation_cooling,
            ga_factor_scales=factor_scales,
            ga_magnitude_damping=args.ga_magnitude_damping,
            generate_plots=not args.no_plots,
        )
        return

    # ── Normal GA/HJ workflow ─────────────────────────────────────────────────
    factor_scales = _parse_factor_scales(args.ga_factor_scale)
    run_workflow(
        region=args.region,
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
        baseline_start=args.baseline_start,
        generate_plots=not args.no_plots,
    )


if __name__ == "__main__":
    try:
        multiprocessing.set_start_method("spawn")
    except RuntimeError:
        pass
    main()

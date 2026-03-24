# LoadMatch Python–Fortran Workflow

Couples a Python LP with the LoadMatch Fortran simulator (`powerworld.f`).
The LP sizes generation/storage capacities; those targets are converted into
the ~37 multiplier factors expected by Fortran.  If the LP solution is
infeasible, a meta-heuristic (GA or Hooke–Jeeves) iteratively adjusts all
tunable parameters until feasibility is achieved at minimum cost.

## Repository layout

```
loadmatch-python/
├── src/
│   ├── io/              # dat_parser: read/write .dat files
│   └── optimization/    # Pyomo LP model (model_builder.py)
├── scripts/
│   ├── run_full_workflow.py      # Main entry point (LP + Fortran + GA/HJ)
│   ├── parse_fortran_output.py   # Parse Fortran stdout → structured JSON
│   ├── plot_results.py           # Publication figures (run locally after sync)
│   ├── run_python_model.py       # Standalone Python LP
│   ├── export_fortran_factors.py # Convert LP results → fortran_factors.dat
│   ├── factor_history_tools.py   # Parse/plot optimisation history
│   └── run_regions_slurm.sh      # SLURM job-array script for multi-region runs
├── fortran/
│   ├── src/powerworld.f          # LoadMatch Fortran source (~21 000 lines)
│   └── bin/powerworld            # Compiled executable (x86_64 Linux only)
├── data/
│   ├── raw/                      # Canonical inputs (shared across regions)
│   ├── results_python/<REGION>/  # LP outputs + fortran_factors.dat
│   └── results_verification/<REGION>/  # Fortran logs, factor history, summaries
├── requirements.txt
└── README.md
```

## Setup

### 1. Python environment

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Compile the Fortran executable

Must be compiled on **x86_64 Linux** (`-mcmodel=large` is not supported on
Apple Silicon).

```bash
mkdir -p fortran/build fortran/bin
gfortran -O2 -mcmodel=large \
    -fdefault-real-8 -fdefault-double-8 -fno-automatic \
    -J fortran/build \
    fortran/src/powerworld.f \
    -o fortran/bin/powerworld
```

### 3. Input data

All files live in `data/raw/`.

| File | Contents |
|------|----------|
| `countrystats.dat` | Base capacities and statistics for all regions |
| `loadreg.COUNTRY2030GW` | Hourly electricity demand for all countries |
| `heatcooldd.dat` | Heating/cooling degree days |
| `heatfrac.dat` | Monthly heat/cold fractions |
| `wwssupworld.<REGION>` | Hourly supply profiles per region (~470 MB each) |
| `baseline_results.<REGION>.dat` | Published factor values for warm-start (optional) |

The Fortran binary reads `data/raw/` via hardcoded path variables in
`powerworld.f` (around line 1050):

```fortran
PATHHOME = './data/raw/'
PATHTEMP = './data/raw/'
```

---

## Running the workflow

### Single region

```bash
source .venv/bin/activate
python -m scripts.run_full_workflow --region UNITED-STATES [OPTIONS]
```

What this does:
1. Solves the Pyomo LP → `data/results_python/<REGION>/`
   *(skipped when `--baseline-start` is used)*
2. Parses the canonical Fortran baseline → `baseline_summary.json`
3. Runs Fortran with LP/baseline factors; inflates if infeasible
4. Runs GA or Hooke–Jeeves to minimise cost
5. Re-runs Fortran with best factors → `fortran_optimal_run.out` + `optimal_summary.json`

### Output files

Results land in `data/results_verification/<REGION>/`:

| File | Contents |
|------|----------|
| `factor_history.log` | Full trial-by-trial log (all evaluations) |
| `genetic_factors.dat` / `hooke_jeeves_factors.dat` | Best-found factors |
| `fortran_stdout.log`, `fortran_stderr.log` | Fortran console output |
| `fortran_optimal_run.out` | Full Fortran stdout for the best solution |
| `baseline_summary.json` | Structured JSON of the canonical baseline run |
| `optimal_summary.json` | Structured JSON of the GA/HJ optimal run |

The JSON summaries contain ~50 parsed metrics (TWh by source/end-use, storage
losses, annual cost lo/mn/hi, cost by category in c/kWh).

### Recommended GA command (38 CPUs, warm-start)

```bash
python -m scripts.run_full_workflow \
    --region UNITED-STATES \
    --baseline-start data/raw/baseline_results.UNITED-STATES.dat \
    --optimizer ga \
    --parallel-evals 36 \
    --ga-population 36 \
    --ga-generations 80
```

### CLI reference

| Flag | Default | Description |
|------|---------|-------------|
| `--region` | `UNITED-STATES` | Grid region (`GRIDUSE` name in `powerworld.f`) |
| `--optimizer {hj,ga}` | `hj` | Optimiser |
| `--parallel-evals N` | 1 | Parallel Fortran evaluations per generation |
| `--baseline-start PATH` | — | Skip LP; start from a baseline `.dat` file |
| `--hj-lock FACTOR [...]` | `HCDDADD FMORTBAU` | Lock factors during search |
| `--ga-population` | 24 | Population size |
| `--ga-generations` | 50 | Generations |
| `--ga-mutation-rate` | 0.15 | Per-factor mutation probability |
| `--ga-mutation-scale` | 0.2 | Relative mutation magnitude |
| `--ga-elite-frac` | 0.2 | Elite fraction preserved |
| `--ga-mutation-cooling` | 0.98 | Per-generation decay on rate and scale |
| `--hj-initial-step` | 0.2 | Relative step size |
| `--hj-shrink` | 0.7 | Step reduction after no improvement |
| `--hj-max-iter` | 40 | Max iterations |

---

## Multi-region SLURM runs (Stanford Sherlock)

Each region maps to one SLURM array task using multiple CPUs for
intra-GA parallelism.

### Setup on Sherlock

```bash
# Load modules (adjust to current Sherlock module names)
module load python/3.11 gcc/12

# Build the Fortran binary (x86_64, do this once on a compute node)
gfortran -O2 -mcmodel=large -fdefault-real-8 -fdefault-double-8 -fno-automatic \
    -J fortran/build fortran/src/powerworld.f -o fortran/bin/powerworld

# Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Submit a job array

Edit `scripts/run_regions_slurm.sh` — the `CONFIG` block at the top:

```bash
REGIONS=("UNITED-STATES" "EUROPE" "CHINA" "INDIA")
PARALLEL_EVALS=36
GA_POPULATION=36
GA_GENERATIONS=80
BASELINE_DIR="data/raw"          # looks for baseline_results.<REGION>.dat
PYTHON_ENV_SETUP="source .venv/bin/activate"
REPO_ROOT="$HOME/loadmatch-python"
```

Also set `--array=0-3%4` to match `N_REGIONS - 1`:

```bash
sbatch scripts/run_regions_slurm.sh
```

### Plotting on Sherlock

`scripts/plot_results.py` uses `matplotlib.use("Agg")` (set automatically) and
**never calls `plt.show()`**, so it works on headless HPC nodes without a
display.  Run it directly on Sherlock and copy the PDFs locally:

```bash
python -m scripts.plot_results --region UNITED-STATES
scp sherlock:/path/to/repo/data/results_verification/UNITED-STATES/*.pdf .
```

Or sync the full results folder locally and run plotting on your laptop.

---

## Creating a baseline file for a new region

Before the GA can warm-start from a baseline, you need
`data/raw/baseline_results.<REGION>.dat`.

1. **Run Fortran with no factor overrides** (move aside any existing
   `fortran_factors.dat`):
   ```bash
   ./fortran/bin/powerworld EUROPE > /tmp/eu_baseline.log 2>&1
   ```

2. **Find the hardcoded defaults** in `powerworld.f` — search for
   `ELSEIF (GRIDUSE.EQ.'EUROPE')` (around line 1750+).

3. **Write the file** in `KEY = VALUE` format (one param per line):
   ```
   # Europe baseline — from powerworld.f ELSEIF EUROPE block
   FACONWIN   = 1.5
   FACOFFWIN  = 1.0
   FACUTILPV  = 1.2
   ...
   ```
   Unlisted parameters are filled automatically from `PARAM_REGISTRY` defaults.

4. **Verify**:
   ```bash
   python -c "
   from pathlib import Path
   from scripts.run_full_workflow import load_baseline_start
   load_baseline_start(Path('data/raw/baseline_results.EUROPE.dat'))
   "
   ```

---

## Visualising results

Generate all publication figures:

```bash
python -m scripts.plot_results --region UNITED-STATES
```

Figures are saved as `.pdf` and `.png` to `data/results_verification/<REGION>/`.

The script reads `factor_history.log`, `baseline_summary.json`, and
`optimal_summary.json` for the specified region.  Missing files are skipped
with a warning rather than raising an error.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `ModuleNotFoundError: No module named 'src'` | Run from repo root with `python -m ...` or `export PYTHONPATH=$(pwd)` |
| Fortran linker error `ADRP out of range` | Compile with `-mcmodel=large` on x86_64 (not supported on Apple Silicon) |
| `Solver not available: highs` | Install HiGHS or set `PYOMO_SOLVER` to another LP solver |
| GA produces only infeasible solutions | Run HJ first (`--optimizer hj`), use its output as the GA starting point |
| Figures blank / display errors on HPC | The `Agg` backend is already set in `plot_results.py`; ensure you are not running an interactive backend elsewhere |

## License

Respect the licensing terms of the original LoadMatch Fortran code and
associated datasets.  Consult the project maintainers before publishing or
redistributing.

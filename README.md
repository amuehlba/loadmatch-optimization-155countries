# LoadMatch Python–Fortran Workflow

This repository couples a Python LP (linear programme) with the legacy LoadMatch
Fortran simulator (`powerworld.f`).  The Python LP sizes generation and storage
capacities to minimise annualised cost at hourly resolution.  Those capacity
targets are converted into the ~37 multiplier factors expected by the Fortran
executable, which performs the detailed 30-second dispatch check.  If the LP
solution is infeasible in Fortran, a meta-heuristic (Hooke–Jeeves or genetic
algorithm) iteratively adjusts **all** tunable model parameters until feasibility
is achieved at minimum cost.

## Repository layout

```
loadmatch-python/
├── src/
│   ├── io/              # Parsers for .dat inputs and conversion utilities
│   └── optimization/    # Pyomo model definition (model_builder.py)
├── scripts/
│   ├── run_full_workflow.py      # Main entry point (LP + Fortran + GA/HJ)
│   ├── run_python_model.py       # Standalone Python LP
│   ├── export_fortran_factors.py # Convert LP results → fortran_factors.dat
│   ├── factor_history_tools.py   # Parse and plot optimisation history
│   └── run_regions_slurm.sh      # SLURM job-array script for multi-region runs
├── fortran/
│   ├── src/powerworld.f          # LoadMatch Fortran source (~21 000 lines)
│   ├── build/                    # Compiler intermediate files
│   └── bin/powerworld            # Compiled executable
├── data/
│   ├── raw/                      # Canonical inputs (see "Input data" section below)
│   ├── results_python/
│   │   └── <REGION>/             # LP outputs + fortran_factors.dat per region
│   └── results_verification/
│       └── <REGION>/             # Fortran logs, factor_history.log, best factors per region
├── notebooks/
│   └── factor_history_analysis.ipynb  # Visualisation of GA/HJ optimisation runs
├── requirements.txt
└── README.md
```

## Quick start

### 1. Python environment

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

> **Note:** `pyomo` needs an LP solver.  The scripts use HiGHS (bundled with
> recent Pyomo).  Install an alternative solver if HiGHS is unavailable.

### 2. Compile the Fortran executable (on the server)

The Fortran binary must be compiled on an **x86_64 Linux** machine because the
code requires `-mcmodel=large` (not supported on Apple Silicon).

```bash
mkdir -p fortran/build fortran/bin
gfortran -O2 -mcmodel=large \
    -fdefault-real-8 -fdefault-double-8 -fno-automatic \
    -J fortran/build \
    fortran/src/powerworld.f \
    -o fortran/bin/powerworld
```

Verify the binary exists:

```bash
ls -lh fortran/bin/powerworld
```

### 3. Ensure input data is in place

All input files live in a **single shared directory** `data/raw/`.  There is no
separate folder per region — the Fortran binary always reads from `./data/raw/`
and constructs region-specific file names automatically.

#### Shared files (one copy covers all regions)

| File | Contents |
|------|----------|
| `countrystats.dat` | Base installed capacities, storage, and statistics for all 150 countries / 24+ regions |
| `loadreg.COUNTRY2030GW` | Hourly electricity demand (GW) for all countries — one row per country |
| `heatcooldd.dat` | Heating/cooling degree days (used for thermal load calculations) |
| `heatfrac.dat` | Monthly heat/cold load fractions |
| `baseline_results.<REGION>.dat` | Published factor values per region used as GA warm-start (optional, e.g. `baseline_results.UNITED-STATES.dat`) |

#### Per-region files (one set per region you want to run)

**Supply file** — always required:

| File | Contents |
|------|----------|
| `wwssupworld.UNITED-STATES` | Hourly WWS supply profiles for the US (~470 MB) |
| `wwssupworld.EUROPE` | Same for Europe |
| `wwssupworld.CHINA` | Same for China |
| … | Named `wwssupworld.<REGION>` — must match the `GRIDUSE` identifier in `powerworld.f` |

**Load file** — most regions share `loadreg.COUNTRY2030GW`, but a few need their
own:

| Region | Load file |
|--------|-----------|
| `UNITED-STATES`, `CHINA`, `INDIA`, `AFRICA`, `CANADA`, `AUSTRALIA`, `SOUTH-AMERICA`, `JAPAN`, `MIDEAST`, `RUSSIA`, `SOUTHEAST-ASIA`, and most others | `loadreg.COUNTRY2030GW` ← already present |
| `EUROPE` | **`loadreg.EUROPE`** must be added |
| `ICELAND` | **`loadreg.ICELAND`** must be added |
| `NORDEN`, `NORDENSWEGER`, `NODESWGENEBELU` | Region-specific load file |

This is set internally by `powerworld.f`; **no file-path edits to `powerworld.f`
are needed** — it constructs the correct filename automatically from `GRIDUSE`.

**Baseline file** (optional, but strongly recommended as GA warm-start):

Name these files `baseline_results.<REGION>.dat` and place them in `data/raw/`.
The SLURM script auto-selects the matching file per region:

```
data/raw/baseline_results.UNITED-STATES.dat
data/raw/baseline_results.EUROPE.dat
data/raw/baseline_results.CHINA.dat
```

If no baseline file is found for a region, the workflow falls back to running the
Python LP phase (requires Gurobi).

Path variables inside `powerworld.f` (around line 1050) must point to `data/raw/`:

```fortran
      PATHHOME = './data/raw/'
      PATHTEMP = './data/raw/'
      PATHTEM1 = './data/raw/'
      PATHLOAD = './data/raw/'
      PTH2LOAD = './data/raw/'
```

---

## Running the full workflow on the server

### One-command run

```bash
source .venv/bin/activate
python -m scripts.run_full_workflow --region UNITED-STATES [OPTIONS]
```

`--region` defaults to `UNITED-STATES` if omitted.  Supply any valid `GRIDUSE`
name from `powerworld.f` (e.g. `CHINA`, `EUROPE`, `INDIA`) to run a different
region.

This does everything automatically:

1. Solves the Pyomo LP, producing `data/results_python/<REGION>/summary.dat`
   *(skipped when `--baseline-start` is used)*
2. Converts LP capacities into `data/results_python/<REGION>/fortran_factors.dat`
   (all 37 parameters; LP values for the 7 capacity factors, Fortran CONUS
   defaults for the other 30) *(or loads factors from a baseline file)*
3. Copies the factor file to `fortran/fortran_factors.dat` and
   `data/raw/fortran_factors.dat`
4. Runs the Fortran executable (region passed as CLI argument)
5. If infeasible: inflates capacity factors until feasibility is achieved
6. Runs the GA or Hooke–Jeeves optimiser to minimise cost while maintaining
   feasibility

Results are saved to `data/results_verification/<REGION>/`:
- `factor_history.log` — full trial-by-trial log
- `genetic_factors.dat` (GA) or `hooke_jeeves_factors.dat` (HJ) — best factors
- `fortran_stdout.log`, `fortran_stderr.log` — Fortran output

---

## Tunable parameters (37 total)

The GA/HJ optimiser can tune all parameters that `powerworld.f` reads from
`fortran_factors.dat`.  Each parameter has a CONUS default, a category that
controls mutation behaviour, and physical bounds:

| Parameter    | Default | Category  | Description                              |
|-------------|---------|-----------|------------------------------------------|
| FACONWIN    | 1.0     | capacity  | Onshore wind capacity scaling            |
| FACOFFWIN   | 1.0     | capacity  | Offshore wind capacity scaling           |
| FACUTILPV   | 1.0     | capacity  | Utility PV capacity scaling              |
| FACRESPV    | 1.0     | capacity  | Residential rooftop PV scaling           |
| FACCOMPV    | 1.0     | capacity  | Commercial rooftop PV scaling            |
| CSPTURBFAC  | 1.0     | capacity  | CSP turbine ratio                        |
| FACSHT      | 1.0     | capacity  | Solar thermal heat scaling               |
| CSPSTORGAT  | 2.612   | ratio     | CSP storage charge/discharge ratio       |
| MXHRDRM     | 11      | hours     | Max demand-response shift hours          |
| BATDISCH    | 1.55    | tw        | Battery max discharge rate (TW)          |
| HCHARCSP    | 14.0    | hours     | CSP max charge hours                     |
| STORHBAT    | 4.0     | hours     | Battery storage duration hours           |
| STORHCOLD   | 14.0    | hours     | Cold storage hours (PCM-ice + CW-STES)   |
| STORHHWAT   | 14.0    | hours     | Hot-water STES hours                     |
| STORHPHS    | 14.0    | hours     | Pumped hydro storage hours               |
| UGFAC       | 3.0     | factor    | UTES charge rate factor                  |
| STORUGDYS   | 60.0    | days      | UTES seasonal heat storage days          |
| DAYH2STOR   | 40.0    | days      | H2 storage days                          |
| HPTURBRAT   | 10.0    | ratio     | Hydro turbine discharge ratio            |
| DAMCAPRAT   | 0.583   | ratio     | Hydro dam capacity / annual output       |
| DAYBASHYD   | 360.0   | days      | Baseload hydro storage days              |
| COOLSTES    | 0.4     | fraction  | Fraction AC from CW-STES vs ice          |
| PHSMIN      | 0.016   | tw        | Min PHS nameplate capacity (TW)          |
| FHEATFLX    | 0.15    | fraction  | Flexible heat load fraction              |
| FCOLDFLX    | 0.15    | fraction  | Flexible cold load fraction              |
| FRSTORINIT  | 0.5     | fraction  | Initial storage fill fraction            |
| FDISTHEAT   | 0.2     | fraction  | District heating fraction                |
| CPERFORM    | 4.0     | cop       | Heat pump COP (kWh-th / kWh-el)         |
| HCDDADD     | 1.0     | fixed     | HDD/CDD daily minimum (locked)          |
| FMORTBAU    | 0.9     | fixed     | BAU mortality fraction (locked)          |
| HWFAC       | 1.0     | factor    | HW-STES charge rate factor               |
| FCDISCH     | 0.091   | tw        | H2 fuel-cell discharge rate (TW)         |
| FCCHARG     | 0.091   | tw        | H2 electrolyser charge rate (TW)         |
| STORHHFC    | 13.0    | hours     | H2 electricity storage hours             |
| HBTDISCH    | 0.0     | tw        | Heat battery discharge rate (TW)         |
| STORHHBT    | 15.0    | hours     | Heat battery storage hours               |
| FRCIHFLEX   | 0.5     | fraction  | Flexible industrial heat fraction        |

**HCDDADD** and **FMORTBAU** are locked by default (not engineering design
variables).  Override with `--hj-lock` if you want different locking.

### Category bounds

| Category | Min  | Max  | Mutation scale |
|----------|------|------|---------------|
| capacity | 0.05 | none | 1.0           |
| ratio    | 0.0  | none | 0.5           |
| factor   | 0.0  | none | 0.5           |
| hours    | 0.0  | none | 0.3           |
| days     | 0.0  | none | 0.3           |
| tw       | 0.0  | none | 0.3           |
| fraction | 0.0  | 1.0  | 0.5           |
| cop      | 1.0  | 6.0  | 0.2           |
| fixed    | --   | --   | 0.0 (locked)  |

---

## CLI reference

### Common options (all optimisers)

| Flag | Default | Description |
|------|---------|-------------|
| `--region REGION` | `UNITED-STATES` | Grid region to simulate (must match a `GRIDUSE` name in `powerworld.f`) |
| `--parallel-evals N` | 1 | Parallel Fortran evaluations per generation |
| `--optimizer {hj,ga}` | `hj` | Hooke–Jeeves or genetic algorithm |
| `--hj-direction {inc,dec,both}` | `both` | Restrict perturbations |
| `--hj-lock FACTOR [...]` | `HCDDADD FMORTBAU` | Lock factors during search |
| `--baseline-start PATH` | — | Skip LP; use baseline file as initial guess |

### Hooke–Jeeves options

| Flag | Default | Description |
|------|---------|-------------|
| `--hj-initial-step` | 0.2 | Relative step size |
| `--hj-shrink` | 0.7 | Step reduction after no improvement |
| `--hj-max-iter` | 40 | Max iterations |
| `--hj-min-step` | 1e-5 | Stop when step < this |

### Genetic algorithm options

| Flag | Default | Description |
|------|---------|-------------|
| `--ga-population` | 24 | Population size |
| `--ga-generations` | 50 | Number of generations |
| `--ga-mutation-rate` | 0.15 | Per-factor mutation probability |
| `--ga-mutation-scale` | 0.2 | Relative mutation magnitude |
| `--ga-elite-frac` | 0.2 | Fraction of population preserved |
| `--ga-mutation-cooling` | 0.98 | Per-generation decay on rate and scale |
| `--ga-magnitude-damping` | 0.5 | Dampens mutations on large-valued params |
| `--ga-factor-scale NAME=SCALE` | — | Per-factor mutation scale override (repeatable) |

---

## Recommended server configurations

### Resource allocation: 38 CPUs available

Each Fortran evaluation is single-threaded and takes roughly the same wall-clock
time.  The GA evaluates `population_size` individuals per generation.  To keep
all CPUs busy you want:

```
parallel_evals  ≈  population_size  ≤  available_CPUs
```

With 38 CPUs, **use a population of 36 with 36 parallel evaluations** (leaving
2 cores for the OS and the Python orchestrator).  Alternatively, use 38 parallel
evaluations if you want maximum utilisation — the Python overhead is minimal.

### Warm-starting from baseline results

If you have results from a previous publication, you can skip the LP step and
use those factors as the initial guess.  This is typically **much faster**
because the baseline is already close to feasible, requiring fewer (or zero)
inflation steps before the GA starts.  Name the file
`baseline_results.<REGION>.dat` (e.g. `baseline_results.UNITED-STATES.dat`).

```bash
python -m scripts.run_full_workflow \
    --region UNITED-STATES \
    --baseline-start data/raw/baseline_results.UNITED-STATES.dat \
    --optimizer ga \
    --parallel-evals 36 \
    --ga-population 36 \
    --ga-generations 80
```

The parser reads 25 parameters from the baseline file format (capacity factors,
storage durations, power rates, etc.) and fills the remaining 12 with CONUS
defaults from `PARAM_REGISTRY`.

### Recommended GA run (all 37 parameters, 38 CPUs)

Run a single region (e.g. `EUROPE`) from a baseline warm-start:

```bash
source .venv/bin/activate
python -m scripts.run_full_workflow \
    --region EUROPE \
    --baseline-start data/raw/baseline_results.EUROPE.dat \
    --optimizer ga \
    --parallel-evals 36 \
    --ga-population 36 \
    --ga-generations 80 \
    --ga-mutation-rate 0.15 \
    --ga-mutation-scale 0.2 \
    --ga-elite-frac 0.2 \
    --ga-mutation-cooling 0.985 \
    --ga-magnitude-damping 0.5
```

Results land in `data/results_verification/EUROPE/`.  Omit `--region` (or set it
to `UNITED-STATES`) to reproduce the original CONUS run — results go to
`data/results_verification/UNITED-STATES/`.

**Why these numbers:**
- **Population 36**: With 35 active parameters, the rule of thumb is population
  >= N_params.  36 fits neatly into 36 parallel slots.
- **Generations 80**: With 35 dimensions, expect convergence in 50-100
  generations.  80 is a reasonable starting point; you can stop early if cost
  plateaus (check `factor_history.log`).
- **Mutation rate 0.15**: At 35 params, this means ~5 mutations per individual
  per generation — enough exploration without destroying good solutions.
- **Cooling 0.985**: Slower cooling than the default 0.98 because with more
  generations, you want mutations to stay meaningful longer.
  After 80 generations: effective rate = 0.15 * 0.985^80 = 0.044 (still active).

**Total Fortran evaluations:** 36 individuals x 80 generations + inflation trials
= ~2,900 evaluations. With 36 parallel slots, that's ~80 sequential batches.

### Convergence guidance

There is no single rule for "enough generations", but these heuristics help:

1. **Monitor the log**: Plot cost vs trial using the notebook or:
   ```python
   from scripts.factor_history_tools import parse_factor_history, records_to_dataframe
   df = records_to_dataframe(parse_factor_history(
       Path("data/results_verification/UNITED-STATES/factor_history.log")))
   # Check last 5 generations for improvement
   ```
2. **Cost plateau**: If the best feasible cost hasn't improved by > 0.1% in the
   last 10 generations, you're likely converged.
3. **Re-run with warm start**: The GA starts from the best feasible point found
   during inflation.  To continue from a previous GA run, manually set the
   starting factors in `fortran_factors.dat` and bypass the LP step.
4. **Staged optimisation** (recommended for first exploration):
   - Phase 1: Lock all non-capacity params, optimise only the 7 capacity factors
   - Phase 2: Lock capacity factors at their Phase 1 values, unlock storage/DR
   - Phase 3: Unlock everything for fine-tuning

### Staged optimisation example

**Phase 1** — capacity factors only (fast, 7 dimensions):

```bash
python -m scripts.run_full_workflow \
    --optimizer ga \
    --parallel-evals 36 \
    --ga-population 36 \
    --ga-generations 30 \
    --hj-lock HCDDADD FMORTBAU \
        CSPSTORGAT MXHRDRM BATDISCH HCHARCSP STORHBAT STORHCOLD \
        STORHHWAT STORHPHS UGFAC STORUGDYS DAYH2STOR HPTURBRAT \
        DAMCAPRAT DAYBASHYD COOLSTES PHSMIN FHEATFLX FCOLDFLX \
        FRSTORINIT FDISTHEAT CPERFORM HWFAC FCDISCH FCCHARG \
        STORHHFC HBTDISCH STORHHBT FRCIHFLEX
```

**Phase 2** — storage and demand response (unlock 28 non-capacity params):

```bash
# First copy Phase 1 best factors into fortran_factors.dat, then:
python -m scripts.run_full_workflow \
    --optimizer ga \
    --parallel-evals 36 \
    --ga-population 36 \
    --ga-generations 50 \
    --hj-lock HCDDADD FMORTBAU \
        FACONWIN FACOFFWIN FACUTILPV FACRESPV FACCOMPV CSPTURBFAC FACSHT
```

**Phase 3** — full unlock:

```bash
python -m scripts.run_full_workflow \
    --optimizer ga \
    --parallel-evals 36 \
    --ga-population 36 \
    --ga-generations 80 \
    --hj-lock HCDDADD FMORTBAU
```

---

---

## Multi-region runs on a SLURM cluster

Each region is an independent GA optimisation and maps naturally to a SLURM job
array: one task per region, each task using multiple CPUs for intra-GA
parallelism.

### What you need before adding a new region

1. **Supply file** — place `data/raw/wwssupworld.<REGION>` on the cluster.
   This is the only truly region-specific raw input file (~470 MB each).
2. **`countrystats.dat`** — already covers all 150 countries; no changes needed.
3. **`loadreg.COUNTRY2030GW`** — already contains all countries; verify your
   region's load row is present.
4. **Recompile the Fortran binary** — the binary now reads the region from CLI
   argument 1 (backward-compatible; defaults to `UNITED-STATES` if no arg):
   ```bash
   gfortran -O2 -mcmodel=large -fdefault-real-8 -fdefault-double-8 \
       fortran/src/powerworld.f -o fortran/bin/powerworld
   ```

### SLURM job array

Edit `scripts/run_regions_slurm.sh` — the `CONFIG` block at the top:

```bash
REGIONS=(
    "UNITED-STATES"
    "EUROPE"
    "CHINA"
    "INDIA"
    # add more as needed
)
PARALLEL_EVALS=36          # must equal --cpus-per-task in the SLURM header
GA_POPULATION=36
GA_GENERATIONS=80
BASELINE_DIR="data/raw"   # looks for baseline_results.<REGION>.dat; "" = always run LP
PYTHON_ENV_SETUP="conda activate loadmatch"
REPO_ROOT="$HOME/loadmatch-python"
```

Also update the `--array` directive to match `N_REGIONS - 1`:

```bash
#SBATCH --array=0-3%4   # 4 regions, max 4 concurrent
```

Submit with:

```bash
sbatch scripts/run_regions_slurm.sh
```

Each task writes results to `data/results_verification/<REGION>/` and LP
outputs to `data/results_python/<REGION>/`, so runs never overwrite each other.

### Analysing results from a specific region

In the notebook, change the `REGION` variable in the second cell:

```python
REGION = "EUROPE"   # reads data/results_verification/EUROPE/factor_history.log
```

---

## Running components individually

### Solve the LP only

```bash
source .venv/bin/activate
python -m scripts.run_python_model   # defaults to UNITED-STATES
```

Produces `data/results_python/UNITED-STATES/summary.dat`.

### Export Fortran factors only

```bash
python -m scripts.export_fortran_factors \
    --region EUROPE \
    --summary data/results_python/EUROPE/summary.dat \
    --output data/results_python/EUROPE/fortran_factors.dat
```

The output file contains all 37 parameters (LP values for the 7 capacity factors,
CONUS defaults for the rest).

### Run Fortran manually

```bash
cd /path/to/loadmatch-python
./fortran/bin/powerworld EUROPE   # pass region as argument (default: UNITED-STATES)
```

The executable reads `fortran_factors.dat` from the `PATHHOME` directory
(`data/raw/`).  The region argument sets `GRIDUSE` inside the simulation.

---

## Visualising optimisation history

The workflow logs every trial to `data/results_verification/factor_history.log`.

### On your local machine

1. Copy the region's result folder from the server:

   ```bash
   scp -r <user>@<server>:/path/to/repo/data/results_verification/EUROPE/ \
       data/results_verification/EUROPE/
   ```

2. In `notebooks/factor_history_analysis.ipynb`, set `REGION = "EUROPE"` in the
   second cell.  The notebook reads from `data/results_verification/EUROPE/` and
   saves figures there.

3. Open the analysis notebook (`notebooks/factor_history_analysis.ipynb`) which
   produces the following publication-ready figures:

   | Figure | Content |
   |--------|---------|
   | Fig 1  | Cost convergence + feasibility rate per generation |
   | Fig 2  | Capacity-factor trajectories (7 factors, with baseline reference) |
   | Fig 3  | Non-capacity parameter trajectories (6-panel: storage hours, days, power rates, fractions, ratios, DR) |
   | Fig 4  | Baseline vs GA-optimised: bar chart of all parameters normalised to baseline |
   | Fig 5  | Population cost distribution per generation (box plots) |
   | Fig 6  | Parameter sensitivity: coefficient of variation in final generation |
   | Fig 7  | Capacity evolution bar chart (LP → inflate → GA) with cost overlay |

   All figures are saved as PDF to `data/results_verification/`.

3. Or use the helper utilities directly:

   ```python
   from pathlib import Path
   import matplotlib.pyplot as plt
   from scripts.factor_history_tools import (
       parse_factor_history,
       records_to_dataframe,
       plot_factor_trajectories,
       plot_cost_and_feasibility,
       FACTOR_COLUMNS,
   )

   records = parse_factor_history(Path("data/results_verification/factor_history.log"))
   df = records_to_dataframe(records)
   plot_factor_trajectories(df, absolute=False)
   plot_factor_trajectories(df, columns=FACTOR_COLUMNS)
   plot_cost_and_feasibility(df)
   plt.show()
   ```

---

## GA design notes

### Mutation handling

- **Category-aware scales**: Each parameter category has a default mutation scale
  multiplier (see table above).  Capacity factors mutate at full scale;
  fractions (0–1) and COP are more conservative.
- **Zero-valued parameters**: Several parameters default to 0 (BATDISCH,
  FCDISCH, FCCHARG, STORHHFC, HBTDISCH).  Multiplicative mutation would leave
  them stuck at 0.  The GA uses the parameter's Fortran default as a reference
  magnitude for additive perturbation when the current value is near zero.
- **Magnitude damping**: Large-valued parameters (e.g. DAYBASHYD = 360) receive
  damped mutations via `1 / max(1, |value|)^damping`.
- **Bounds clamping**: Every mutated value is clamped to its category bounds
  (e.g. fractions to [0, 1], COP to [1, 6]).
- **Crossover**: BLX-alpha blending — child = w * parent1 + (1-w) * parent2
  with w ~ Uniform(0,1).

### Inflation logic

The `inflate_until_feasible()` function only touches the **7 original capacity
factors**.  It first raises sub-unity factors toward 1.0, then multiplicatively
inflates all capacity factors.  Storage/DR/ratio parameters remain at their
defaults during inflation and are only adjusted by the GA or HJ.

---

## Troubleshooting

- **`ModuleNotFoundError: No module named 'src'`** — Run from the repo root
  with `python -m ...`, or `export PYTHONPATH=$(pwd)`.
- **Fortran linker error (`ADRP out of range`)** — Compile with
  `-mcmodel=large` on x86_64 (does not work on Apple Silicon ARM).
- **End-of-file on `loadreg.COUNTRY2030GW`** — Ensure path variables in
  `powerworld.f` have no trailing blanks.  Use `CHARACTER(11)` and short
  relative paths.
- **Solver failures** — Check that HiGHS is installed; alternatively, point the
  Pyomo `SolverFactory` to another LP solver.
- **`command not found: python`** — Use `python3` explicitly, or ensure `.venv`
  is activated.
- **GA produces only infeasible individuals** — The inflation phase may have
  started from a point too far from feasibility.  Try increasing
  `--ga-population` or running a Hooke–Jeeves pass first (`--optimizer hj`),
  then feeding its output as the GA starting point.

## Contributing

- Keep Fortran edits minimal to ease syncing with upstream.
- When adding parameters to `PARAM_REGISTRY` in `run_full_workflow.py`, also
  update `FACTOR_COLUMNS` in `factor_history_tools.py` and the
  `READ_FACTOR_OVERRIDES` subroutine in `powerworld.f`.
- Run verification tests: `python -c "from scripts.run_full_workflow import PARAM_REGISTRY; print(len(PARAM_REGISTRY), 'params')"`.

## License

Respect the licensing terms accompanying the original LoadMatch Fortran code and
associated datasets.  Consult the project maintainers for publication or
redistribution permissions.

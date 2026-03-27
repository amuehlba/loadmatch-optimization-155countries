# LoadMatch Python–Fortran Workflow

A Python driver for the **LoadMatch** energy-system simulator (`powerworld.f`),
developed by the Jacobson Lab at Stanford University.  The workflow optimises
the ~30 tunable parameters of the LoadMatch Fortran model using a **Genetic
Algorithm (GA)**, searching for the lowest-cost 100% clean-energy system layout
for any of the supported world regions.  Results are compared against the
published Jacobson-group baseline and visualised as a full set of
publication-quality figures.

---

## What this repository does

1. **Parses the canonical Jacobson-group baseline** (`xxEGS.<REGION>`) and
   stores a structured JSON summary of every energy metric.

2. **Extracts region-specific Fortran defaults** directly from `powerworld.f`
   (branch-aware: respects the hardcoded `IMERGH2` and `IFEGS` control
   variables so the correct conditional branch is read).

3. **Runs the LoadMatch Fortran simulator** (`powerworld.f`, ~21 000 lines)
   with a given set of factors via a `READ_FACTOR_OVERRIDES` subroutine, and
   parses the full output into ~50 structured metrics (TWh by
   source/end-use/storage, annual cost lo/mn/hi, cost by category in c/kWh).

4. **Optimises with a parallel Genetic Algorithm** — up to 24 parallel Fortran
   evaluations per generation across all CPU cores — minimising annual system
   cost while maintaining feasibility (all demand met).

5. **Generates a complete figure set** (13 main figures + 3 multi-region
   overview figures) automatically at the end of every run:
   - Energy supply/demand breakdowns
   - Storage configuration
   - Capacity factor comparison (baseline vs GA)
   - Land and water use
   - Cost breakdown and convergence
   - Wind/solar/water Sankey energy-flow diagrams (baseline and GA-optimal)
   - Cross-region convergence, wind/solar scatter, and ternary composition plots

6. **Scales to any supported world region** via command-line argument — no
   changes to `powerworld.f` required.  Supports all 29+ regions defined in
   the Fortran source.

---

## Repository layout

```
loadmatch-python/
├── src/
│   ├── io/
│   │   └── dat_parser.py              # Read/write .dat key=value files
│   └── optimization/
│       └── model_builder.py           # Pyomo LP model (optional warm-start)
├── scripts/
│   ├── run_full_workflow.py           # Main entry point — GA + Fortran + plots
│   ├── parse_fortran_output.py        # Parse Fortran stdout → structured JSON
│   ├── plot_results.py                # All publication figures (Figs 2–13 + overviews)
│   ├── run_python_model.py            # Standalone Pyomo LP (optional)
│   ├── export_fortran_factors.py      # Convert LP results → fortran_factors.dat
│   ├── factor_history_tools.py        # Parse/plot optimisation history
│   ├── run_regions_slurm.sh           # Legacy 4-region SLURM job array
│   ├── run_select_regions_slurm.sh    # Curated 10-region SLURM job array
│   ├── run_all_regions_slurm.sh       # All 29-region SLURM job array
│   └── run_europe_slurm.sh            # Single-region SLURM job (testing)
├── fortran/
│   ├── src/powerworld.f               # LoadMatch Fortran source (~21 000 lines)
│   └── bin/powerworld                 # Compiled executable (x86_64 Linux only)
├── data/
│   ├── raw/                           # Canonical inputs (shared across regions)
│   │   ├── countrystats.dat
│   │   ├── loadreg.COUNTRY2030GW
│   │   ├── heatcooldd.dat
│   │   ├── heatfrac.dat
│   │   ├── wwssupworld.<REGION>       # Hourly supply profiles (~470 MB each)
│   │   └── xxEGS.<REGION>            # Jacobson-group canonical baseline output
│   ├── results_python/<REGION>/       # LP outputs (optional)
│   ├── results_verification/<REGION>/ # Fortran logs, factor history, summaries
│   └── results_verification/          # Cross-region overview figures (figA1–A3)
├── requirements.txt
└── README.md
```

---

## Setup

### 1. Python environment

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Compile the Fortran executable

Must be compiled on **x86_64 Linux** (Sherlock or any Linux workstation).
Apple Silicon is not supported by `gfortran -mcmodel=medium`.

```bash
mkdir -p fortran/build fortran/bin
gfortran -O2 -mcmodel=medium \
    -fdefault-real-8 -fdefault-double-8 -fno-automatic \
    -J fortran/build \
    fortran/src/powerworld.f \
    -o fortran/bin/powerworld
```

> **Note:** `-mcmodel=medium` (not `large`) is required — the model's static
> data arrays exceed the 2 GB limit of the default small model but `-mcmodel=large`
> is not supported by all gfortran versions on Sherlock.

### 3. Input data

All files live in `data/raw/`.  The Fortran binary expects the working directory
to be the repository root when invoked.

| File | Contents |
|------|----------|
| `countrystats.dat` | Base capacities and statistics for all regions |
| `loadreg.COUNTRY2030GW` | Hourly electricity demand for all countries (real values) |
| `heatcooldd.dat` | Heating/cooling degree days |
| `heatfrac.dat` | Monthly heat/cold fractions |
| `wwssupworld.<REGION>` | Hourly wind/solar supply profiles per region (~470 MB each) |
| `xxEGS.<REGION>` | Jacobson-group published Fortran output (canonical baseline) |

> **Supply file preprocessing:** The first time a region is run, Fortran
> auto-generates `wwsmonthly.<REGION>` from `wwssupworld.<REGION>` (IFREWRITE=3).
> This one-time step can take 10–20 minutes for large regions (e.g. UNITED-STATES).
> Subsequent runs skip it automatically.

---

## Running the workflow

### Single region (local or Sherlock interactive)

```bash
source .venv/bin/activate
python -m scripts.run_full_workflow \
    --region UNITED-STATES \
    --optimizer ga \
    --parallel-evals 8 \
    --ga-population 20 \
    --ga-generations 30 \
    --baseline-start defaults
```

**What happens, step by step:**

1. Parses `data/raw/xxEGS.<REGION>` → `canonical_baseline_summary.json`
2. Extracts region-specific Fortran defaults from `powerworld.f` (branch-aware)
3. Runs Fortran with those defaults → `fortran_baseline_run.out` + `baseline_summary.json`
4. If infeasible, inflates capacity factors until a feasible starting point is found
5. Runs the GA for the specified number of generations, evaluating `--parallel-evals`
   candidates simultaneously using `ProcessPoolExecutor`
6. Re-runs Fortran with the best-found factors → `fortran_optimal_run.out` + `optimal_summary.json`
7. Generates all publication figures and saves them to `data/results_verification/<REGION>/`
8. Generates cross-region overview figures in `data/results_verification/`
   (including any other regions that have already completed)

### Recommended settings for Sherlock (24 CPUs)

```bash
python -m scripts.run_full_workflow \
    --region UNITED-STATES \
    --optimizer ga \
    --parallel-evals 24 \
    --ga-population 37 \
    --ga-generations 50 \
    --ga-mutation-rate 0.15 \
    --ga-mutation-scale 0.2 \
    --ga-elite-frac 0.2 \
    --ga-mutation-cooling 0.985 \
    --baseline-start defaults
```

### Output files

All results land in `data/results_verification/<REGION>/`:

| File | Contents |
|------|----------|
| `canonical_baseline_summary.json` | Parsed Jacobson-group baseline (~50 metrics) |
| `baseline_summary.json` | Fortran run with region default factors |
| `optimal_summary.json` | Fortran run with GA-optimal factors |
| `genetic_factors.dat` | Best-found factor values (KEY = VALUE format) |
| `factor_history.log` | Full log of every Fortran evaluation (factor values + cost) |
| `fortran_baseline_run.out` | Full Fortran stdout for the baseline run |
| `fortran_optimal_run.out` | Full Fortran stdout for the optimal run |
| `fortran_stdout.log`, `fortran_stderr.log` | Latest Fortran console output |
| `fig*.pdf` / `fig*.png` | Publication figures |

Cross-region overview figures are saved to `data/results_verification/`:

| File | Contents |
|------|----------|
| `figA1_all_regions_convergence.{pdf,png}` | Cost reduction (%) vs GA generation per region |
| `figA2_all_regions_wind_solar.{pdf,png}` | Wind vs solar energy share scatter |
| `figA3_all_regions_ternary.{pdf,png}` | Wind–Solar–Water ternary composition |

### CLI reference

| Flag | Default | Description |
|------|---------|-------------|
| `--region` | `UNITED-STATES` | Region name matching `GRIDUSE` in `powerworld.f` |
| `--optimizer {hj,ga}` | `hj` | Optimiser (GA recommended) |
| `--parallel-evals N` | 1 | Parallel Fortran evaluations (set = CPU count) |
| `--baseline-start {defaults,PATH}` | — | `defaults`: use Fortran hardcoded region values; `PATH`: load from `.dat` file; omit to run LP first |
| `--ga-population` | 24 | GA population size (odd numbers work well) |
| `--ga-generations` | 50 | Number of GA generations |
| `--ga-mutation-rate` | 0.15 | Per-factor mutation probability |
| `--ga-mutation-scale` | 0.2 | Relative mutation magnitude |
| `--ga-elite-frac` | 0.2 | Fraction of population preserved unchanged |
| `--ga-mutation-cooling` | 0.98 | Per-generation decay applied to rate and scale |
| `--hj-initial-step` | 0.2 | Relative step size (Hooke–Jeeves) |
| `--hj-shrink` | 0.7 | Step reduction factor after no improvement |
| `--hj-max-iter` | 40 | Maximum Hooke–Jeeves iterations |

---

## Tunable parameters (PARAM_REGISTRY)

The GA optimises 27 tunable parameters.  Eight additional parameters are
**fixed** (locked from optimisation) because they represent policy constraints
or physical constants that should not be treated as engineering design variables.

| Category | Parameters |
|----------|-----------|
| **Capacity factors** (dimensionless scaling) | `FACONWIN`, `FACOFFWIN`, `FACUTILPV`, `FACRESPV`, `FACCOMPV`, `CSPTURBFAC`, `FACSHT` |
| **Storage / CSP** | `CSPSTORGAT`, `MXHRDRM`, `BATDISCH`, `HCHARCSP`, `STORHBAT`, `STORHCOLD`, `STORHHWAT`, `STORHPHS` |
| **UTES / H2 storage** | `UGFAC`, `STORUGDYS`, `DAYH2STOR` |
| **Hydropower** | `HPTURBRAT`, `DAYBASHYD` |
| **H2 fuel cells** | `FCDISCH`, `FCCHARG`, `STORHHFC` |
| **Heat systems** | `HWFAC`, `HBTDISCH`, `STORHHBT`, `CPERFORM` |
| **Fixed (not optimised)** | `FRSTORINIT`, `DAMCAPRAT`, `PHSMIN`, `FDISTHEAT`, `COOLSTES`, `FHEATFLX`, `FCOLDFLX`, `FRCIHFLEX` |

Fixed parameters are still shown in Fig 4 (parameter comparison) with a
`[fixed]` label and grey styling.

---

## Multi-region SLURM runs (Stanford Sherlock)

Three ready-to-submit SLURM job-array scripts are provided.  Each task in the
array runs one region independently; `--parallel-evals` CPUs are used for
intra-GA parallelism within each task.

### Available scripts

| Script | Regions | Array |
|--------|---------|-------|
| `run_select_regions_slurm.sh` | 10 curated regions (see below) | `--array=0-9` |
| `run_all_regions_slurm.sh` | All 29 world regions | `--array=0-28` |
| `run_europe_slurm.sh` | EUROPE only (for testing) | single job |
| `run_regions_slurm.sh` | USA, Canada, Europe, China (legacy) | `--array=0-3` |

### Curated 10-region set (`run_select_regions_slurm.sh`)

Chosen for geographic diversity and resource contrast:

| Index | Region | Rationale |
|-------|--------|-----------|
| 0 | `UNITED-STATES` | Reference; high solar/wind/hydro mix |
| 1 | `CANADA` | Hydro-dominant |
| 2 | `EUROPE` | Policy-critical; high offshore wind |
| 3 | `CHINA` | Largest emitter; rapid buildout |
| 4 | `INDIA` | Fast-growing demand; high solar |
| 5 | `JAPAN` | Island; resource-constrained |
| 6 | `AUSTRALIA` | World-leading solar/wind resource |
| 7 | `AFRICA-EAST` | Geothermal-rich; developing |
| 8 | `SOUTHEAST-ASIA` | Tropical; biomass + solar |
| 9 | `RUSSIA` | Cold climate; fossil-heavy baseline |

### All 29 world regions (`run_all_regions_slurm.sh`)

`AFRICA-EAST`, `AFRICA-NORTH`, `AFRICA-SOUTH`, `AFRICA-WEST`, `AUSTRALIA`,
`CANADA`, `CENTRAL-AMERIC`, `CENTRAL-ASIA`, `CHINA`, `CUBA`, `EUROPE`,
`HAITI`, `ICELAND`, `INDIA`, `ISRAEL`, `JAMAICA`, `JAPAN`, `MADAGASCAR`,
`MAURITIUS`, `MIDEAST`, `NEW-ZEALAND`, `PHILIPPINES`, `RUSSIA`, `SOUTHAM-NW`,
`SOUTHAM-SE`, `SOUTHEAST-ASIA`, `SOUTH-KOREA`, `TAIWAN`, `UNITED-STATES`

### Sherlock setup (one-time)

```bash
# On a login node:
module load python/3.12.1

cd $GROUP_HOME/loadmatch-python
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Compile Fortran on a compute node (srun for x86_64):
srun --partition=serc --cpus-per-task=4 --mem=8G --pty bash
cd $GROUP_HOME/loadmatch-python
mkdir -p fortran/build fortran/bin
gfortran -O2 -mcmodel=medium \
    -fdefault-real-8 -fdefault-double-8 -fno-automatic \
    -J fortran/build fortran/src/powerworld.f \
    -o fortran/bin/powerworld
exit
```

### Submit

```bash
# Recommended: start with the 10-region batch to validate
sbatch scripts/run_select_regions_slurm.sh

# Full 29-region run
sbatch scripts/run_all_regions_slurm.sh

# Test single region
sbatch scripts/run_europe_slurm.sh
```

### Monitor progress

```bash
squeue -u $USER
tail -f logs/slurm_<JOBID>_<TASK>.out
tail -f data/results_verification/<REGION>/factor_history.log
```

### After jobs complete — generate plots

Plots are generated automatically at the end of each run (including cross-region
overviews).  To regenerate manually for any region:

```bash
python -m scripts.plot_results --region UNITED-STATES
```

Sync figures to your local machine:

```bash
rsync -av sherlock:/path/to/repo/data/results_verification/ ./results_local/
```

---

## Supported world regions

All regions are defined as `ELSEIF (GRIDUSE.EQ.'<NAME>')` blocks in
`powerworld.f`.  The region name is passed as a command-line argument to
the Fortran binary and as `--region` to the Python workflow.

**Main world regions (29):**
`AFRICA-EAST`, `AFRICA-NORTH`, `AFRICA-SOUTH`, `AFRICA-WEST`,
`AUSTRALIA`, `CANADA`, `CENTRAL-AMERIC`, `CENTRAL-ASIA`,
`CHINA`, `CUBA`, `EUROPE`, `HAITI`, `ICELAND`, `INDIA`,
`ISRAEL`, `JAMAICA`, `JAPAN`, `MADAGASCAR`, `MAURITIUS`,
`MIDEAST`, `NEW-ZEALAND`, `PHILIPPINES`, `RUSSIA`,
`SOUTHAM-NW`, `SOUTHAM-SE`, `SOUTHEAST-ASIA`, `SOUTH-KOREA`,
`TAIWAN`, `UNITED-STATES`

**Additional European sub-regions:**
`NORDEN`, `NORDENSWEGER`, `NODESWGENEBELU`, `SWIGER`, `SWIFRA`, `SWIITA`,
`NWEUROPE`, `SPAPORGIB`, `WESTEUROPE`, `GERMANY`, `UNITED-KINGDOM`, `FRANCE`,
`NORWAY`, `SWEDEN`, `DENMARK`, `NETHERLANDS`, `BELGIUM`, `LUXEMBOURG`,
`SWITZERLAND`, `SPAIN`, `PORTUGAL`, `GIBRALTAR`, `ITALY`,
`GRAN-CANARIA`, `LANZAROTE-FV`, `TENERIFE`, `LA-PALMA`, `LA-GOMERA`, `EL-HIERRO`

Each region requires `data/raw/wwssupworld.<REGION>` and optionally
`data/raw/xxEGS.<REGION>` for baseline comparison.

---

## Figures generated

All figures are saved as both `.pdf` and `.png`.

| Figure | Contents |
|--------|----------|
| `fig2` | GA cost convergence over generations |
| `fig3` | Factor evolution over GA generations |
| `fig4` | Baseline vs GA-optimal factor comparison (all parameters, fixed shown in grey) |
| `fig5` | Energy supply/demand breakdown and storage |
| `fig7` | Annual cost breakdown by technology category |
| `fig8` | Land and water use (region-specific labels) |
| `fig9` | Capacity factor and generation by source |
| `fig10` | Installed capacity comparison (baseline vs GA vs 2050 target) |
| `fig12` | Storage hours and capacity |
| `fig13a` | Sankey energy-flow diagram — baseline scenario |
| `fig13b` | Sankey energy-flow diagram — GA-optimal scenario |
| `figA1` | *(cross-region)* Cost reduction (%) vs GA generation |
| `figA2` | *(cross-region)* Wind vs solar energy share scatter |
| `figA3` | *(cross-region)* Wind–Solar–Water ternary composition |

Cross-region figures (figA1–A3) are regenerated automatically each time any
region finishes, accumulating all available results.

---

## Key implementation notes

### Factor file mechanism

The Fortran binary reads `READ_FACTOR_OVERRIDES` (a subroutine appended to
`powerworld.f`) immediately after setting all region-specific defaults.  The
subroutine opens `data/raw/fortran_factors.dat`, reads KEY = VALUE pairs, and
overrides the matching Fortran variables.  If the file is absent, all hardcoded
region defaults are used unchanged.

When `--baseline-start defaults` is used, the Python workflow deletes any
existing factor file before the baseline run so Fortran uses its hardcoded
values, then writes a factor file for every subsequent GA evaluation.

### Branch-aware default extraction

`extract_fortran_region_defaults()` in `run_full_workflow.py` parses
`powerworld.f` to read the actual runtime values of each parameter for a given
region.  It is aware of `IF/ELSEIF/ELSE/ENDIF` chains conditioned on `IMERGH2`
and `IFEGS` (both hardcoded in `powerworld.f`) and only reads values from the
branch that executes at runtime.  Unknown conditionals fall back to
conservative "always-active" treatment.

### Supply file preprocessing (IFREWRITE)

The first run for a region triggers IFREWRITE=3, which reads
`wwssupworld.<REGION>` and writes `wwsmonthly.<REGION>`.  This is automatic
and region-agnostic.  Subsequent runs set IFREWRITE=0 and skip preprocessing.
The preprocessing step can take 10–20 minutes for large supply files.

### Parallel GA evaluations

Each GA generation evaluates the population using `ProcessPoolExecutor` with
`--parallel-evals` workers.  Each worker gets an isolated workspace directory
under `data/tmp_workspaces/` to avoid file conflicts between concurrent Fortran
processes.  Set `--parallel-evals` equal to the number of available CPUs.

### Load data format

The `loadreg.COUNTRY2030GW` file contains hourly electricity demand per country
as real-valued GW.  The Fortran source was updated to read these as
floating-point (`RLOADMW`) rather than integer (`ILOADMW`) to support countries
with sub-GW or fractional demand values (e.g. Cyprus, small island states).

---

## Troubleshooting

| Symptom | Cause / Fix |
|---------|-------------|
| `ModuleNotFoundError: No module named 'src'` | Run from repo root with `python -m ...` or `export PYTHONPATH=$(pwd)` |
| `Relocation truncated` linker error | Add `-mcmodel=medium` to the gfortran compile command |
| `Bad integer` error in Fortran for small countries | Rebuild Fortran after the RLOADMW fix (already in source) |
| US baseline infeasible / long inflate loop | Rebuild Fortran — hardcoded US defaults were corrupted with optimisation results; fixed in source |
| GA produces only infeasible solutions | Run HJ first (`--optimizer hj`) to find a feasible region, then restart GA from that point |
| Supply preprocessing takes 20+ minutes | Normal for first run of a region; subsequent runs are fast |
| `Solver not available: highs` | Install HiGHS or use `--baseline-start defaults` to skip the LP entirely |
| Figures not generated / plot errors | Run `python -m scripts.plot_results --region <REGION>` manually; check for missing `optimal_summary.json` |

---

## License

Respect the licensing terms of the original LoadMatch Fortran code and
associated datasets.  Consult the Jacobson Lab before publishing or
redistributing results.

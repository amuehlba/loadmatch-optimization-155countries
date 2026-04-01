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
├── Snakefile                          # Snakemake pipeline (all rules)
├── config/
│   └── workflow.yaml                  # Default regions + GA parameters
├── profiles/
│   └── slurm/
│       └── config.yaml                # SLURM cluster profile for Snakemake
├── src/
│   ├── io/
│   │   └── dat_parser.py              # Read/write .dat key=value files
│   └── optimization/
│       └── model_builder.py           # Pyomo LP model (optional warm-start)
├── scripts/
│   ├── run_full_workflow.py           # GA + Fortran driver (--no-plots / --preprocess-only / --run-lp-only)
│   ├── check_inputs.py                # Validate required data files (called by Snakemake)
│   ├── parse_fortran_output.py        # Parse Fortran stdout → structured JSON
│   ├── plot_results.py                # All publication figures (--all-regions flag)
│   ├── run_python_model.py            # Standalone Pyomo LP (optional)
│   ├── export_fortran_factors.py      # Convert LP results → fortran_factors.dat
│   ├── factor_history_tools.py        # Parse/plot optimisation history
│   ├── run_all_regions_slurm.sh       # All 29 regions — Snakemake cluster launcher
│   ├── run_select_regions_slurm.sh    # 10 curated regions — Snakemake launcher
│   ├── run_europe_slurm.sh            # EUROPE only — Snakemake launcher (testing)
│   └── run_regions_slurm.sh           # Legacy 4-region direct job array
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
├── logs/                              # Snakemake + SLURM logs
├── docs/                              # dag.png and other documentation assets
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

## Snakemake workflow

The entire pipeline is described in the `Snakefile` at the repo root.
Snakemake tracks input/output dependencies and only reruns rules whose
outputs are stale or missing — making it safe to restart interrupted runs.

### Rule dependency graph (DAG)

![Snakemake DAG (3 representative regions)](docs/dag.png)

*Shown for 3 representative regions; the full 29-region graph follows the same structure.*

**Rule execution order (per region):**

```
compile_fortran
      │
check_inputs          ← validates all required data files exist (exits on error)
      │
preprocess_supply     ← runs IFREWRITE=1,2 if wwssupworld.{region} missing
      │
[run_lp]              ← optional LP warm-start (lp_warmstart: true in config)
      │
run_ga                ← GA optimisation (--no-plots)
      │
plot_region           ← per-region figures (fig1–fig13)
      │ (all regions done)
plot_overview         ← cross-region figA1–A3
      │
     all
```

`run_lp` is only included in the DAG when `lp_warmstart: true` is set in
`config/workflow.yaml` and a compatible LP solver is installed.

To regenerate the DAG image at any time (requires `graphviz`):
```bash
# 3-region illustration (readable); requires actual data/raw files to exist
.venv/bin/snakemake --dag \
    --config "regions=[UNITED-STATES,EUROPE,JAPAN]" \
    | dot -Tpng -Grankdir=TB -Gsize="10,12" -Gdpi=200 \
    -o docs/dag.png
```

### Common commands

```bash
# ── Local (laptop / interactive node) ────────────────────────────────────────

# Dry-run: print what would be executed without running anything
snakemake --cores 1 --dryrun all

# Run the full pipeline for all regions (N = number of CPUs available)
snakemake --cores N all

# Run for a single region only
snakemake --cores 24 \
    data/results_verification/EUROPE/optimal_summary.json \
    data/results_verification/EUROPE/.plots_done

# Regenerate overview figures from all completed runs
snakemake --cores 1 plot_overview

# Force-rerun one region's GA even if outputs exist
snakemake --cores 24 --forcerun run_ga \
    data/results_verification/EUROPE/optimal_summary.json

# ── SLURM cluster (Sherlock) ──────────────────────────────────────────────────

# Submit all 29 regions (orchestrator job launches child jobs automatically)
sbatch scripts/run_all_regions_slurm.sh

# Submit 10-region curated subset
sbatch scripts/run_select_regions_slurm.sh

# Submit EUROPE only (for testing)
sbatch scripts/run_europe_slurm.sh

# Monitor all running jobs
squeue -u $USER

# Check Snakemake's view of rule completion
snakemake --profile profiles/slurm --summary
```

### Configuration

Edit `config/workflow.yaml` to change which regions are run or to adjust GA
hyper-parameters without modifying the Snakefile:

```yaml
# Run only a subset of regions
regions:
  - UNITED-STATES
  - EUROPE
  - JAPAN

# Tune the GA
ga:
  population:  50
  generations: 100

# Enable LP warm-start (requires Gurobi or HiGHS)
lp_warmstart: true
```

Pass a one-off override without editing the file:
```bash
snakemake --cores 24 \
    --config ga.generations=100 \
    data/results_verification/EUROPE/optimal_summary.json
```

#### LP warm-start (`lp_warmstart`)

When `lp_warmstart: true`, the `run_lp` rule runs the Pyomo LP optimiser for
each region **before** the GA and uses the resulting capacity factors as the GA
starting point.  Requires a working LP solver in the Python environment:

```bash
pip install highspy      # HiGHS (open-source, recommended)
# or
pip install gurobipy     # Gurobi (requires licence)
```

When `lp_warmstart: false` (default), the GA starts from:
1. `data/raw/baseline_results.<REGION>.dat` — if present
2. Fortran hardcoded region defaults — otherwise

### New flags added to Python scripts

| Script | Flag | Effect |
|--------|------|--------|
| `run_full_workflow.py` | `--no-plots` | Skip figure generation after GA (Snakemake handles plots as a separate rule) |
| `run_full_workflow.py` | `--preprocess-only` | Run IFREWRITE=1,2 supply preprocessing only, then exit (used by `preprocess_supply` rule) |
| `run_full_workflow.py` | `--run-lp-only` | Run LP optimisation and export factors only, then exit (used by `run_lp` rule) |
| `plot_results.py` | `--all-regions` | Only regenerate cross-region overview figures (figA1–A3); skip per-region figures |

---

## Running the workflow (standalone, without Snakemake)

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

All figures are saved as both `.pdf` and `.png` using SKILL.md publication defaults
(8 pt Helvetica/Arial, Okabe-Ito CVD-safe palette, no top/right spines, 300 dpi).

| Figure | Filename stem | Contents |
|--------|---------------|----------|
| 1 | `fig1_cost_convergence` | GA cost convergence + feasibility rate per generation |
| 2 | `fig2_capacity_factors` | Capacity-factor trajectories of the best feasible individual |
| 3 | `fig3_parameter_trajectories` | Optimised (non-fixed) parameter trajectories, 5 panels |
| 4 | `fig4_baseline_vs_ga` | Baseline vs GA-optimal: all parameters, fixed params sorted to bottom |
| 5 | `fig5_cost_and_capacity` | Cost distribution boxplots (A) + generation GW & energy storage TWh at milestones (B) |
| 6 | `fig6_parameter_sensitivity` | Coefficient of variation across final-generation feasible population |
| 7 | `fig7_capacity_comparison` | Generation & storage capacities: baseline vs GA-optimal (3 panels) |
| 8 | `fig8_area_comparison` | Land area demand by technology and total footprint |
| 9 | `fig9_cost_comparison` | Annual cost breakdown by category: baseline vs GA-optimal |
| 10 | `fig10_capacity_mix` | Installed capacity mix, wind/solar scatter, and ternary diagram |
| 11 | `fig11_diversity_heatmap` | Population diversity heatmap (final GA generation) |
| 12 | `fig12_cost_waterfall` | Cost waterfall: contribution of each parameter change |
| 13a | `fig13_sankey_energy_flow` | Sankey energy-flow diagram — baseline scenario |
| 13b | `fig13_sankey_energy_flow_optimal` | Sankey energy-flow diagram — GA-optimal scenario |
| A1 | `figA1_all_regions_convergence` | *(cross-region)* Cost reduction (%) vs GA generation |
| A2 | `figA2_all_regions_wind_solar` | *(cross-region)* Wind vs solar energy share scatter |
| A3 | `figA3_all_regions_ternary` | *(cross-region)* Wind–Solar–Water ternary composition |

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
branch that executes at runtime.

Conditionals on **unknown** variables (e.g. `FRCLDEGS`, `IFNEWLOAD`) are treated
as **opaque blocks**: an `unrecognized_depth` counter tracks nesting depth, and
any assignments inside an opaque block are silently skipped.  This prevents
incorrect defaults being extracted (e.g. `BATDISCH=0` from a dead branch in the
EUROPE block), which would otherwise make every GA evaluation infeasible.

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
| Snakemake `MissingOutputException` for `optimal_summary.json` | GA run failed or was killed — check `logs/run_ga_<REGION>.log`; resubmit with `--rerun-incomplete` |
| `fortran/bin/powerworld does not exist and compile: false` | Set `compile: true` in `config/workflow.yaml` or pre-build the binary on a compute node |
| Snakemake SLURM profile: `sbatch: command not found` | Profile is only valid on Sherlock; use `snakemake --cores N` for local execution |
| Overview figures (figA1–A3) missing after partial runs | Run `snakemake --cores 1 plot_overview` after at least one region completes |

---

## License

Respect the licensing terms of the original LoadMatch Fortran code and
associated datasets.  Consult the Jacobson Lab before publishing or
redistributing results.

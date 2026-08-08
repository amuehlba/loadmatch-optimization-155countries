# LoadMatch optimization: 155 countries (Paper 2)

Python driver around the Jacobson-group **LoadMatch** Fortran grid simulator
(`fortran/src/powerworld.f`). A genetic algorithm (GA) tunes the model's
capacity, storage, and dispatch factors to minimize annual system cost for a
100% clean-energy grid, across **30 regions covering 155 countries**, on 3-year
GATOR-GCMOM weather data at 30-second resolution.

The paper compares the PI's trial-and-error model against the GA optimization on
system cost and solve time, for a base (no data-center) case and five
data-center (DC) scenarios. The final deliverable per region is an `xx` file in
the PI's exact format, which the PI feeds into his own post-processing.

## Execution environment

**All Fortran compilation and all GA runs happen on Sherlock (Stanford HPC,
x86_64 Linux).** The Fortran needs `gfortran -mcmodel=medium`, which is not
available on Apple Silicon, so the binary cannot be built or run on a Mac.
Develop on the Mac, push to GitHub, pull on Sherlock, build and run there.

`data/` is git-ignored. Result trees, CSVs, and figures are not tracked; sync
them between Sherlock and your Mac manually (`rsync`/`scp`).

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Snakemake (optional, see below) requires **7.x** and **pulp==2.7.0** exactly.

## Build the Fortran (Sherlock only)

```bash
mkdir -p fortran/build fortran/bin
gfortran -O2 -mcmodel=medium \
    -fdefault-real-8 -fdefault-double-8 -fno-automatic \
    -J fortran/build fortran/src/powerworld.f \
    -o fortran/bin/powerworld
```

## How the driver works

`scripts/run_full_workflow.py` wraps the binary. The GA mutates every non-fixed
factor in `PARAM_REGISTRY`, writing candidate values to
`data/raw/fortran_factors.dat`, which the binary reads at runtime. The GA is
always seeded with `--baseline-start`:

- `defaults`: the Fortran hardcoded region values (the PI's trial-and-error
  baseline); this is the standard base-case start.
- `scratch`: the spreadsheet start (capacity factors = 1, storage = 0);
  results go to isolated `<REGION>_scratch/` directories.
- a path to a factor file: seed the GA from a specific solution.

All scripts run from the repo root as modules (`python -m scripts.<name>`).

## Running the analysis

### Full campaign (all 30 regions: base + scratch + all DC cases)

```bash
sbatch scripts/run_full_campaign_slurm.sh
```

A lightweight supervisor that keeps at most two job arrays in the queue at once:
scratch runs in the background, base runs alongside it, and once base succeeds
the five DC cases run one at a time, each seeding from the fresh base optima.

### Base optimization only (all regions)

```bash
sbatch scripts/run_all_regions_array_slurm.sh              # base, seed = defaults
sbatch scripts/run_all_regions_scratch_slurm.sh            # from-scratch comparison
```

Both are 30-task job arrays (one region per task; region list from
`config/workflow.yaml`). Throttle concurrency with `--array=0-29%8`.

### Data-center cases (all regions, one case per submission)

```bash
sbatch --export=ALL,DC_CASE=case1    scripts/run_all_regions_dc_slurm.sh   # dc1
sbatch --export=ALL,DC_CASE=case2    scripts/run_all_regions_dc_slurm.sh   # dc2
sbatch --export=ALL,DC_CASE=case3    scripts/run_all_regions_dc_slurm.sh   # dc2rc
sbatch --export=ALL,DC_CASE=case2bat scripts/run_all_regions_dc_slurm.sh   # dc2bat
sbatch --export=ALL,DC_CASE=case2h2  scripts/run_all_regions_dc_slurm.sh   # dc2h2
```

| DC case  | result dir | meaning                                                |
|----------|------------|--------------------------------------------------------|
| case1    | `_dc1`     | EGS-powered data-center load (IFDATCEN=1)              |
| case2    | `_dc2`     | re-optimize all generation + both storage (IFDATCEN=2)|
| case3    | `_dc2rc`   | rooftop PV only; utility PV and wind locked at base   |
| case2bat | `_dc2bat`  | case2 with hydrogen locked (battery storage only)     |
| case2h2  | `_dc2h2`   | case2 with battery locked (hydrogen storage only)     |

Rooftop (`case3`) is skipped for Greenland and Iceland by default; force it with
`--export=ALL,DC_CASE=case3,FORCE=1` when the PI needs those `xx` files.

### One region (interactive or debugging)

Base:
```bash
python -m scripts.run_full_workflow \
    --region EUROPE --optimizer ga --baseline-start defaults \
    --parallel-evals 24 --ga-population 37 --ga-generations 50 \
    --ga-mutation-rate 0.15 --ga-mutation-scale 0.2 --ga-elite-frac 0.2 \
    --ga-mutation-cooling 0.985 --max-land-pct 7 --no-plots
```

Data-center (example: hydrogen case for one region) adds the scenario flags and
the case lock set:
```bash
python -m scripts.run_full_workflow \
    --region SOUTHEAST-ASIA --optimizer ga --baseline-start \
    data/results_verification/SOUTHEAST-ASIA/genetic_factors.dat \
    --datacenter 2 --dc-label h2 \
    --hj-lock FACRESPV FACCOMPV CSPTURBFAC FACSHT STORHCOLD STORHHWAT STORHPHS \
              STORUGDYS CPERFORM BATDISCH STORHBAT HCDDADD FMORTBAU \
    --parallel-evals 24 --ga-population 37 --ga-generations 50 \
    --max-land-pct 7 --no-plots
```
For production runs use the DC array script, which builds the correct
`--dc-label` and `--hj-lock` set for each case automatically.

### Seeding from a PI hand solution

When the PI provides a hand-tuned solution ("the base case except for these few
factors"), build a seed and evaluate or re-optimize from it:

```bash
python -m scripts.build_seed_from_overrides \
    --base data/results_verification/SOUTHEAST-ASIA/genetic_factors.dat \
    --out  data/pi_seasia.dat \
    FACOFFWIN=1.7 FACUTILPV=2.2 FCCHARG=0.25 FCDISCH=0.25

# evaluate the exact solution under the current binary (one Fortran run):
sbatch --export=ALL,REGION=SOUTHEAST-ASIA,CASE=case2h2,\
SEEDFILE=data/pi_seasia.dat,OUTSUFFIX=pi_sea,MODE=eval \
    scripts/reoptimize_fromseed_slurm.sh
# then MODE=ga to polish it with a full GA.
```

The same script runs a from-scratch GA validation from any seed: point
`SEEDFILE` at a region's base `genetic_factors.dat` with `MODE=ga`.

## Exporting results, tables, and figures

After a campaign finishes:

```bash
bash scripts/export_all_results.sh
```

This regenerates, in order: the base comparison table
(`comparison_summary.csv`), the DC comparison table
(`dc_comparison_summary.csv`), the results workbook (`results_export.xlsx`), the
PI `xx` deliverables, and all publication figures, then runs a data-integrity
check. Afterward, pull `data/results_verification/` to your Mac for the paper.

Individual steps (all read the `*_summary.json` files in each region directory):

| Command                                       | Produces                                    |
|-----------------------------------------------|---------------------------------------------|
| `python -m scripts.export_comparison`         | base vs GA vs scratch comparison CSV        |
| `python -m scripts.export_dc_comparison`      | `dc_comparison_summary.csv`                 |
| `python -m scripts.export_results`            | `results_export.xlsx` / `.csv`              |
| `python -m scripts.rebuild_xx_deliverables`   | per-region `xx` files for the PI            |
| `python -m scripts.plot_comparison`           | base cost/structure figures                 |
| `python -m scripts.plot_dc_comparison`        | data-center figures (incl. `fig_dc_combined`) |
| `python -m scripts.plot_structure`            | what changed vs the trial-and-error model   |
| `python -m scripts.plot_results --region <R>` | per-region figures                          |

## Snakemake (optional)

The `Snakefile` encodes the per-region DAG
(`compile_fortran -> check_inputs -> preprocess_supply -> run_ga -> plot_region`,
then `plot_overview` / `export_results`). To build one target directly:

```bash
snakemake --cores 24 data/results_verification/EUROPE/optimal_summary.json
```

Region list and GA hyper-parameters come from `config/workflow.yaml`.

## Repository layout

```
scripts/
  run_full_workflow.py             core driver (GA + Fortran evaluation)
  run_full_campaign_slurm.sh       one-shot supervisor: base + scratch + all DC
  run_all_regions_array_slurm.sh   base optimization, 30-region job array
  run_all_regions_scratch_slurm.sh from-scratch comparison array
  run_all_regions_dc_slurm.sh      one DC case across all regions (DC_CASE env)
  build_seed_from_overrides.py     build a seed file from base + PI overrides
  reoptimize_fromseed_slurm.sh     evaluate/re-optimize from an arbitrary seed
  export_all_results.sh            regenerate every table, xx file, and figure
  export_comparison.py             base comparison table
  export_dc_comparison.py          data-center comparison table
  export_results.py                XLSX + CSV workbook
  rebuild_xx_deliverables.py       PI xx deliverables
  plot_results.py                  per-region figures
  plot_comparison.py               base cross-region figures
  plot_dc_comparison.py            data-center figures
  plot_dc_economics.py             data-center economics figures (standalone)
  plot_dc_tables.py                data-center tables (standalone)
  plot_structure.py                structural-change figures
  plot_style.py                    shared figure palette/style
  parse_fortran_output.py          Fortran stdout -> structured JSON
  factor_history_tools.py          factor_history.log -> DataFrames
  check_inputs.py                  input-file validation (Snakemake rule)
  list_loadreg_files.py            helper: enumerate region loadreg source files
src/
  io/dat_parser.py                 KEY = VALUE factor-file read/write
  io/data_loader.py                region load/weather data loading
  region_shortcodes.py             region -> xx short-code map
  xx_tools.py                      xx-file helpers (override-echo stripping)
fortran/
  src/powerworld.f                 working binary source (PI pristine + our edits)
  src/powerworld_replication.f     old-cost variant for the replication check
  Makefile
config/workflow.yaml               regions + GA hyper-parameters
Snakefile                          per-region pipeline DAG
powerworld_changes.diff            our edits vs the PI's pristine powerworld.f
```

## Notes

- Feasibility uses a strict per-step unmet-load tolerance (`EXCESIN > 1e-12`).
  Marginally infeasible cases are made feasible by nudging one design variable
  upward, following the PI's trial-and-error method, rather than loosening the
  tolerance.
- `--max-land-pct 7` applies a soft land-use penalty: it steers the optimizer,
  so the final solution should respect the cap but a small breach is possible.
- The `xx` files in `data/results_verification/xx_optimized_<suffix>/` are the
  final per-region deliverables the PI post-processes.

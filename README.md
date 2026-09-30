# LOADMATCH-O

LOADMATCH-O optimizes the design parameters of the LOADMATCH grid-integration
model (`fortran/src/powerworld.f`) with a genetic algorithm (GA). It minimizes
the annual energy cost of 100% wind-water-solar (WWS) systems for 30 regions
covering 155 countries, simulated in 30-second time steps over three weather
years. The repository contains the optimization driver, the SLURM scripts for
the full campaign (Baseline, Scratch and five data-center cases), and the
scripts that produce all tables and figures.

- Paper: DOI [to be added]
- Code archive (Zenodo): DOI [to be added]

## Requirements

- x86_64 Linux with `gfortran` (the model needs `-mcmodel=medium`).
- Python 3.9 or newer. The driver uses only the standard library; the exports
  and figures need the packages in `requirements.txt`.
- SLURM for the campaign scripts. They are set up for Stanford's Sherlock
  cluster (`--partition=serc`, `module load python/3.9.0`); adjust these lines
  for another cluster.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

mkdir -p fortran/build fortran/bin
gfortran -O2 -mcmodel=medium -fdefault-real-8 -fdefault-double-8 -fno-automatic \
    -J fortran/build fortran/src/powerworld.f -o fortran/bin/powerworld
```

## Input data

The input data are not part of this repository; see the data availability
statement of the paper. All inputs go in `data/raw/`:

| File | Content |
|------|---------|
| `countrystats.dat`, `heatcooldd.dat`, `heatfrac.dat` | country statistics, degree days, heat fractions |
| `wwssupworld.<REGION>` | GATOR-GCMOM wind, solar and heat supply per region |
| `ELECMAPS/loadreg.<COUNTRY>`, `ELECMAPS/24LOADELECMAPS.dat` | hourly electricity load (`python -m scripts.list_loadreg_files` lists the files needed) |
| `xx.<SHORTCODE>` (optional) | reference LOADMATCH reports of the trial-and-error solutions |

## How it works

`scripts/run_full_workflow.py` optimizes one region. Each candidate is a full
LOADMATCH run: the driver writes the 15 design variables (capacity factors of
wind, PV and solar heat; storage power and duration of batteries, hydrogen,
pumped hydro and thermal storage) to `data/raw/fortran_factors.dat`, which the
`READ_FACTOR_OVERRIDES` subroutine appended to `powerworld.f` reads after the
region defaults are set. All other parameters keep their LOADMATCH values.
Evaluations run in parallel in isolated working directories. Infeasible
designs (unmet load in any time step) are discarded, and a soft cost penalty
steers new land use below 7% of each region's area (`--max-land-pct 7`). The
GA uses a fixed random seed (`--seed`), so runs are reproducible.

The starting point is set with `--baseline-start`:

- `defaults`: the expert trial-and-error solution hardcoded in `powerworld.f` (Baseline);
- `scratch`: capacity factors 1 and no storage (Scratch);
- a `KEY = VALUE` file, e.g. a saved optimum `genetic_factors.dat`.

`powerworld_changes.diff` lists all differences from the original LOADMATCH
source (version of 2026-09-11), chiefly the factor-override subroutine,
command-line arguments for region, preprocessing mode and data-center
scenario, relative input paths, and divide-by-zero guards.

## Running

Run everything from the repository root. The complete campaign (30 regions,
all cases) is one supervisor job:

```bash
sbatch scripts/run_full_campaign_slurm.sh
```

It submits these job arrays (one task per region of `config/workflow.yaml`),
which can also be run individually:

| Script | Case | Results in `data/results_verification/` |
|--------|------|------------------------------------------|
| `run_all_regions_array_slurm.sh` | Baseline: GA from trial-and-error | `<REGION>/` |
| `run_all_regions_scratch_slurm.sh` | Scratch: GA from scratch | `<REGION>_scratch2/` |
| `run_all_regions_dc_slurm.sh` with `DC_CASE=case1` | data centers, EGS | `<REGION>_dc1/` |
| `... DC_CASE=case2` | data centers, wind + solar + batteries + H2 (WSBH) | `<REGION>_dc2/` |
| `... DC_CASE=case2bat` | as case2, batteries only (WSB) | `<REGION>_dc2bat/` |
| `... DC_CASE=case2h2` | as case2, hydrogen only (WSH) | `<REGION>_dc2h2/` |
| `... DC_CASE=case3` | data centers, rooftop PV + batteries + H2 (RBH) | `<REGION>_dc2rc/` |

For example `sbatch --export=ALL,DC_CASE=case2 scripts/run_all_regions_dc_slurm.sh`.
The data-center cases start from the Baseline optimum of each region; their
design variables are defined in `scripts/dc_cases.sh`. A single region runs
directly, e.g.

```bash
python -m scripts.run_full_workflow --region EUROPE --baseline-start defaults \
    --parallel-evals 24 --ga-population 37 --ga-generations 50 \
    --ga-mutation-cooling 0.985 --max-land-pct 7
```

Each results directory holds the summaries (`baseline_summary.json`,
`optimal_summary.json` with solve time), the optimum (`genetic_factors.dat`),
the evaluation history (`factor_history.log`) and the raw LOADMATCH reports.
The optimized report of each region is written to
`xx_optimized<SUFFIX>/xx.<SHORTCODE>`.

Two further tools: `build_seed_from_overrides.py` with
`reoptimize_fromseed_slurm.sh` evaluates or re-optimizes a data-center case
from a hand-tuned solution, and `regen_wwshourly_slurm.sh` /
`regen_wwshourly_us_dc.sh` write the hourly output (`wwshourly.<REGION>`) of
saved optima.

## Tables and figures

```bash
bash scripts/export_all_results.sh
```

writes all tables and figures to `data/results_verification/`:

| Script | Output |
|--------|--------|
| `export_comparison.py` | `comparison_summary.csv`: cost, land and solve time per region |
| `export_dc_comparison.py` | `dc_comparison_summary.csv`: data-center cases |
| `export_bau_comparison.py` | `bau_comparison_summary.csv`: WWS vs business as usual |
| `export_results.py` | `results_export.xlsx`: all metrics and factors per region and case |
| `plot_comparison.py` | `fig_cost_comparison`, `fig_land_comparison`, `fig_solve_time` |
| `plot_structure.py` | `fig_factor_changes`, `fig_cost_change_by_category`, `fig_generation_mix_change` |
| `plot_bau_comparison.py` | `fig_wws_vs_bau` |
| `plot_convergence.py` | `fig_convergence` |
| `plot_dispatch.py` | `SI_dispatch/<REGION>_dispatch`, `SI_dispatch/<REGION>_soc` |
| `plot_dc_comparison.py` | `fig_dc_combined` |
| `plot_dc_tables.py` | `fig_dc_land_use`, `fig_dc_net_jobs`, `fig_dc_land_region_heatmap`, `fig_dc_jobs_region_heatmap` |
| `plot_dc_economics.py` | `fig_dc_nameplate`, `fig_dc_lcoe`, `dc_lcoe_comparison.csv` |

`plot_bau_comparison.py`, `plot_dc_tables.py` and `plot_dc_economics.py` read
per-case Excel workbooks that are created separately by the LOADMATCH
post-processing program from the xx reports of each case. The program and the
workbooks are not part of this repository; the workbooks can be obtained from
the authors (see the data availability statement of the paper). Place them in
`data/results_verification/Tables/`.

## License

The code is released under the MIT License (`LICENSE`), except
`fortran/src/powerworld.f` and the excerpts of it in `powerworld_changes.diff`.
That file is the LOADMATCH model, (c) Mark Z. Jacobson/Stanford University,
distributed under the GNU LGPLv3 (`fortran/COPYING.LESSER`,
`fortran/COPYING`); publications that use it should cite the references
listed in its header.

#!/bin/bash
# ============================================================================
# rerun_first_feasible.sh
#
# Regenerates the results_export.csv and all method-comparison figures after
# adding "First feasible" as a fifth case.  Runs as a SLURM job so it
# survives disconnection.
#
# Usage (from repo root on Sherlock login node):
#   sbatch scripts/rerun_first_feasible.sh
#
# Nothing is re-computed in the Fortran/GA sense.  The script only:
#   1. Re-exports results_export.csv/.xlsx (adds First feasible row per region
#      by parsing existing lp_ga_factor_history.log files).
#   2. Regenerates all figures produced by plot_method_comparison.py.
#
# LP regions (for fig6–fig11 which need per-region LP data):
#   Edit LP_REGIONS below if you ran LP warm-start on more than the defaults.
#
# Monitor progress:
#   squeue -u $USER
#   tail -f logs/snakemake/rerun_ff_<JOBID>.out
# ============================================================================

# ---- SLURM directives -------------------------------------------------------
#SBATCH --job-name=lm-rerun-ff
#SBATCH --output=logs/snakemake/rerun_ff_%j.out
#SBATCH --error=logs/snakemake/rerun_ff_%j.err
#SBATCH --time=2:00:00
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=2
#SBATCH --mem=16G
#SBATCH --partition=serc

# ---- CONFIG -----------------------------------------------------------------

REPO_ROOT="$GROUP_HOME/loadmatch-python"

# Regions that have LP warm-start results (used for per-region LP figures).
# Add or remove regions to match what actually ran with lp_warmstart: true.
LP_REGIONS="EUROPE UNITED-STATES CANADA CHINA INDIA JAPAN AUSTRALIA AFRICA-EAST SOUTHEAST-ASIA RUSSIA"

# ---- END CONFIG -------------------------------------------------------------

module load python/3.12.1
source "${REPO_ROOT}/.venv/bin/activate"
cd "$REPO_ROOT" || { echo "Cannot cd to $REPO_ROOT"; exit 1; }

mkdir -p logs/snakemake figures/method_comparison

echo "======================================================================"
echo "First-feasible re-run starting"
echo "Repo  : $REPO_ROOT"
echo "Start : $(date)"
echo "Host  : $(hostname)"
echo "======================================================================"

# ── Step 1: Pull the latest code ──────────────────────────────────────────────
echo ""
echo "=== git pull ==="
git pull --ff-only

# ── Step 2: Re-export results (adds First feasible row per region) ─────────────
echo ""
echo "=== Re-exporting results ==="
# Auto-discovers all regions that have baseline_summary.json or
# optimal_summary.json, so no --regions flag needed.
python -m scripts.export_results \
    --out data/results_verification/results_export.xlsx \
    --csv data/results_verification/results_export.csv

# ── Step 3: Regenerate all method-comparison figures ──────────────────────────
echo ""
echo "=== Regenerating method-comparison figures ==="
python -m scripts.plot_method_comparison \
    --input       data/results_verification/results_export.csv \
    --output      figures/method_comparison \
    --lp-regions  $LP_REGIONS \
    --results-dir data/results_verification \
    --lp-dir      data/results_python

EXIT_CODE=$?

echo ""
echo "======================================================================"
echo "End time  : $(date)"
echo "Exit code : $EXIT_CODE"
echo "======================================================================"
echo ""
echo "Figures written to: ${REPO_ROOT}/figures/method_comparison/"
echo "Export written to : ${REPO_ROOT}/data/results_verification/results_export.{xlsx,csv}"

exit $EXIT_CODE

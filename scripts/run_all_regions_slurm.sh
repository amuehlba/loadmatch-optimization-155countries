#!/bin/bash
# ============================================================================
# run_all_regions_slurm.sh
#
# Submit the Snakemake workflow for all 29 world regions via SLURM.
#
# This script runs the Snakemake orchestrator as a single long-lived SLURM
# job.  Snakemake then submits each run_ga and plot_region rule as separate
# SLURM jobs (via --profile profiles/slurm), respecting resource declarations
# in the Snakefile and config/workflow.yaml.
#
# Usage (from repo root on Sherlock login node):
#   sbatch scripts/run_all_regions_slurm.sh
#
# To run only a subset of regions, edit config/workflow.yaml → regions: list,
# or use:
#   sbatch scripts/run_select_regions_slurm.sh
#
# Monitor progress:
#   squeue -u $USER
#   tail -f logs/snakemake/run_ga_<REGION>_<JOBID>.out
#   tail -f data/results_verification/<REGION>/factor_history.log
#   snakemake --profile profiles/slurm --summary   # shows rule status
# ============================================================================

# ---- SLURM directives (for the orchestrator job itself) --------------------
#SBATCH --job-name=loadmatch-snakemake
#SBATCH --output=logs/snakemake_orchestrator_%j.out
#SBATCH --error=logs/snakemake_orchestrator_%j.err
#SBATCH --time=72:00:00          # orchestrator must outlive all child jobs
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=2        # orchestrator itself is lightweight
#SBATCH --mem=8G
#SBATCH --partition=serc

# ---- CONFIG -----------------------------------------------------------------

REPO_ROOT="$GROUP_HOME/loadmatch-python"
PYTHON_ENV_SETUP="module load python/3.12.1 && source ${REPO_ROOT}/.venv/bin/activate"

# ---- END CONFIG -------------------------------------------------------------

eval "$PYTHON_ENV_SETUP"
cd "$REPO_ROOT" || { echo "Cannot cd to $REPO_ROOT"; exit 1; }

mkdir -p logs/snakemake

echo "======================================================================"
echo "Snakemake orchestrator starting"
echo "Repo     : $REPO_ROOT"
echo "Start    : $(date)"
echo "Host     : $(hostname)"
echo "======================================================================"

snakemake \
    --profile  profiles/slurm \
    --jobs     29 \
    --rerun-incomplete \
    "$@" \
    all

EXIT_CODE=$?

echo "======================================================================"
echo "End time : $(date)"
echo "Exit code: $EXIT_CODE"
echo "======================================================================"
exit $EXIT_CODE

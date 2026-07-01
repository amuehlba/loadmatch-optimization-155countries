#!/bin/bash
# ============================================================================
# run_all_regions_slurm.sh
#
# Submit the 30-region base-case LoadMatch GA workflow as a PERSISTENT SLURM
# supervisor job.  This one lightweight sbatch job runs the Snakemake
# orchestrator, which in turn submits each run_ga / plot_region rule as its own
# SLURM job (via --profile profiles/slurm).  Because the supervisor is itself a
# batch job, the whole run survives login-node logouts / disconnects — no
# screen/tmux needed.
#
# Runs STEP 1 only (base WWS, IFDATCEN=0).  Data-center scenarios (steps 2/3)
# are launched separately with --datacenter 1/2.
#
# Usage (from the repo root on a Sherlock login node):
#   sbatch scripts/run_all_regions_slurm.sh
#
# To run a subset, edit config/workflow.yaml -> regions:, then resubmit.
#
# Monitor progress:
#   squeue -u $USER
#   tail -f logs/snakemake_orchestrator_<JOBID>.out     # the supervisor
#   tail -f logs/snakemake/run_ga_<REGION>_<JOBID>.out  # a region's GA job
#   tail -f data/results_verification/<REGION>/factor_history.log
# ============================================================================

# ---- SLURM directives (for the supervisor job itself) ----------------------
#SBATCH --job-name=loadmatch-super
#SBATCH --output=logs/snakemake_orchestrator_%j.out
#SBATCH --error=logs/snakemake_orchestrator_%j.err
#SBATCH --time=7-00:00:00        # must outlive ALL child jobs (lower if the
                                 # partition rejects 7 days)
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=2        # supervisor itself is lightweight
#SBATCH --mem=8G
#SBATCH --partition=serc

# ---- CONFIG -----------------------------------------------------------------

# Repo root = the directory sbatch was submitted from (run sbatch from repo root).
REPO_ROOT="${SLURM_SUBMIT_DIR:-$(pwd)}"
PYTHON_ENV_SETUP="module load python/3.9.0 && source ${REPO_ROOT}/.venv/bin/activate"

# ---- END CONFIG -------------------------------------------------------------

eval "$PYTHON_ENV_SETUP"
cd "$REPO_ROOT" || { echo "Cannot cd to $REPO_ROOT"; exit 1; }

mkdir -p logs/snakemake

# Clear any stale Snakemake lock from a previously killed supervisor (no-op if none).
snakemake --unlock --profile profiles/slurm 2>/dev/null || true

echo "======================================================================"
echo "Snakemake supervisor starting"
echo "Repo     : $REPO_ROOT"
echo "Start    : $(date)"
echo "Host     : $(hostname)"
echo "======================================================================"

# --keep-going    : one region's failure won't stop the other 29.
# --restart-times : retry a transiently-failed child job (flaky node etc.).
snakemake \
    --profile          profiles/slurm \
    --jobs             30 \
    --keep-going \
    --restart-times    2 \
    --rerun-incomplete \
    "$@" \
    all

EXIT_CODE=$?

echo "======================================================================"
echo "End time : $(date)"
echo "Exit code: $EXIT_CODE"
echo "======================================================================"
exit $EXIT_CODE

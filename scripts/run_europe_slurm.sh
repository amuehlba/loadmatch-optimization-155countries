#!/bin/bash
# ============================================================================
# run_europe_slurm.sh
#
# Single SLURM job for the EUROPE region only.  Useful for rapid testing of
# the full GA pipeline before committing a full 29-region run.
#
# Uses Snakemake to run the pipeline in correct rule order:
#   run_ga (EUROPE) → plot_region (EUROPE)
#
# Usage (from repo root on Sherlock login node):
#   sbatch scripts/run_europe_slurm.sh
#
# Monitor progress:
#   squeue -u $USER
#   tail -f logs/snakemake/run_ga_EUROPE_*.out
#   tail -f data/results_verification/EUROPE/factor_history.log
# ============================================================================

# ---- SLURM directives (orchestrator job) ------------------------------------
#SBATCH --job-name=loadmatch-europe
#SBATCH --output=logs/snakemake_europe_%j.out
#SBATCH --error=logs/snakemake_europe_%j.err
#SBATCH --time=72:00:00          # orchestrator must outlive the child GA job
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=2
#SBATCH --mem=8G
#SBATCH --partition=serc

# ---- CONFIG -----------------------------------------------------------------

REPO_ROOT="$GROUP_HOME/loadmatch-python"
PYTHON_ENV_SETUP="module load python/3.12.1 && source ${REPO_ROOT}/.venv/bin/activate"

# Override regions list to only EUROPE.
EUROPE_CONFIG="config/europe_only.yaml"

# ---- END CONFIG -------------------------------------------------------------

eval "$PYTHON_ENV_SETUP"
cd "$REPO_ROOT" || { echo "Cannot cd to $REPO_ROOT"; exit 1; }

mkdir -p logs/snakemake config

cat > "$EUROPE_CONFIG" <<'EOF'
regions:
  - EUROPE
EOF

echo "======================================================================"
echo "Snakemake orchestrator (EUROPE only) starting"
echo "Repo     : $REPO_ROOT"
echo "Start    : $(date)"
echo "Host     : $(hostname)"
echo "======================================================================"

snakemake \
    --profile    profiles/slurm \
    --configfile "$EUROPE_CONFIG" \
    --jobs       1 \
    --rerun-incomplete \
    all

EXIT_CODE=$?

echo "======================================================================"
echo "End time  : $(date)"
echo "Exit code : $EXIT_CODE"
echo "======================================================================"
exit $EXIT_CODE

#!/bin/bash
# ============================================================================
# run_europe_slurm.sh
#
# Single SLURM job for the EUROPE region only (for testing).
#
# Usage (from repo root on Sherlock login node):
#   sbatch scripts/run_europe_slurm.sh
#
# Monitor progress:
#   squeue -u $USER
#   tail -f logs/slurm_europe_<jobid>.out
#   tail -f data/results_EUROPE/factor_history.log
# ============================================================================

# ---- SLURM directives -------------------------------------------------------
#SBATCH --job-name=loadmatch-europe
#SBATCH --output=logs/slurm_europe_%j.out
#SBATCH --error=logs/slurm_europe_%j.err
#SBATCH --time=48:00:00
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=24                 # must equal PARALLEL_EVALS below
#SBATCH --mem=200G
#SBATCH --partition=serc

# ---- CONFIG -----------------------------------------------------------------

REGION="EUROPE"
PARALLEL_EVALS=24

# GA settings
GA_POPULATION=37
GA_GENERATIONS=50
GA_MUTATION_RATE=0.15
GA_MUTATION_SCALE=0.2
GA_ELITE_FRAC=0.2
GA_COOLING=0.985

BASELINE_DIR="data/raw"
REPO_ROOT="$GROUP_HOME/loadmatch-python"
PYTHON_ENV_SETUP="module load python/3.12.1 && source ${REPO_ROOT}/.venv/bin/activate"

# ---- END CONFIG -------------------------------------------------------------

echo "======================================================================"
echo "Job ID  : $SLURM_JOB_ID"
echo "Region  : $REGION"
echo "Start   : $(date)"
echo "Host    : $(hostname)"
echo "======================================================================"

eval "$PYTHON_ENV_SETUP"
cd "$REPO_ROOT" || { echo "Cannot cd to $REPO_ROOT"; exit 1; }
mkdir -p logs

BASELINE_START="defaults"
CANDIDATE="${REPO_ROOT}/${BASELINE_DIR}/baseline_results.${REGION}.dat"
if [ -f "$CANDIDATE" ]; then
    BASELINE_START="$CANDIDATE"
    echo "Baseline : $CANDIDATE"
else
    echo "Baseline : Fortran region defaults (no file at $CANDIDATE)"
fi

CMD=(
    python -m scripts.run_full_workflow
    --region              "$REGION"
    --optimizer           ga
    --parallel-evals      "$PARALLEL_EVALS"
    --ga-population       "$GA_POPULATION"
    --ga-generations      "$GA_GENERATIONS"
    --ga-mutation-rate    "$GA_MUTATION_RATE"
    --ga-mutation-scale   "$GA_MUTATION_SCALE"
    --ga-elite-frac       "$GA_ELITE_FRAC"
    --ga-mutation-cooling "$GA_COOLING"
    --baseline-start      "$BASELINE_START"
)

echo "Running: ${CMD[*]}"
echo "======================================================================"
"${CMD[@]}"
EXIT_CODE=$?

echo "======================================================================"
echo "End time  : $(date)"
echo "Exit code : $EXIT_CODE"
echo "======================================================================"
exit $EXIT_CODE

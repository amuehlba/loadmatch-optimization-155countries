#!/bin/bash
# ============================================================================
# run_all_regions_slurm.sh
#
# SLURM job array running the LoadMatch GA optimizer for all 29 world regions.
# One SLURM task per region; each task uses PARALLEL_EVALS CPUs for
# intra-GA parallelism (matching the --parallel-evals argument).
#
# Usage (from repo root on Sherlock login node):
#   sbatch scripts/run_all_regions_slurm.sh
#
# Monitor progress:
#   squeue -u $USER
#   tail -f logs/slurm_<jobid>_<idx>.out
#   tail -f data/results_verification/<REGION>/factor_history.log
# ============================================================================

# ---- SLURM directives -------------------------------------------------------
#SBATCH --job-name=loadmatch-all
#SBATCH --output=logs/slurm_%A_%a.out      # %A = job id, %a = array index
#SBATCH --error=logs/slurm_%A_%a.err
#SBATCH --array=0-28                        # 29 regions: index 0-28
#SBATCH --time=48:00:00
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=24                 # must equal PARALLEL_EVALS below
#SBATCH --mem=200G
#SBATCH --partition=serc

# ---- CONFIG -----------------------------------------------------------------

REGIONS=(
    "AFRICA-EAST"
    "AFRICA-NORTH"
    "AFRICA-SOUTH"
    "AFRICA-WEST"
    "AUSTRALIA"
    "CANADA"
    "CENTRAL-AMERIC"
    "CENTRAL-ASIA"
    "CHINA"
    "CUBA"
    "EUROPE"
    "HAITI"
    "ICELAND"
    "INDIA"
    "ISRAEL"
    "JAMAICA"
    "JAPAN"
    "MADAGASCAR"
    "MAURITIUS"
    "MIDEAST"
    "NEW-ZEALAND"
    "PHILIPPINES"
    "RUSSIA"
    "SOUTHAM-NW"
    "SOUTHAM-SE"
    "SOUTHEAST-ASIA"
    "SOUTH-KOREA"
    "TAIWAN"
    "UNITED-STATES"
)

# Number of parallel Fortran evaluations per job (= cpus-per-task above).
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

REGION="${REGIONS[$SLURM_ARRAY_TASK_ID]}"

echo "======================================================================"
echo "Job array ID : $SLURM_ARRAY_JOB_ID  task: $SLURM_ARRAY_TASK_ID"
echo "Region       : $REGION"
echo "Start time   : $(date)"
echo "Host         : $(hostname)"
echo "======================================================================"

eval "$PYTHON_ENV_SETUP"
cd "$REPO_ROOT" || { echo "Cannot cd to $REPO_ROOT"; exit 1; }
mkdir -p logs

BASELINE_START="defaults"
if [ -n "$BASELINE_DIR" ]; then
    CANDIDATE="${REPO_ROOT}/${BASELINE_DIR}/baseline_results.${REGION}.dat"
    if [ -f "$CANDIDATE" ]; then
        BASELINE_START="$CANDIDATE"
        echo "Baseline     : $CANDIDATE"
    else
        echo "Baseline     : Fortran region defaults (no file at $CANDIDATE)"
    fi
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
echo "End time : $(date)"
echo "Exit code: $EXIT_CODE"
echo "======================================================================"
exit $EXIT_CODE

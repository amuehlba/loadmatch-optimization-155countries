#!/bin/bash
# ============================================================================
# run_regions_slurm.sh
#
# SLURM job array that runs the LoadMatch GA optimizer for a list of regions.
# One SLURM task per region.  Each task uses PARALLEL_EVALS CPUs for
# intra-GA parallelism (matching the --parallel-evals argument).
#
# Usage:
#   sbatch scripts/run_regions_slurm.sh
#
# Adjust the variables in the CONFIG section below before submitting.
# ============================================================================

# ---- SLURM directives -------------------------------------------------------
#SBATCH --job-name=loadmatch
#SBATCH --output=logs/slurm_%A_%a.out      # %A = job id, %a = array index
#SBATCH --error=logs/slurm_%A_%a.err
#SBATCH --time=24:00:00                    # wall-clock limit per region
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=36                 # must equal PARALLEL_EVALS below
#SBATCH --mem=32G
# Uncomment and set a partition if needed:
# #SBATCH --partition=normal

# ---- CONFIG -----------------------------------------------------------------

# List of region names (must match GRIDUSE values in powerworld.f).
# The job array index selects from this list.
REGIONS=(
    "UNITED-STATES"
    "EUROPE"
    "CHINA"
    "INDIA"
    "CANADA"
    "AUSTRALIA"
    "JAPAN"
    "SOUTH-AMERICA"
    "AFRICA"
    "MIDEAST"
    "RUSSIA"
    "SOUTHEAST-ASIA"
)

# Number of parallel Fortran evaluations per job.
# Must equal --cpus-per-task above.
PARALLEL_EVALS=36

# GA settings
GA_POPULATION=36
GA_GENERATIONS=80
GA_MUTATION_RATE=0.15
GA_MUTATION_SCALE=0.2
GA_ELITE_FRAC=0.2
GA_COOLING=0.985

# Per-region baseline files used as GA warm-start (skips the LP phase).
# Convention: data/raw/baseline_results.<REGION>.dat
# If the file for a region does not exist, the LP is run instead (requires Gurobi).
# Set BASELINE_DIR="" to always run the LP for all regions.
BASELINE_DIR="data/raw"

# Python environment — adjust to match your cluster setup.
# Examples: "module load python/3.11", "conda activate loadmatch", etc.
PYTHON_ENV_SETUP="conda activate loadmatch"

# Root directory of the repository on the cluster.
REPO_ROOT="$HOME/loadmatch-python"

# ---- END CONFIG -------------------------------------------------------------

# Validate job array bounds
N_REGIONS=${#REGIONS[@]}
#SBATCH --array=0-11%4        # adjust upper bound to N_REGIONS-1; %4 = max 4 concurrent

# Safety check: abort if array index is out of range
if [ "$SLURM_ARRAY_TASK_ID" -ge "$N_REGIONS" ]; then
    echo "Array index $SLURM_ARRAY_TASK_ID >= N_REGIONS=$N_REGIONS — nothing to do."
    exit 0
fi

REGION="${REGIONS[$SLURM_ARRAY_TASK_ID]}"

# Auto-select per-region baseline file if it exists
BASELINE_START=""
if [ -n "$BASELINE_DIR" ]; then
    CANDIDATE="${REPO_ROOT}/${BASELINE_DIR}/baseline_results.${REGION}.dat"
    if [ -f "$CANDIDATE" ]; then
        BASELINE_START="$CANDIDATE"
    else
        echo "No baseline file found at $CANDIDATE — will run LP phase."
    fi
fi

echo "======================================================================"
echo "Job array ID : $SLURM_ARRAY_JOB_ID  task: $SLURM_ARRAY_TASK_ID"
echo "Region       : $REGION"
echo "Baseline     : ${BASELINE_START:-<LP run>}"
echo "Start time   : $(date)"
echo "Host         : $(hostname)"
echo "======================================================================"

# Set up environment
eval "$PYTHON_ENV_SETUP"
cd "$REPO_ROOT" || { echo "Cannot cd to $REPO_ROOT"; exit 1; }

# Create log directory (in case it doesn't exist yet)
mkdir -p logs

# Build the command
CMD=(
    python -m scripts.run_full_workflow
    --region         "$REGION"
    --optimizer      ga
    --parallel-evals "$PARALLEL_EVALS"
    --ga-population  "$GA_POPULATION"
    --ga-generations "$GA_GENERATIONS"
    --ga-mutation-rate   "$GA_MUTATION_RATE"
    --ga-mutation-scale  "$GA_MUTATION_SCALE"
    --ga-elite-frac      "$GA_ELITE_FRAC"
    --ga-mutation-cooling "$GA_COOLING"
)

if [ -n "$BASELINE_START" ]; then
    CMD+=(--baseline-start "$BASELINE_START")
fi

echo "Running: ${CMD[*]}"
"${CMD[@]}"
EXIT_CODE=$?

echo "======================================================================"
echo "End time : $(date)"
echo "Exit code: $EXIT_CODE"
echo "======================================================================"
exit $EXIT_CODE

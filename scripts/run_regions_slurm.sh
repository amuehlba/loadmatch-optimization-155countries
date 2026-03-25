#!/bin/bash
# ============================================================================
# run_regions_slurm.sh
#
# SLURM job array that runs the LoadMatch GA optimizer for a list of regions.
# One SLURM task per region.  Each task uses PARALLEL_EVALS CPUs for
# intra-GA parallelism (matching the --parallel-evals argument).
#
# Usage (from repo root on Sherlock login node):
#   sbatch scripts/run_regions_slurm.sh
#
# Monitor progress:
#   squeue -u $USER
#   tail -f logs/slurm_<jobid>_0.out          # UNITED-STATES
#   tail -f logs/slurm_<jobid>_1.out          # CANADA
#   tail -f data/results_<region>/factor_history.log
# ============================================================================

# ---- SLURM directives -------------------------------------------------------
#SBATCH --job-name=loadmatch-ga-regions
#SBATCH --output=logs/slurm_%A_%a.out      # %A = job id, %a = array index
#SBATCH --error=logs/slurm_%A_%a.err
#SBATCH --array=0-1                        # 2 regions: index 0 and 1
#SBATCH --time=48:00:00                   # wall-clock limit per region
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=37                 # must equal PARALLEL_EVALS below
#SBATCH --mem=128G
#SBATCH --partition=serc

# ---- CONFIG -----------------------------------------------------------------

REGIONS=(
    "UNITED-STATES"
    "CANADA"
)

# Number of parallel Fortran evaluations per job (= cpus-per-task above).
PARALLEL_EVALS=37

# GA settings
GA_POPULATION=37
GA_GENERATIONS=50
GA_MUTATION_RATE=0.15
GA_MUTATION_SCALE=0.2
GA_ELITE_FRAC=0.2
GA_COOLING=0.985

# Per-region baseline warm-start.
# If a baseline_results.<REGION>.dat file exists it is used; otherwise the
# Fortran hardcoded region defaults are used (--baseline-start defaults).
# This avoids needing Gurobi/LP on the cluster.
BASELINE_DIR="data/raw"

# Root directory of the repository on the cluster.
# GROUP_HOME is set by Sherlock to /home/groups/<PI_name>.
REPO_ROOT="$GROUP_HOME/loadmatch-python"

# Python environment.
# Load Python 3.12 so the venv uses a consistent interpreter on all nodes.
# Gurobi module only needed if --baseline-start is a file path (LP warm-start).
PYTHON_ENV_SETUP="module load python/3.12.1 && source ${REPO_ROOT}/.venv/bin/activate"

# ---- END CONFIG -------------------------------------------------------------

REGION="${REGIONS[$SLURM_ARRAY_TASK_ID]}"

echo "======================================================================"
echo "Job array ID : $SLURM_ARRAY_JOB_ID  task: $SLURM_ARRAY_TASK_ID"
echo "Region       : $REGION"
echo "Start time   : $(date)"
echo "Host         : $(hostname)"
echo "======================================================================"

# Set up environment
eval "$PYTHON_ENV_SETUP"
cd "$REPO_ROOT" || { echo "Cannot cd to $REPO_ROOT"; exit 1; }

# Create log directory (Slurm writes here before the job starts too)
mkdir -p logs

# Select baseline warm-start: prefer existing file, fall back to Fortran defaults
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

# Build the command
CMD=(
    python -m scripts.run_full_workflow
    --region             "$REGION"
    --optimizer          ga
    --parallel-evals     "$PARALLEL_EVALS"
    --ga-population      "$GA_POPULATION"
    --ga-generations     "$GA_GENERATIONS"
    --ga-mutation-rate   "$GA_MUTATION_RATE"
    --ga-mutation-scale  "$GA_MUTATION_SCALE"
    --ga-elite-frac      "$GA_ELITE_FRAC"
    --ga-mutation-cooling "$GA_COOLING"
    --baseline-start     "$BASELINE_START"
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

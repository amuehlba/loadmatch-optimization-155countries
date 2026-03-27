#!/bin/bash
# ============================================================================
# run_select_regions_slurm.sh
#
# SLURM job array for a curated set of 10 geographically diverse regions —
# chosen to span major resource contrasts and policy contexts.
#
#   idx  Region          Rationale
#   ---  ------          ---------
#    0   UNITED-STATES   Reference region; high solar/wind/hydro mix
#    1   CANADA          Hydro-dominant; similar data quality to USA
#    2   EUROPE          Policy-critical; high offshore wind
#    3   CHINA           Largest emitter; rapid buildout
#    4   INDIA           Fast-growing demand; high solar
#    5   JAPAN           Island; resource-constrained
#    6   AUSTRALIA       World-leading solar/wind resource
#    7   AFRICA-EAST     Geothermal-rich; developing
#    8   SOUTHEAST-ASIA  Tropical; biomass + solar mix
#    9   RUSSIA          Cold climate; large land; fossil-heavy baseline
#
# Usage (from repo root on Sherlock login node):
#   sbatch scripts/run_select_regions_slurm.sh
#
# Monitor progress:
#   squeue -u $USER
#   tail -f logs/slurm_<jobid>_<idx>.out
#   tail -f data/results_verification/<REGION>/factor_history.log
# ============================================================================

# ---- SLURM directives -------------------------------------------------------
#SBATCH --job-name=loadmatch-select
#SBATCH --output=logs/slurm_%A_%a.out      # %A = job id, %a = array index
#SBATCH --error=logs/slurm_%A_%a.err
#SBATCH --array=0-9                         # 10 regions: index 0-9
#SBATCH --time=48:00:00
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=24                 # must equal PARALLEL_EVALS below
#SBATCH --mem=200G
#SBATCH --partition=serc

# ---- CONFIG -----------------------------------------------------------------

REGIONS=(
    "UNITED-STATES"
    "CANADA"
    "EUROPE"
    "CHINA"
    "INDIA"
    "JAPAN"
    "AUSTRALIA"
    "AFRICA-EAST"
    "SOUTHEAST-ASIA"
    "RUSSIA"
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

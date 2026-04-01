#!/bin/bash
# ============================================================================
# run_select_regions_slurm.sh
#
# Submit the Snakemake workflow for a curated 10-region subset via SLURM.
# Snakemake submits each run_ga and plot_region rule as individual SLURM jobs
# using --profile profiles/slurm.
#
# Selected regions span major resource contrasts and policy contexts:
#   UNITED-STATES   Reference; high solar/wind/hydro
#   CANADA          Hydro-dominant
#   EUROPE          Policy-critical; high offshore wind
#   CHINA           Largest emitter; rapid buildout
#   INDIA           Fast-growing demand; high solar
#   JAPAN           Island; resource-constrained
#   AUSTRALIA       World-leading solar/wind resource
#   AFRICA-EAST     Geothermal-rich; developing
#   SOUTHEAST-ASIA  Tropical; biomass + solar
#   RUSSIA          Cold climate; fossil-heavy baseline
#
# Usage (from repo root on Sherlock login node):
#   sbatch scripts/run_select_regions_slurm.sh
#
# To run all 29 regions:
#   sbatch scripts/run_all_regions_slurm.sh
#
# Monitor progress:
#   squeue -u $USER
#   tail -f logs/snakemake/run_ga_<REGION>_<JOBID>.out
#   snakemake --profile profiles/slurm --summary
# ============================================================================

# ---- SLURM directives (for the orchestrator job itself) --------------------
#SBATCH --job-name=loadmatch-select
#SBATCH --output=logs/snakemake_select_%j.out
#SBATCH --error=logs/snakemake_select_%j.err
#SBATCH --time=72:00:00          # orchestrator must outlive all child jobs
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=2
#SBATCH --mem=8G
#SBATCH --partition=serc

# ---- CONFIG -----------------------------------------------------------------

REPO_ROOT="$GROUP_HOME/loadmatch-python"
PYTHON_ENV_SETUP="module load python/3.12.1 && source ${REPO_ROOT}/.venv/bin/activate"

# Temporary config file that limits the region list to these 10.
SELECT_CONFIG="config/select_regions.yaml"

# ---- END CONFIG -------------------------------------------------------------

eval "$PYTHON_ENV_SETUP"
cd "$REPO_ROOT" || { echo "Cannot cd to $REPO_ROOT"; exit 1; }

mkdir -p logs/snakemake config

# Write a temporary config that overrides just the regions list.
cat > "$SELECT_CONFIG" <<'EOF'
regions:
  - UNITED-STATES
  - CANADA
  - EUROPE
  - CHINA
  - INDIA
  - JAPAN
  - AUSTRALIA
  - AFRICA-EAST
  - SOUTHEAST-ASIA
  - RUSSIA
EOF

echo "======================================================================"
echo "Snakemake orchestrator (10-region subset) starting"
echo "Repo     : $REPO_ROOT"
echo "Start    : $(date)"
echo "Host     : $(hostname)"
echo "======================================================================"

snakemake \
    --profile     profiles/slurm \
    --configfile  "$SELECT_CONFIG" \
    --jobs        10 \
    --rerun-incomplete \
    all

EXIT_CODE=$?

echo "======================================================================"
echo "End time : $(date)"
echo "Exit code: $EXIT_CODE"
echo "======================================================================"
exit $EXIT_CODE

#!/bin/bash
# ============================================================================
# run_all_regions_array_slurm.sh
#
# Run the 30-region base-case GA optimization as a SLURM JOB ARRAY — one array
# task per region, no Snakemake.  Each task is its own SLURM job, so the whole
# run is persistent (survives login-node logout) with no supervisor process.
#
# Runs STEP 1 only (base WWS, IFDATCEN=0).  Reporting (cross-region figures +
# comparison_summary.csv) is a quick separate step once the array finishes —
# see the end of this file.
#
# Usage (from the repo root on a Sherlock login node):
#   sbatch scripts/run_all_regions_array_slurm.sh
#
# The region list is read from config/workflow.yaml, so keep --array=0-29 in
# sync with the number of regions there (currently 30).
#
# Monitor:
#   squeue -u $USER
#   tail -f logs/slurm_ga_<ARRAYID>_<TASKID>.out
#   tail -f data/results_verification/<REGION>/factor_history.log
# ============================================================================

#SBATCH --job-name=lm-ga
#SBATCH --output=logs/slurm_ga_%A_%a.out
#SBATCH --error=logs/slurm_ga_%A_%a.err
#SBATCH --array=0-29                 # 30 regions (indices 0..29)
#SBATCH --time=48:00:00
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=24           # = --parallel-evals below
#SBATCH --mem=200G                   # lower (e.g. 64G) if jobs pend for resources
#SBATCH --partition=serc
# Tip: to cap how many run at once, submit with e.g.
#   sbatch --array=0-29%8 scripts/run_all_regions_array_slurm.sh

set -euo pipefail

REPO_ROOT="${SLURM_SUBMIT_DIR:-$(pwd)}"
cd "$REPO_ROOT"

module load python/3.9.0
source .venv/bin/activate

# Region list from config/workflow.yaml (the `regions:` YAML list).
mapfile -t REGIONS < <(sed -n 's/^[[:space:]]*-[[:space:]]*//p' config/workflow.yaml \
                       | grep -E '^[A-Z]' )
REGION="${REGIONS[$SLURM_ARRAY_TASK_ID]}"

echo "=================================================================="
echo "Array task : $SLURM_ARRAY_TASK_ID   Region: $REGION"
echo "Start      : $(date)   Host: $(hostname)"
echo "=================================================================="

python -m scripts.run_full_workflow \
    --region          "$REGION" \
    --optimizer       ga \
    --parallel-evals  "${SLURM_CPUS_PER_TASK:-24}" \
    --ga-population    37 \
    --ga-generations   50 \
    --ga-mutation-rate 0.15 \
    --ga-mutation-scale 0.2 \
    --ga-elite-frac    0.2 \
    --ga-mutation-cooling 0.985 \
    --baseline-start   defaults \
    --no-plots

echo "Done: $REGION   $(date)"

# ----------------------------------------------------------------------------
# After the whole array finishes, generate figures + the comparison table
# (light; run on a login node or in a small salloc):
#
#   source .venv/bin/activate
#   python -m scripts.plot_results --all-regions        # per-region + figA1-A5
#   python -m scripts.export_comparison                 # comparison_summary.csv
#   python -m scripts.export_results --regions $(sed -n 's/^[[:space:]]*-[[:space:]]*//p' config/workflow.yaml | grep -E '^[A-Z]' | tr '\n' ' ')
# ----------------------------------------------------------------------------

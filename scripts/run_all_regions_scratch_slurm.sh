#!/bin/bash
# ============================================================================
# run_all_regions_scratch_slurm.sh
#
# SLURM job array: GA optimization for all 30 regions starting FROM SCRATCH,
# i.e. from the spreadsheet values (all capacity factors = 1, storage design
# variables = 0) instead of the PI's trial-and-error solution.  Addresses the
# question whether the optimizer needs the expert starting point at all.
#
# Identical GA budget to the base run (population 37 x 50 generations) so the
# two starts are compared at equal search effort.  A few extra evaluations are
# spent up front ramping storage to find a first feasible point.
#
# Results are fully isolated from the base case:
#   data/results_verification/<REGION>_scratch/        summaries, logs, raw outs
#   data/results_verification/xx_optimized_scratch/    xx.<SHORTCODE> deliverables
#
# Usage (from the repo root on a Sherlock login node):
#   sbatch scripts/run_all_regions_scratch_slurm.sh
#
# Keep --array=0-29 in sync with the region count in config/workflow.yaml.
# ============================================================================

#SBATCH --job-name=lm-ga-scratch
#SBATCH --output=logs/slurm_ga_scratch_%A_%a.out
#SBATCH --error=logs/slurm_ga_scratch_%A_%a.err
#SBATCH --array=0-29
#SBATCH --time=48:00:00
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=24
#SBATCH --mem=200G
#SBATCH --partition=serc

set -euo pipefail

REPO_ROOT="${SLURM_SUBMIT_DIR:-$(pwd)}"
cd "$REPO_ROOT"

module load python/3.9.0
source .venv/bin/activate

mapfile -t REGIONS < <(sed -n 's/^[[:space:]]*-[[:space:]]*//p' config/workflow.yaml \
                       | grep -E '^[A-Z]' )
REGION="${REGIONS[$SLURM_ARRAY_TASK_ID]}"

echo "=================================================================="
echo "Array task : $SLURM_ARRAY_TASK_ID   Region: $REGION   (SCRATCH start)"
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
    --baseline-start   scratch \
    --no-plots

echo "Done: $REGION (scratch)   $(date)"

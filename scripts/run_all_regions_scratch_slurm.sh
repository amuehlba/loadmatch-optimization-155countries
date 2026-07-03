#!/bin/bash
# ============================================================================
# run_all_regions_scratch_slurm.sh
#
# SLURM job array: GA optimization for all 30 regions starting FROM SCRATCH,
# i.e. from the spreadsheet values (all capacity factors = 1, storage design
# variables = 0) instead of the PI's trial-and-error solution.  Addresses the
# question whether the optimizer needs the expert starting point at all.
#
# EXTENDED search budget vs the base run: population 48 (fills 2 x 24-core
# batches exactly, so it costs no extra wall-clock per generation vs 37),
# 150 generations, and slower mutation cooling (0.995 vs 0.985) so the search
# stays mobile long enough to traverse the larger distance from the scratch
# start.  The bootstrap additionally bisects back toward the feasibility
# boundary after the storage ramp / capacity inflation, and the initial
# population is seeded with spread variants (storage down, capacity up/down).
# The first scratch campaign at the equal budget (37 x 50) is preserved in
# results_export.xlsx; disclose the budget difference when reporting.
#
# Results are fully isolated from the base case AND from the first scratch
# campaign (which stays untouched in <REGION>_scratch/):
#   data/results_verification/<REGION>_scratch2/       summaries, logs, raw outs
#   data/results_verification/xx_optimized_scratch2/   xx.<SHORTCODE> deliverables
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
    --ga-population    48 \
    --ga-generations   150 \
    --ga-mutation-rate 0.15 \
    --ga-mutation-scale 0.2 \
    --ga-elite-frac    0.2 \
    --ga-mutation-cooling 0.995 \
    --baseline-start   scratch \
    --max-land-pct     7 \
    --scratch-label    scratch2 \
    --no-plots

echo "Done: $REGION (scratch)   $(date)"

#!/bin/bash
# ============================================================================
# run_all_regions_scratch_slurm.sh
#
# Scratch optimization: GA started from the spreadsheet values (all capacity
# factors = 1, storage design variables = 0) instead of the trial-and-error
# solution, one SLURM array task per region.
#
# Larger search budget than the baseline optimization: population 48 (two full
# 24-core batches per generation, so no extra wall-clock per generation vs 37),
# 150 generations, and slower mutation cooling (0.995), so the search stays
# mobile over the larger distance from the scratch start.
#
# Usage (from the repo root):
#   sbatch scripts/run_all_regions_scratch_slurm.sh
#
# Results: data/results_verification/<REGION>_scratch2/ and
#          xx_optimized_scratch2/.
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
    --parallel-evals  "${SLURM_CPUS_PER_TASK:-24}" \
    --ga-population    48 \
    --ga-generations   150 \
    --ga-mutation-rate 0.15 \
    --ga-mutation-scale 0.2 \
    --ga-elite-frac    0.2 \
    --ga-mutation-cooling 0.995 \
    --baseline-start   scratch \
    --max-land-pct     7 \
    --scratch-label    scratch2

echo "Done: $REGION (scratch)   $(date)"

#!/bin/bash
# ============================================================================
# run_all_regions_array_slurm.sh
#
# Baseline optimization (no data centers): GA seeded from the expert
# trial-and-error solution (the region values hardcoded in powerworld.f), one
# SLURM array task per region.
#
# Usage (from the repo root):
#   sbatch scripts/run_all_regions_array_slurm.sh
#   sbatch --array=0-29%8 scripts/run_all_regions_array_slurm.sh   # throttled
#
# Array index i runs the i-th region of config/workflow.yaml.
# Results: data/results_verification/<REGION>/ and xx_optimized/.
# ============================================================================

#SBATCH --job-name=lm-ga
#SBATCH --output=logs/slurm_ga_%A_%a.out
#SBATCH --error=logs/slurm_ga_%A_%a.err
#SBATCH --array=0-29
#SBATCH --time=48:00:00
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=24           # = --parallel-evals below
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
echo "Array task : $SLURM_ARRAY_TASK_ID   Region: $REGION"
echo "Start      : $(date)   Host: $(hostname)"
echo "=================================================================="

python -m scripts.run_full_workflow \
    --region          "$REGION" \
    --parallel-evals  "${SLURM_CPUS_PER_TASK:-24}" \
    --ga-population    37 \
    --ga-generations   50 \
    --ga-mutation-rate 0.15 \
    --ga-mutation-scale 0.2 \
    --ga-elite-frac    0.2 \
    --ga-mutation-cooling 0.985 \
    --baseline-start   defaults \
    --max-land-pct     7

echo "Done: $REGION   $(date)"

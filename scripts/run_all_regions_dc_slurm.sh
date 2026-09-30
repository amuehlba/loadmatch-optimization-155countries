#!/bin/bash
# ============================================================================
# run_all_regions_dc_slurm.sh
#
# One data-center case for all regions, one SLURM array task per region.
# Select the case with DC_CASE (default case1; see scripts/dc_cases.sh):
#
#   sbatch --export=ALL,DC_CASE=case1    scripts/run_all_regions_dc_slurm.sh
#   sbatch --export=ALL,DC_CASE=case2    scripts/run_all_regions_dc_slurm.sh
#   sbatch --export=ALL,DC_CASE=case3    scripts/run_all_regions_dc_slurm.sh
#   sbatch --export=ALL,DC_CASE=case2bat scripts/run_all_regions_dc_slurm.sh
#   sbatch --export=ALL,DC_CASE=case2h2  scripts/run_all_regions_dc_slurm.sh
#
# Every case is seeded from the region's no-data-center optimum,
# data/results_verification/<REGION>/genetic_factors.dat, so the baseline
# optimization (run_all_regions_array_slurm.sh) must have finished first.
# Results are isolated per case (<REGION>_dc*/, xx_optimized_dc*/).
#
# Rooftop PV (case3) is skipped for Greenland and Iceland, where it is not a
# viable strategy (tiny rooftop base capacity, polar-night winters); set
# FORCE=1 to run it anyway.
# ============================================================================

#SBATCH --job-name=lm-ga-dc
#SBATCH --output=logs/slurm_dc_%A_%a.out
#SBATCH --error=logs/slurm_dc_%A_%a.err
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
source scripts/dc_cases.sh

mapfile -t REGIONS < <(sed -n 's/^[[:space:]]*-[[:space:]]*//p' config/workflow.yaml \
                       | grep -E '^[A-Z]' )
REGION="${REGIONS[$SLURM_ARRAY_TASK_ID]}"

DC_CASE="${DC_CASE:-case1}"
dc_case "$DC_CASE"

if [ "$DC_CASE" = "case3" ] && { [ "$REGION" = "GREENLAND" ] || [ "$REGION" = "ICELAND" ]; } && [ "${FORCE:-0}" != "1" ]; then
  echo "Skipping case3 for $REGION (rooftop strategy not applicable; set FORCE=1 to run)."
  exit 0
fi

SEED="data/results_verification/${REGION}/genetic_factors.dat"
if [ ! -f "$SEED" ]; then
  echo "ERROR: no-data-center optimum not found: $SEED"
  echo "Run the baseline optimization (run_all_regions_array_slurm.sh) first."
  exit 1
fi

echo "=================================================================="
echo "Array task : $SLURM_ARRAY_TASK_ID   Region: $REGION"
echo "DC case    : $DC_CASE  (IFDATCEN=$DATACENTER, label='$LABEL')"
echo "Locked     : ${LOCKS:-none}"
echo "Start      : $(date)   Host: $(hostname)"
echo "=================================================================="

# shellcheck disable=SC2086
python -m scripts.run_full_workflow \
    --region          "$REGION" \
    --parallel-evals  "${SLURM_CPUS_PER_TASK:-24}" \
    --ga-population    37 \
    --ga-generations   50 \
    --ga-mutation-rate 0.15 \
    --ga-mutation-scale 0.2 \
    --ga-elite-frac    0.2 \
    --ga-mutation-cooling 0.985 \
    --baseline-start  "$SEED" \
    --datacenter      "$DATACENTER" \
    --dc-label        "$LABEL" \
    ${LOCKS:+--lock $LOCKS} \
    --max-land-pct     7 \
    $EXTRA

echo "Done: $REGION ($DC_CASE)   $(date)"

#!/bin/bash
# ============================================================================
# reoptimize_fromseed_slurm.sh   (env: REGION, CASE, SEEDFILE, OUTSUFFIX,
#                                 [MODE=eval|ga], [SEED])
#
# Run one data-center case (see scripts/dc_cases.sh) for one region from an
# arbitrary seed file, e.g. a hand-tuned solution built with
# build_seed_from_overrides.py:
#   MODE=eval  evaluate the seed once (one Fortran run, no GA);
#   MODE=ga    run the GA starting from the seed.
#
# Results go to <REGION>_<OUTSUFFIX>/ and xx_optimized_<OUTSUFFIX>/, so the
# campaign results are never touched.
#
# Example:
#   sbatch --export=ALL,REGION=SOUTHEAST-ASIA,CASE=case2h2,\
# SEEDFILE=data/seed_seasia_wsh.dat,OUTSUFFIX=seed_wsh,MODE=eval \
#       scripts/reoptimize_fromseed_slurm.sh
# ============================================================================
#SBATCH --job-name=lm-fromseed
#SBATCH --output=logs/fromseed_%j.out
#SBATCH --error=logs/fromseed_%j.err
#SBATCH --time=24:00:00
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=24
#SBATCH --mem=200G
#SBATCH --partition=serc

set -uo pipefail
cd "${SLURM_SUBMIT_DIR:-$(pwd)}"
mkdir -p logs
module load python/3.9.0
source .venv/bin/activate
source scripts/dc_cases.sh

: "${REGION:?set REGION}"; : "${CASE:?set CASE}"
: "${SEEDFILE:?set SEEDFILE}"; : "${OUTSUFFIX:?set OUTSUFFIX}"
MODE="${MODE:-eval}"
SEED="${SEED:-12345}"

[ -f "$SEEDFILE" ] || { echo "seed file not found: $SEEDFILE"; exit 1; }
dc_case "$CASE" || exit 1

echo "=== from-seed $MODE   $REGION $CASE   seed=$SEEDFILE   -> _$OUTSUFFIX   ($(date)) ==="

COMMON=(--region "$REGION" --baseline-start "$SEEDFILE"
        --datacenter "$DATACENTER" --dc-label "$LABEL"
        --max-land-pct 7 --out-suffix "$OUTSUFFIX")
if [ "$MODE" = "eval" ]; then
  python -m scripts.run_full_workflow "${COMMON[@]}" --parallel-evals 1 --evaluate-only
elif [ "$MODE" = "ga" ]; then
  # shellcheck disable=SC2086
  python -m scripts.run_full_workflow "${COMMON[@]}" \
      --ga-population 37 --ga-generations 50 --ga-mutation-rate 0.15 \
      --ga-mutation-scale 0.2 --ga-elite-frac 0.2 --ga-mutation-cooling 0.985 \
      --parallel-evals 24 --seed "$SEED" ${LOCKS:+--lock $LOCKS} $EXTRA
else
  echo "unknown MODE '$MODE' (want eval|ga)"; exit 1
fi

echo "=== done ($(date)) ==="
R=data/results_verification
echo "--- result ---"
grep -H "annual_cost_mn_bil_per_yr\|\"feasible\"" "$R/${REGION}_${OUTSUFFIX}/optimal_summary.json" 2>/dev/null || true

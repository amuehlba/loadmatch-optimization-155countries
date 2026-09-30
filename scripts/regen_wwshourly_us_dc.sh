#!/bin/bash
# ============================================================================
# regen_wwshourly_us_dc.sh
#
# Write the hourly output file wwshourly.UNITED-STATES for the United States
# Baseline optimum and all five data-center cases (see
# regen_wwshourly_slurm.sh for why this needs a separate evaluation).  Each
# case's saved optimum is evaluated once, with its data-center scenario; the
# EGS case (dc1) has no optimum of its own and is evaluated from the Baseline
# optimum, as in the campaign.
#
# All cases write the same data/raw/wwshourly.UNITED-STATES, so they run one
# after another and each file is copied to a case-tagged name in wwshourly_US/.
#
# Output: wwshourly_US.tgz  (base, dc1_EGS, dc2_WSBH, dc2bat_WSB, dc2h2_WSH,
#                            dc2rc_RBH)
#
# Usage (from the repo root):
#   sbatch scripts/regen_wwshourly_us_dc.sh
# ============================================================================
#SBATCH --job-name=uswwsh
#SBATCH --partition=serc
#SBATCH --output=logs/uswwsh_%j.out
#SBATCH --error=logs/uswwsh_%j.err
#SBATCH --time=3:00:00
#SBATCH --cpus-per-task=2
#SBATCH --mem=96G

set -uo pipefail
cd "${SLURM_SUBMIT_DIR:-.}"
[ -d data/results_verification ] || { echo "ERROR: run from the repo root (data/results_verification not found in $PWD)"; exit 1; }
if [ -f .venv/bin/activate ]; then source .venv/bin/activate; fi
source scripts/dc_cases.sh
mkdir -p logs wwshourly_US

R=UNITED-STATES

# run_case TAG CASE: CASE = base or a dc_cases.sh case.
run_case () {
  local TAG="$1" CASE="$2" SCEN="" SUFFIX=""
  if [ "$CASE" != "base" ]; then
    dc_case "$CASE" || return
    SCEN="--datacenter $DATACENTER"
    SUFFIX="_dc${DATACENTER}${LABEL}"
  fi
  [ "$CASE" = "case1" ] && SUFFIX=""        # dc1 is evaluated from the base optimum
  local SEED="data/results_verification/${R}${SUFFIX}/genetic_factors.dat"
  if [ ! -f "$SEED" ]; then echo "  [SKIP] $TAG : seed not found ($SEED)"; return; fi
  echo "=== $TAG   seed=${SEED}   ($(date)) ==="
  # shellcheck disable=SC2086
  if python -m scripts.run_full_workflow --region "$R" \
        --baseline-start "$SEED" --evaluate-only --parallel-evals 1 \
        $SCEN --max-land-pct 7 --out-suffix "uswwsh_${TAG}"; then
    cp -f "data/raw/wwshourly.${R}" "wwshourly_US/wwshourly.${R}.${TAG}"
    echo "  saved wwshourly_US/wwshourly.${R}.${TAG}"
  else
    echo "  [FAIL] $TAG"
  fi
}

run_case base        base
run_case dc1_EGS     case1
run_case dc2_WSBH    case2
run_case dc2bat_WSB  case2bat
run_case dc2h2_WSH   case2h2
run_case dc2rc_RBH   case3

echo "----------------------------------------------------------------"
ls -la wwshourly_US/
tar czf wwshourly_US.tgz -C wwshourly_US .
echo "Wrote wwshourly_US.tgz"

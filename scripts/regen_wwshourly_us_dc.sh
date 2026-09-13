#!/bin/bash
# ============================================================================
# regen_wwshourly_us_dc.sh
#
# Regenerate wwshourly for the UNITED-STATES base optimization case AND all five
# data-center cases, using the current powerworld binary (the 120-step hourly-
# output fix).  Evaluation only: each case's existing optimum
# (genetic_factors.dat) is evaluated once with the correct scenario + locks; no
# re-optimization, no cost/xx change.
#
# Because every case writes to the same data/raw/wwshourly.UNITED-STATES, the
# cases run sequentially and each file is copied out to a case-tagged name in
# wwshourly_US/ before the next case overwrites it.
#
# Output: wwshourly_US.tgz  (6 files: base + dc1/EGS, dc2/WSBH, dc2bat/WSB,
#                            dc2h2/WSH, dc2rc/RBH)
#
# Usage:
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
source .venv/bin/activate 2>/dev/null || true
mkdir -p logs wwshourly_US

R=UNITED-STATES
ALWAYS_LOCK="HCDDADD FMORTBAU"
LOCK_CASE2="FACRESPV FACCOMPV CSPTURBFAC FACSHT STORHCOLD STORHHWAT STORHPHS STORUGDYS CPERFORM"
LOCK_CASE3="FACONWIN FACOFFWIN FACUTILPV CSPTURBFAC FACSHT STORHCOLD STORHHWAT STORHPHS STORUGDYS CPERFORM"

# tag | results-dir suffix | scenario flags | hj-locks
run_case () {
  local TAG="$1" SUFFIX="$2" SCEN="$3" LOCKS="$4"
  local DIR="data/results_verification/${R}${SUFFIX}"
  local SEED="${DIR}/genetic_factors.dat"
  if [ ! -f "$SEED" ]; then echo "  [SKIP] $TAG : optimum not found ($SEED)"; return; fi
  echo "=== $TAG   dir=${DIR}   ($(date)) ==="
  # shellcheck disable=SC2086
  if python -m scripts.run_full_workflow --region "$R" --optimizer ga \
        --baseline-start "$SEED" --evaluate-only --parallel-evals 1 \
        $SCEN ${LOCKS:+--hj-lock $LOCKS} --max-land-pct 7 \
        --out-suffix "uswwsh_${TAG}" --no-plots; then
    cp -f "data/raw/wwshourly.${R}" "wwshourly_US/wwshourly.${R}.${TAG}"
    echo "  saved wwshourly_US/wwshourly.${R}.${TAG}"
  else
    echo "  [FAIL] $TAG"
  fi
}

run_case base        ""       ""                              ""
run_case dc1_EGS     _dc1     "--datacenter 1"                "$ALWAYS_LOCK"
run_case dc2_WSBH    _dc2     "--datacenter 2"                "$LOCK_CASE2 $ALWAYS_LOCK"
run_case dc2bat_WSB  _dc2bat  "--datacenter 2 --dc-label bat" "$LOCK_CASE2 FCDISCH FCCHARG DAYH2STOR $ALWAYS_LOCK"
run_case dc2h2_WSH   _dc2h2   "--datacenter 2 --dc-label h2"  "$LOCK_CASE2 BATDISCH STORHBAT $ALWAYS_LOCK"
run_case dc2rc_RBH   _dc2rc   "--datacenter 2 --dc-label rc"  "$LOCK_CASE3 $ALWAYS_LOCK"

echo "----------------------------------------------------------------"
ls -la wwshourly_US/
tar czf wwshourly_US.tgz -C wwshourly_US .
echo "Wrote wwshourly_US.tgz"

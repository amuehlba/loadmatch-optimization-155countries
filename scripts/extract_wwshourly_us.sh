#!/bin/bash
# ============================================================================
# extract_wwshourly_us.sh
#
# Regenerate the U.S. hourly time-series output (data/raw/wwshourly.UNITED-STATES)
# for the base case and the five data-center cases, WITHOUT re-optimizing.
#
# wwshourly.<REGION> is a Fortran OUTPUT (hourly time-series statistics for
# printing).  It is written to data/raw on every serial model run, overwritten
# each run, and never archived per case (parallel GA workers write it to
# ephemeral workspaces).  Here we re-EVALUATE each case's saved optimum once
# (a single Fortran run, no GA) and copy the resulting file out under a
# case-specific name before the next run overwrites it.
#
# Two flags matter:
#   --parallel-evals 1   forces the direct (global-lock) run in data/raw, so
#                        wwshourly lands there.  With >1 the eval runs in an
#                        ephemeral workspace and the file is lost.
#   --out-suffix wwsdump sends the throwaway results/xx to a scratch dir so the
#                        real per-case optimal_summary.json / xx are untouched.
#
# The saved optima it reads (must already exist):
#   base / dc1 : data/results_verification/UNITED-STATES/genetic_factors.dat
#   dc2        : .../UNITED-STATES_dc2/genetic_factors.dat
#   dc2rc      : .../UNITED-STATES_dc2rc/genetic_factors.dat
#   dc2bat     : .../UNITED-STATES_dc2bat/genetic_factors.dat
#   dc2h2      : .../UNITED-STATES_dc2h2/genetic_factors.dat
#
# Output: data/results_verification/wwshourly_US/wwshourly.UNITED-STATES.<case>
#
# Run from the repo root on Sherlock (ideally inside an salloc; six short runs):
#   bash scripts/extract_wwshourly_us.sh
# ============================================================================
set -uo pipefail
cd "${SLURM_SUBMIT_DIR:-$(pwd)}"
source .venv/bin/activate 2>/dev/null || true

R=data/results_verification
OUT=$R/wwshourly_US
WH=data/raw/wwshourly.UNITED-STATES
mkdir -p "$OUT"

run() {  # $1=case label   $2=seed dir (under $R)   $3..=extra scenario flags
  local label="$1" seeddir="$2"; shift 2
  local seed="$R/$seeddir/genetic_factors.dat"
  if [ ! -f "$seed" ]; then
    echo "SKIP $label: seed not found ($seed)"; return
  fi
  echo "=================================================================="
  echo "  $label   seed=$seeddir   scenario flags: $*"
  echo "=================================================================="
  python -m scripts.run_full_workflow --region UNITED-STATES \
      --baseline-start "$seed" --evaluate-only --parallel-evals 1 \
      --out-suffix wwsdump --no-plots "$@"
  if [ -f "$WH" ]; then
    cp -f "$WH" "$OUT/wwshourly.UNITED-STATES.$label"
    echo "  saved -> $OUT/wwshourly.UNITED-STATES.$label"
  else
    echo "  WARNING: $WH not found after the $label run"
  fi
}

run base   UNITED-STATES
run dc1     UNITED-STATES        --datacenter 1
run dc2     UNITED-STATES_dc2    --datacenter 2
run dc2rc   UNITED-STATES_dc2rc  --datacenter 2 --dc-label rc
run dc2bat  UNITED-STATES_dc2bat --datacenter 2 --dc-label bat
run dc2h2   UNITED-STATES_dc2h2  --datacenter 2 --dc-label h2

# Remove the throwaway results/xx produced by the isolated evaluate runs.
rm -rf "$R/UNITED-STATES_wwsdump" "$R/xx_optimized_wwsdump" 2>/dev/null || true

echo ""
echo "Done. U.S. wwshourly for the six cases:"
ls -la "$OUT/"

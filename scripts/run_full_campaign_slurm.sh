#!/bin/bash
# ============================================================================
# run_full_campaign_slurm.sh
#
# Supervisor for the full result set: baseline optimization, scratch
# optimization, and all five data-center cases, with at most two job arrays in
# the queue at any time:
#
#   - scratch runs in the background for the whole campaign (the longest
#     arrays; nothing depends on it);
#   - the baseline optimization runs alongside it;
#   - once the baseline succeeds, the five data-center cases run one at a time
#     (case1, case2, case3, case2bat, case2h2), each seeded from the fresh
#     baseline optima.
#
# If the baseline array reports a failure, the data-center cases are not
# started (they would seed from missing or stale optima).
#
# Usage (from the repo root):
#   sbatch scripts/run_full_campaign_slurm.sh
#   sbatch --export=ALL,SKIP_BASE=1 scripts/run_full_campaign_slurm.sh  # keep base
# ============================================================================

#SBATCH --job-name=lm-campaign
#SBATCH --output=logs/campaign_supervisor_%j.out
#SBATCH --error=logs/campaign_supervisor_%j.err
#SBATCH --time=4-00:00:00
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=2G
#SBATCH --partition=serc

set -uo pipefail

REPO_ROOT="${SLURM_SUBMIT_DIR:-$(pwd)}"
cd "$REPO_ROOT"
mkdir -p logs

# Core budget: each array task uses 24 cores.  Throttling every array to 20
# concurrent tasks caps one array at 480 cores, two overlapping arrays at 960.
# This --array option overrides the "#SBATCH --array=0-29" of the array
# scripts for the campaign only.
THROTTLE="--array=0-29%20"

echo "=== Launch: scratch (independent)   ($(date)) ==="
sbatch --wait $THROTTLE scripts/run_all_regions_scratch_slurm.sh & P_SCR=$!

if [ "${SKIP_BASE:-0}" = "1" ]; then
  echo "=== SKIP_BASE=1: keeping the existing baseline optima   ($(date)) ==="
else
  echo "=== Launch: baseline optimization   ($(date)) ==="
  sbatch --wait $THROTTLE scripts/run_all_regions_array_slurm.sh & P_BASE=$!
  wait "$P_BASE"; RC_BASE=$?
  echo "base exit=$RC_BASE   ($(date))"
  if [ "$RC_BASE" -ne 0 ]; then
    echo "ERROR: baseline array reported failures; NOT starting data-center cases."
    echo "Waiting for scratch to finish, then exiting; fix the baseline and rerun."
    wait "$P_SCR" || echo "WARNING: scratch array reported failures."
    exit 1
  fi
fi

for c in case1 case2 case3 case2bat case2h2; do
  echo "=== Launch: DC $c   ($(date)) ==="
  sbatch --wait $THROTTLE --export=ALL,DC_CASE="$c" scripts/run_all_regions_dc_slurm.sh \
    || echo "WARNING: DC_CASE=$c reported failures."
done

echo "=== data-center cases complete; waiting on scratch   ($(date)) ==="
wait "$P_SCR" || echo "WARNING: scratch array reported failures."

echo "=== Campaign complete ($(date)) ==="
echo "Next: bash scripts/export_all_results.sh"

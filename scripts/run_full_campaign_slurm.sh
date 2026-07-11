#!/bin/bash
# ============================================================================
# run_full_campaign_slurm.sh
#
# One-shot supervisor that reruns the ENTIRE result set — base optimization,
# scratch campaign, and all five data-center cases — while keeping AT MOST TWO
# job arrays in the queue (running or pending) at any time:
#
#   wave 1:  base  +  scratch            (independent of each other)
#   wave 2:  dc case1  +  dc case2       (start only after base SUCCEEDED,
#   wave 3:  dc case3  +  dc case2bat     since dc cases seed from the new
#   wave 4:  dc case2h2                   base optima)
#
# The supervisor itself is one lightweight plain job (not an array).  If the
# base array reports any failure, the data-center waves are NOT started (they
# would silently seed from stale optima); fix the failed region(s) and restart
# this supervisor — completed arrays simply rerun (results are overwritten
# deterministically-equivalent) or comment out finished waves below.
#
# Usage (from the repo root):
#   sbatch scripts/run_full_campaign_slurm.sh
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

# SKIP_BASE=1: keep the existing base results (e.g. when only the scratch and
# data-center campaigns changed) — wave 1 then runs scratch + dc case1, and the
# dc cases rely on the dc script's own per-region seed check.
#   sbatch --export=ALL,SKIP_BASE=1 scripts/run_full_campaign_slurm.sh
if [ "${SKIP_BASE:-0}" = "1" ]; then
  echo "=== Wave 1 (SKIP_BASE): scratch + dc case1   ($(date)) ==="
  sbatch --wait scripts/run_all_regions_scratch_slurm.sh & P_SCR=$!
  sbatch --wait --export=ALL,DC_CASE=case1 scripts/run_all_regions_dc_slurm.sh & P_DC1=$!
  wait "$P_DC1" || echo "WARNING: DC_CASE=case1 reported failures."
  wait "$P_SCR" || echo "WARNING: scratch array reported failures."
else
  echo "=== Wave 1: base + scratch   ($(date)) ==="
  sbatch --wait scripts/run_all_regions_array_slurm.sh   & P_BASE=$!
  sbatch --wait scripts/run_all_regions_scratch_slurm.sh & P_SCR=$!
  wait "$P_BASE"; RC_BASE=$?
  wait "$P_SCR";  RC_SCR=$?
  echo "base exit=$RC_BASE  scratch exit=$RC_SCR   ($(date))"

  if [ "$RC_BASE" -ne 0 ]; then
    echo "ERROR: base array reported failures — NOT starting data-center cases."
    echo "Fix the failed base region(s), then rerun this supervisor."
    exit 1
  fi
  if [ "$RC_SCR" -ne 0 ]; then
    echo "WARNING: scratch array reported failures (independent of dc cases; continuing)."
  fi
fi

run_wave() {
  echo "=== Wave: DC ${1}${2:+ + $2}   ($(date)) ==="
  sbatch --wait --export=ALL,DC_CASE="$1" scripts/run_all_regions_dc_slurm.sh & A=$!
  if [ -n "${2:-}" ]; then
    sbatch --wait --export=ALL,DC_CASE="$2" scripts/run_all_regions_dc_slurm.sh & B=$!
    wait "$B" || echo "WARNING: DC_CASE=$2 reported failures."
  fi
  wait "$A" || echo "WARNING: DC_CASE=$1 reported failures."
}

if [ "${SKIP_BASE:-0}" = "1" ]; then
  run_wave case2 case3            # case1 already ran in wave 1
  run_wave case2bat case2h2
else
  run_wave case1 case2
  run_wave case3 case2bat
  run_wave case2h2
fi

echo "=== Campaign complete ($(date)) ==="
echo "Next: run the export/plot scripts (export_comparison, plot_comparison,"
echo "      export_results, export_dc_comparison) and pull results to local."

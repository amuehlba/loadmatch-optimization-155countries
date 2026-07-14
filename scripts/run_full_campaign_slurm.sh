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

# ---------------------------------------------------------------------------
# Core budget: stay under ~1000 cores at any instant.  Each array task uses
# --cpus-per-task=24, and this supervisor keeps at most TWO arrays running at
# once (scratch in the background overlapping one other array: base, then each
# dc case in turn).
# Throttling each array to 20 concurrent tasks (%20) caps one array at
# 20*24 = 480 cores, so two overlapping arrays use 2*480 = 960 cores (+1 for
# this supervisor) < 1000.  All 30 regions still run, just <=20 at a time
# (a lone array simply finishes in ~2 passes).  This --array override on the
# sbatch command line supersedes the "#SBATCH --array=0-29" in each array
# script, for this campaign only; standalone runs of those scripts are
# unaffected (a single array alone is 30*24 = 720 cores, still < 1000).
# ---------------------------------------------------------------------------
THROTTLE="--array=0-29%20"

# The dc cases seed from the BASE optimum (each region's genetic_factors.dat),
# so they must wait for BASE to finish — but NOT for scratch, which feeds
# nothing and is the long pole.  Strategy: run scratch in the BACKGROUND for the
# whole campaign, and once base is done, run the five dc cases one at a time in
# the FOREGROUND.  That starts dc as soon as base finishes (overlapping the
# still-running scratch) instead of blocking dc behind scratch, while never
# exceeding TWO concurrent arrays: scratch (1) + the current dc case (1) = 960
# cores at %20.  case1 is evaluate-only (fast); the rest are short GA re-solves.
#
# SKIP_BASE=1: keep the existing base results and start dc immediately (the dc
# script still does its own per-region genetic_factors.dat seed check):
#   sbatch --export=ALL,SKIP_BASE=1 scripts/run_full_campaign_slurm.sh

# Start scratch in the background — it runs independently the whole time.
echo "=== Launch: scratch (independent, long pole)   ($(date)) ==="
sbatch --wait $THROTTLE scripts/run_all_regions_scratch_slurm.sh & P_SCR=$!

# Base must finish successfully before any dc case can seed from it.
if [ "${SKIP_BASE:-0}" = "1" ]; then
  echo "=== SKIP_BASE=1: base kept from a previous run; dc may start now   ($(date)) ==="
else
  echo "=== Launch: base optimization   ($(date)) ==="
  sbatch --wait $THROTTLE scripts/run_all_regions_array_slurm.sh & P_BASE=$!
  wait "$P_BASE"; RC_BASE=$?
  echo "base exit=$RC_BASE   ($(date))"
  if [ "$RC_BASE" -ne 0 ]; then
    echo "ERROR: base array reported failures — NOT starting data-center cases"
    echo "       (they would seed from stale/failed base optima)."
    echo "Waiting for scratch to finish, then exiting; fix base and rerun."
    wait "$P_SCR" || echo "WARNING: scratch array reported failures."
    exit 1
  fi
fi

# Base is done: stream the dc cases one at a time while scratch keeps running.
for c in case1 case2 case3 case2bat case2h2; do
  echo "=== Launch: DC $c   ($(date)) ==="
  sbatch --wait $THROTTLE --export=ALL,DC_CASE="$c" scripts/run_all_regions_dc_slurm.sh \
    || echo "WARNING: DC_CASE=$c reported failures."
done

# All dc cases done; wait on scratch (usually the last to finish).
echo "=== dc cases complete; waiting on scratch   ($(date)) ==="
wait "$P_SCR" || echo "WARNING: scratch array reported failures."

echo "=== Campaign complete ($(date)) ==="
echo "Next: run the export/plot scripts (export_comparison, plot_comparison,"
echo "      export_results, export_dc_comparison) and pull results to local."

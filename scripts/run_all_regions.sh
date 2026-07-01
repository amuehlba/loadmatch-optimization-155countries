#!/bin/bash
# ============================================================================
# run_all_regions.sh
#
# Run the full 30-region base-case LoadMatch GA workflow via Snakemake + SLURM.
# Snakemake submits each rule as an individual SLURM job and monitors them.
# Produces, per region: optimal_summary.json (with solve-time timing),
# baseline_summary.json, the GA-optimal xx file, and figures; then the
# cross-region comparison (comparison_summary.csv, figA4/figA5) and XLSX export.
#
# This runs STEP 1 only (base WWS, IFDATCEN=0).  The data-center scenarios
# (steps 2/3) are launched separately with --datacenter 1/2 once step 1 is done.
#
# Run from the repo root on a Sherlock login node (inside screen or tmux):
#   screen -S loadmatch
#   bash scripts/run_all_regions.sh
#
# Force-rerun specific rules (e.g. after a code change):
#   bash scripts/run_all_regions.sh --forcerun run_ga
#
# Monitor progress:
#   squeue -u $USER
#   tail -f logs/run_ga_<REGION>.log
#   tail -f data/results_verification/<REGION>/factor_history.log
# ============================================================================

set -euo pipefail

# Repo root = parent of this script's directory, resolved absolutely.  Works
# wherever the repo is cloned (no hardcoded path).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

module load python/3.12.1
source "${REPO_ROOT}/.venv/bin/activate"
cd "${REPO_ROOT}"

mkdir -p logs/snakemake

# Unlock any stale lock left by a previously killed Snakemake process (no-op if none).
.venv/bin/snakemake --unlock --profile profiles/slurm 2>/dev/null || true

.venv/bin/snakemake \
    --profile          profiles/slurm \
    --jobs             30 \
    --rerun-incomplete \
    "$@" \
    all

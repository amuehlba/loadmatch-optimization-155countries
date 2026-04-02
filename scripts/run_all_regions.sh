#!/bin/bash
# ============================================================================
# run_all_regions.sh
#
# Run the full 29-region LoadMatch workflow via Snakemake + SLURM.
# Snakemake submits each rule as an individual SLURM job and monitors them.
#
# Run from the repo root on a Sherlock login node (inside screen or tmux):
#   screen -S loadmatch
#   bash scripts/run_all_regions.sh
#
# Force-rerun specific rules (e.g. after a code change):
#   bash scripts/run_all_regions.sh --forcerun plot_region plot_overview
#
# Monitor progress:
#   squeue -u $USER
#   tail -f logs/run_ga_<REGION>.log
#   tail -f data/results_verification/<REGION>/factor_history.log
# ============================================================================

set -euo pipefail

REPO_ROOT="${GROUP_HOME:-$(pwd)}/loadmatch-python"

module load python/3.12.1
source "${REPO_ROOT}/.venv/bin/activate"
cd "$REPO_ROOT"

mkdir -p logs/snakemake

.venv/bin/snakemake \
    --profile          profiles/slurm \
    --jobs             29 \
    --rerun-incomplete \
    "$@" \
    all

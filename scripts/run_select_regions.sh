#!/bin/bash
# ============================================================================
# run_select_regions.sh
#
# Run a curated 10-region subset via Snakemake + SLURM.
#
# Run from the repo root on a Sherlock login node (inside screen or tmux):
#   screen -S loadmatch
#   bash scripts/run_select_regions.sh
#
# Force-rerun specific rules:
#   bash scripts/run_select_regions.sh --forcerun plot_region plot_overview
#
# To run all 29 regions:
#   bash scripts/run_all_regions.sh
# ============================================================================

set -euo pipefail

REPO_ROOT="${GROUP_HOME:-$(pwd)}/loadmatch-python"
SELECT_CONFIG="config/select_regions.yaml"

module load python/3.12.1
source "${REPO_ROOT}/.venv/bin/activate"
cd "$REPO_ROOT"

mkdir -p logs/snakemake config

cat > "$SELECT_CONFIG" <<'EOF'
regions:
  - UNITED-STATES
  - CANADA
  - EUROPE
  - CHINA
  - INDIA
  - JAPAN
  - AUSTRALIA
  - AFRICA-EAST
  - SOUTHEAST-ASIA
  - RUSSIA
EOF

.venv/bin/snakemake \
    --profile          profiles/slurm \
    --configfile       "$SELECT_CONFIG" \
    --jobs             10 \
    --rerun-incomplete \
    "$@" \
    all

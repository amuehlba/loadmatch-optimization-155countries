#!/bin/bash
# ============================================================================
# run_europe.sh
#
# Run the EUROPE region only — useful for testing the full pipeline quickly.
#
# Run from the repo root on a Sherlock login node:
#   bash scripts/run_europe.sh
#
# Force-rerun specific rules:
#   bash scripts/run_europe.sh --forcerun plot_region
# ============================================================================

set -euo pipefail

REPO_ROOT="${GROUP_HOME:-$(pwd)}/loadmatch-python"
EUROPE_CONFIG="config/europe_only.yaml"

module load python/3.12.1
source "${REPO_ROOT}/.venv/bin/activate"
cd "$REPO_ROOT"

mkdir -p logs/snakemake config

cat > "$EUROPE_CONFIG" <<'EOF'
regions:
  - EUROPE
EOF

.venv/bin/snakemake \
    --profile          profiles/slurm \
    --configfile       "$EUROPE_CONFIG" \
    --jobs             1 \
    --rerun-incomplete \
    "$@" \
    all

#!/bin/bash
# ============================================================================
# regen_wwshourly_slurm.sh
#
# Regenerate wwshourly.<REGION> for the base (no-data-center) Baseline optimum
# of all 30 regions using the current powerworld binary.
#
# Use after a powerworld.f change that affects ONLY the hourly output format
# (e.g. the 120-time-steps-per-hour fix) and therefore does NOT change costs,
# factors, or xx deliverables.  This is an EVALUATION pass, not a
# re-optimization: each region's existing Baseline optimum (genetic_factors.dat)
# is evaluated once.
#
# Regions run SEQUENTIALLY with --parallel-evals 1 (the direct Fortran path), so
# each writes its own wwshourly.<REGION> to data/raw/ without racing on the
# shared factor file.  Evaluations go to throwaway <REGION>_wwsh/ dirs, so the
# live base results and xx deliverables are NOT overwritten.
#
# Output: a tarball wwshourly_regen.tgz of all data/raw/wwshourly.* for the PI.
#
# Usage:
#   sbatch scripts/regen_wwshourly_slurm.sh
# ============================================================================
#SBATCH --job-name=wwsh_regen
#SBATCH --partition=serc
#SBATCH --output=logs/wwsh_regen_%j.out
#SBATCH --error=logs/wwsh_regen_%j.err
#SBATCH --time=6:00:00
#SBATCH --cpus-per-task=2
#SBATCH --mem=96G

set -uo pipefail
cd "$(dirname "$0")/.."
source .venv/bin/activate 2>/dev/null || true
mkdir -p logs

# Region list from config/workflow.yaml (same extraction as the base campaign).
REGIONS=()
while IFS= read -r R; do
  REGIONS+=("$R")
done < <(sed -n 's/^[[:space:]]*-[[:space:]]*//p' config/workflow.yaml | grep -E '^[A-Z]')
echo "Regenerating wwshourly for ${#REGIONS[@]} regions"
echo "binary: $(readlink -f fortran/bin/powerworld)"

ok=0; skip=0; fail=0
for R in "${REGIONS[@]}"; do
  SEED="data/results_verification/${R}/genetic_factors.dat"
  if [ ! -f "$SEED" ]; then
    echo "  [SKIP] $R : no base optimum ($SEED)"; skip=$((skip+1)); continue
  fi
  echo "=== $R  ($(date)) ==="
  if python -m scripts.run_full_workflow --region "$R" --optimizer ga \
        --baseline-start "$SEED" --evaluate-only --parallel-evals 1 \
        --out-suffix wwsh --no-plots; then
    ok=$((ok+1))
  else
    echo "  [FAIL] $R"; fail=$((fail+1))
  fi
done

echo "----------------------------------------------------------------"
echo "evaluated ok=$ok  skipped=$skip  failed=$fail"

# Collect the regenerated hourly files for the PI.
( cd data/raw && ls wwshourly.* >/dev/null 2>&1 \
  && tar czf ../../wwshourly_regen.tgz wwshourly.* \
  && echo "Wrote wwshourly_regen.tgz with $(ls wwshourly.* | wc -l) files" ) \
  || echo "WARNING: no wwshourly.* files found in data/raw/"

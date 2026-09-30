#!/bin/bash
# ============================================================================
# regen_wwshourly_slurm.sh
#
# Write the hourly output file wwshourly.<REGION> for the Baseline optimum of
# every region.  The GA's final evaluation runs in an isolated workspace, so
# the optimization itself does not keep this file; here each saved optimum
# (genetic_factors.dat) is evaluated once more, with no re-optimization.
#
# Regions run one after another with --parallel-evals 1 (direct Fortran run),
# so each writes data/raw/wwshourly.<REGION>.  Evaluations go to throwaway
# <REGION>_wwsh/ dirs; the campaign results are not touched.
#
# Output: wwshourly_regen.tgz with all data/raw/wwshourly.* files.
#
# Usage (from the repo root):
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
cd "${SLURM_SUBMIT_DIR:-.}"
[ -f config/workflow.yaml ] || { echo "ERROR: run from the repo root (config/workflow.yaml not found in $PWD)"; exit 1; }
if [ -f .venv/bin/activate ]; then source .venv/bin/activate; fi
mkdir -p logs

REGIONS=()
while IFS= read -r R; do
  REGIONS+=("$R")
done < <(sed -n 's/^[[:space:]]*-[[:space:]]*//p' config/workflow.yaml | grep -E '^[A-Z]')
[ "${#REGIONS[@]}" -gt 0 ] || { echo "ERROR: no regions parsed from config/workflow.yaml"; exit 1; }
echo "Regenerating wwshourly for ${#REGIONS[@]} regions"
echo "binary: $(readlink -f fortran/bin/powerworld)"

ok=0; skip=0; fail=0
for R in "${REGIONS[@]}"; do
  SEED="data/results_verification/${R}/genetic_factors.dat"
  if [ ! -f "$SEED" ]; then
    echo "  [SKIP] $R : no base optimum ($SEED)"; skip=$((skip+1)); continue
  fi
  echo "=== $R  ($(date)) ==="
  if python -m scripts.run_full_workflow --region "$R" \
        --baseline-start "$SEED" --evaluate-only --parallel-evals 1 \
        --out-suffix wwsh; then
    ok=$((ok+1))
  else
    echo "  [FAIL] $R"; fail=$((fail+1))
  fi
done

echo "----------------------------------------------------------------"
echo "evaluated ok=$ok  skipped=$skip  failed=$fail"

( cd data/raw && ls wwshourly.* >/dev/null 2>&1 \
  && tar czf ../../wwshourly_regen.tgz wwshourly.* \
  && echo "Wrote wwshourly_regen.tgz with $(ls wwshourly.* | wc -l) files" ) \
  || echo "WARNING: no wwshourly.* files found in data/raw/"

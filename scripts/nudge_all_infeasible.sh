#!/bin/bash
# ============================================================================
# nudge_all_infeasible.sh
#
# Batch-nudge every (region, case) that reeval_all_slurm.sh flagged INFEASIBLE
# under the strict EXCESIN threshold, to feasibility, with one conservative
# variable bump each (calls nudge_case_feasible.sh).  Reports which became
# feasible and which still fail (those need a different variable or a real
# re-optimization).
#
# It reads the INFEASIBLE list from the reeval logs.  Point REEVAL_LOGS at the
# CURRENT reeval job's logs so stale logs don't pollute the list:
#   sbatch --export=ALL,REEVAL_LOGS="logs/reeval_35779603_*.out" \
#          scripts/nudge_all_infeasible.sh
# ============================================================================
#SBATCH --job-name=lm-nudge
#SBATCH --output=logs/nudge_%j.out
#SBATCH --error=logs/nudge_%j.err
#SBATCH --time=1-00:00:00
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=2
#SBATCH --mem=64G
#SBATCH --partition=serc

set -uo pipefail
cd "${SLURM_SUBMIT_DIR:-$(pwd)}"
mkdir -p logs
REEVAL_LOGS="${REEVAL_LOGS:-logs/reeval_*.out}"

mapfile -t PAIRS < <(grep -hoE "INFEASIBLE: [A-Z0-9-]+ \([a-z0-9]+\)" $REEVAL_LOGS \
  | sed -E 's/INFEASIBLE: ([A-Z0-9-]+) \(([a-z0-9]+)\)/\1 \2/' | sort -u)

echo "Nudging ${#PAIRS[@]} infeasible (region, case) pairs from: $REEVAL_LOGS"
ok=""; fail=""
for pc in "${PAIRS[@]}"; do
  read -r REG CS <<< "$pc"
  echo "-------- $REG ($CS)   $(date) --------"
  if bash scripts/nudge_case_feasible.sh "$REG" "$CS"; then
    ok="$ok $REG/$CS"
  else
    fail="$fail $REG/$CS"
  fi
done

echo ""
echo "=== NUDGE SUMMARY   ($(date)) ==="
echo "feasible now:${ok:- none}"
echo "still failing (try another VAR or re-optimize):${fail:- none}"

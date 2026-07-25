#!/bin/bash
# ============================================================================
# nudge_dc1_feasible.sh  [REGION] [VAR]
#
# The EGS (dc1) case evaluates a region's no-data-center optimum with the data
# centers added.  For a region whose optimum sits on the discharge/feasibility
# edge (e.g. JAMAICA under the 2023-cost code), that pure evaluation can be
# infeasible.  Per the PI: restore feasibility with the SMALLEST increase of ONE
# design variable, then report that single adjustment in the paper.
#
# This tries escalating multipliers of VAR applied to the base optimum,
# evaluates the EGS case each time, and stops at the first feasible one; it then
# writes the real dc1 deliverable (<REGION>_dc1/, xx_optimized_dc1/) with that
# nudged seed and saves the seed for the record.
#
# Variable choice (JAMAICA diagnosis: battery empty + discharge-rate-limited):
#   FACONWIN / FACOFFWIN  more wind -> battery drains less        (PI's pick)
#   STORHBAT              more battery energy -> not empty at step (most direct)
#   BATDISCH             will NOT help here (battery is empty, rate is moot)
#
# Usage (in an salloc, from the repo root):
#   bash scripts/nudge_dc1_feasible.sh JAMAICA FACONWIN
#   bash scripts/nudge_dc1_feasible.sh JAMAICA STORHBAT     # if wind needs a big bump
# ============================================================================
set -uo pipefail
cd "${SLURM_SUBMIT_DIR:-$(pwd)}"
module load python/3.9.0
source .venv/bin/activate

REGION="${1:-JAMAICA}"
VAR="${2:-FACONWIN}"
R=data/results_verification
BASE="$R/$REGION/genetic_factors.dat"
[ -f "$BASE" ] || { echo "no base optimum at $BASE"; exit 1; }
WORK="$(mktemp -d)"

_bump() {  # $1=mult  $2=outfile ; multiply VAR by mult in the base seed
  python - "$BASE" "$VAR" "$1" "$2" <<'PY'
import sys
base, var, mult, out = sys.argv[1], sys.argv[2].upper(), float(sys.argv[3]), sys.argv[4]
found = False; lines = []
for ln in open(base):
    if "=" in ln:
        k, v = ln.split("=", 1)
        if k.strip().upper() == var:
            lines.append(f"{k.strip()} = {float(v) * mult}\n"); found = True; continue
    lines.append(ln)
open(out, "w").writelines(lines)
sys.exit(0 if found else 3)
PY
}

winner=""
for MULT in 1.03 1.05 1.10 1.15 1.20 1.30 1.50; do
  SEED="$WORK/seed_$MULT.dat"
  _bump "$MULT" "$SEED" || { echo "ERROR: $VAR not found in $BASE"; rm -rf "$WORK"; exit 1; }
  echo "--- $REGION dc1: $VAR x$MULT ---"
  LOG="$WORK/log_$MULT.txt"
  python -m scripts.run_full_workflow --region "$REGION" \
    --baseline-start "$SEED" --datacenter 1 --evaluate-only --parallel-evals 1 \
    --out-suffix dc1search --no-plots >"$LOG" 2>&1 || true
  if grep -q "\[evaluate\] feasible=True" "$LOG"; then
    cost=$(grep -oE "Evaluate-only: cost [0-9.]+" "$LOG" | grep -oE "[0-9.]+" | head -1)
    echo "  FEASIBLE at $VAR x$MULT  (cost ~\$${cost} B/yr)"
    winner="$MULT"; break
  fi
  echo "  still infeasible"
done
rm -rf "$R/${REGION}_dc1search" "$R/xx_optimized_dc1search" 2>/dev/null || true

if [ -z "$winner" ]; then
  echo ""
  echo "No feasible point up to $VAR x1.5.  Try a different variable, e.g.:"
  echo "  bash scripts/nudge_dc1_feasible.sh $REGION STORHBAT"
  echo "  bash scripts/nudge_dc1_feasible.sh $REGION FACOFFWIN"
  rm -rf "$WORK"; exit 1
fi

echo ""
echo "== writing the real dc1 deliverable with $VAR x$winner =="
SEEDKEEP="$R/$REGION/genetic_factors.dc1nudge.dat"
_bump "$winner" "$SEEDKEEP"
python -m scripts.run_full_workflow --region "$REGION" \
  --baseline-start "$SEEDKEEP" --datacenter 1 --evaluate-only --parallel-evals 1 \
  --no-plots
rm -rf "$WORK"
echo ""
echo "Done: $REGION dc1 feasible with $VAR increased ${winner}x from the base optimum."
echo "Nudged seed saved to $SEEDKEEP.  Report this single-variable adjustment in the"
echo "dc methods, then re-export (export_dc_comparison) to refresh the table."

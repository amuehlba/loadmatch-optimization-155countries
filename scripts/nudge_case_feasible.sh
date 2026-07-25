#!/bin/bash
# ============================================================================
# nudge_case_feasible.sh  REGION CASE [VAR]
#
# Make ONE (region, case) feasible under the strict EXCESIN threshold by the
# smallest conservative increase of ONE design variable (the PI's trial-and-
# error philosophy).  Handles every case: base, scratch2, dc1, dc2, dc2rc,
# dc2bat, dc2h2.  It escalates VAR x{1.03..2.0}, evaluates that case (no
# re-optimization) each time, stops at the first feasible bump, and overwrites
# that case's real result with the feasible nudged one.
#
# Default VAR per case (a FREE generation lever for that case):
#   base/scratch2/dc1/dc2/dc2bat/dc2h2 -> FACONWIN   (onshore wind)
#   dc2rc                              -> FACRESPV   (rooftop; wind is locked)
# Override by passing VAR (e.g. STORHBAT for a discharge-rate-limited step).
#
# Usage (in an salloc, from the repo root):
#   bash scripts/nudge_case_feasible.sh JAMAICA dc2
#   bash scripts/nudge_case_feasible.sh NEW-ZEALAND dc2bat STORHBAT
# Exit: 0 feasible, 1 not feasible up to x2.0, 2 bad args / missing seed.
# ============================================================================
set -uo pipefail
cd "${SLURM_SUBMIT_DIR:-$(pwd)}"
module load python/3.9.0 2>/dev/null || true
source .venv/bin/activate 2>/dev/null || true

REGION="${1:?usage: REGION CASE [VAR]}"
CASE="${2:?usage: REGION CASE [VAR]}"
VARARG="${3:-}"
R=data/results_verification

case "$CASE" in
  base)     SEEDDIR="$R/$REGION";            OUTDIR="$R/$REGION";            SCEN="";                              OUT="";                      DEFVAR=FACONWIN ;;
  scratch2) SEEDDIR="$R/${REGION}_scratch2"; OUTDIR="$R/${REGION}_scratch2"; SCEN="";                              OUT="--out-suffix scratch2"; DEFVAR=FACONWIN ;;
  dc1)      SEEDDIR="$R/$REGION";            OUTDIR="$R/${REGION}_dc1";      SCEN="--datacenter 1";                OUT="";                      DEFVAR=FACONWIN ;;
  dc2)      SEEDDIR="$R/${REGION}_dc2";      OUTDIR="$R/${REGION}_dc2";      SCEN="--datacenter 2";                OUT="";                      DEFVAR=FACONWIN ;;
  dc2rc)    SEEDDIR="$R/${REGION}_dc2rc";    OUTDIR="$R/${REGION}_dc2rc";    SCEN="--datacenter 2 --dc-label rc";  OUT="";                      DEFVAR=FACRESPV ;;
  dc2bat)   SEEDDIR="$R/${REGION}_dc2bat";   OUTDIR="$R/${REGION}_dc2bat";   SCEN="--datacenter 2 --dc-label bat"; OUT="";                      DEFVAR=FACONWIN ;;
  dc2h2)    SEEDDIR="$R/${REGION}_dc2h2";    OUTDIR="$R/${REGION}_dc2h2";    SCEN="--datacenter 2 --dc-label h2";  OUT="";                      DEFVAR=FACONWIN ;;
  *) echo "unknown CASE '$CASE'"; exit 2 ;;
esac
VAR="${VARARG:-$DEFVAR}"
SEED="$SEEDDIR/genetic_factors.dat"
[ -f "$SEED" ] || { echo "SKIP $REGION ($CASE): no seed $SEED"; exit 2; }

WORK="$(mktemp -d)"
_bump() {  # $1=mult $2=outfile ; multiply VAR by mult in the seed
  python - "$SEED" "$VAR" "$1" "$2" <<'PY'
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
for M in 1.03 1.05 1.10 1.15 1.20 1.30 1.50 2.00; do
  _bump "$M" "$WORK/s.dat" || { echo "FAIL $REGION ($CASE): '$VAR' not in seed"; rm -rf "$WORK"; exit 2; }
  python -m scripts.run_full_workflow --region "$REGION" --baseline-start "$WORK/s.dat" \
     --evaluate-only --parallel-evals 1 $SCEN --out-suffix nudgesearch --no-plots \
     >"$WORK/l.txt" 2>&1 || true
  grep -q "\[evaluate\] feasible=True" "$WORK/l.txt" && { winner="$M"; break; }
done
rm -rf "$R/${REGION}_nudgesearch" "$R/xx_optimized_nudgesearch" 2>/dev/null || true

if [ -z "$winner" ]; then
  echo "FAIL $REGION ($CASE): not feasible with $VAR up to x2.0 (try another VAR or re-optimize)"
  rm -rf "$WORK"; exit 1
fi

# Write the real deliverable for this case with the winning bump.
_bump "$winner" "$WORK/final.dat"
python -m scripts.run_full_workflow --region "$REGION" --baseline-start "$WORK/final.dat" \
   --evaluate-only --parallel-evals 1 $SCEN $OUT --no-plots >/dev/null 2>&1
mkdir -p "$OUTDIR"; cp "$WORK/final.dat" "$OUTDIR/genetic_factors.nudge.dat"
cost=$(grep -oE "Evaluate-only: cost [0-9.]+" "$WORK/l.txt" | grep -oE "[0-9.]+" | head -1)
echo "OK   $REGION ($CASE): feasible with $VAR x$winner  (cost ~\$${cost}B)"
rm -rf "$WORK"

#!/bin/bash
# ============================================================================
# reoptimize_leanseed_slurm.sh   (env: REGION, CASE, REALSUFFIX, TRANSFORM,
#                                 OUTSUFFIX, [SEED])
#
# Re-optimize a data-center case starting from a HAND-SHAPED seed, to escape the
# expensive basin the feasibility bootstrap lands in for restricted cases (e.g.
# SOUTHAM-SE rooftop, SOUTHEAST-ASIA hydrogen).  The seed is the CURRENT case
# optimum with TRANSFORM applied (e.g. cut the over-built generation, raise the
# free storage), so the GA starts in the cheaper "moderate-generation + more-
# storage" basin instead of the overbuilt one.  Writes to a throwaway OUTSUFFIX.
#
# TRANSFORM is space-separated VAR:MULT, e.g. "FACCOMPV:0.5 STORHBAT:2.0".
#
# Example:
#   sbatch --export=ALL,REGION=SOUTHAM-SE,CASE=case3,REALSUFFIX=dc2rc,\
# TRANSFORM="FACRESPV:0.5 FACCOMPV:0.5 STORHBAT:2.0 DAYH2STOR:1.5",\
# OUTSUFFIX=lean_serc scripts/reoptimize_leanseed_slurm.sh
# ============================================================================
#SBATCH --job-name=lm-lean
#SBATCH --output=logs/lean_%j.out
#SBATCH --error=logs/lean_%j.err
#SBATCH --time=24:00:00
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=24
#SBATCH --mem=200G
#SBATCH --partition=serc

set -uo pipefail
cd "${SLURM_SUBMIT_DIR:-$(pwd)}"
mkdir -p logs
module load python/3.9.0
source .venv/bin/activate

: "${REGION:?}"; : "${CASE:?}"; : "${REALSUFFIX:?}"; : "${TRANSFORM:?}"; : "${OUTSUFFIX:?}"
SEED="${SEED:-12345}"

ALWAYS_LOCK="HCDDADD FMORTBAU"
LOCK_CASE2="FACRESPV FACCOMPV CSPTURBFAC FACSHT STORHCOLD STORHHWAT STORHPHS STORUGDYS CPERFORM"
LOCK_CASE3="FACONWIN FACOFFWIN FACUTILPV CSPTURBFAC FACSHT STORHCOLD STORHHWAT STORHPHS STORUGDYS CPERFORM"
case "$CASE" in
  case2)    SCEN="--datacenter 2";                LOCKS="$LOCK_CASE2 $ALWAYS_LOCK" ;;
  case3)    SCEN="--datacenter 2 --dc-label rc";  LOCKS="$LOCK_CASE3 $ALWAYS_LOCK" ;;
  case2bat) SCEN="--datacenter 2 --dc-label bat"; LOCKS="$LOCK_CASE2 FCDISCH FCCHARG DAYH2STOR $ALWAYS_LOCK" ;;
  case2h2)  SCEN="--datacenter 2 --dc-label h2";  LOCKS="$LOCK_CASE2 BATDISCH STORHBAT $ALWAYS_LOCK" ;;
  *) echo "unknown CASE '$CASE'"; exit 1 ;;
esac

R=data/results_verification
CUR="$R/${REGION}_${REALSUFFIX}/genetic_factors.dat"
[ -f "$CUR" ] || { echo "no current optimum $CUR"; exit 1; }

LEAN="$(mktemp)"
python - "$CUR" "$LEAN" "$TRANSFORM" <<'PY'
import sys
cur, out, transform = sys.argv[1], sys.argv[2], sys.argv[3]
scales = {}
for tok in transform.split():
    k, v = tok.split(":"); scales[k.strip().upper()] = float(v)
lines = []
for ln in open(cur):
    if "=" in ln:
        k, v = ln.split("=", 1)
        if k.strip().upper() in scales:
            lines.append(f"{k.strip()} = {float(v) * scales[k.strip().upper()]}\n"); continue
    lines.append(ln)
open(out, "w").writelines(lines)
print("lean seed:", ", ".join(f"{k} x{m}" for k, m in scales.items()))
PY

echo "=== lean-seed reoptimize $REGION $CASE  seed=$SEED  -> _$OUTSUFFIX   ($(date)) ==="
# shellcheck disable=SC2086
python -m scripts.run_full_workflow --region "$REGION" --optimizer ga \
    --ga-population 37 --ga-generations 50 --ga-mutation-rate 0.15 \
    --ga-mutation-scale 0.2 --ga-elite-frac 0.2 --ga-mutation-cooling 0.985 \
    --parallel-evals 24 --baseline-start "$LEAN" $SCEN --hj-lock $LOCKS \
    --max-land-pct 7 --seed "$SEED" --out-suffix "$OUTSUFFIX" --no-plots
rm -f "$LEAN"
echo "=== done   ($(date)) ==="

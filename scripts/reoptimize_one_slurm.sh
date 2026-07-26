#!/bin/bash
# ============================================================================
# reoptimize_one_slurm.sh   (env: REGION, CASE, SEED, OUTSUFFIX)
#
# Re-optimize ONE data-center case for ONE region with a specific GA random
# seed, writing to a throwaway output suffix (so the current result is not
# touched).  Used to search for a lower-cost basin for a poor-quality optimum
# (e.g. SOUTHAM-SE rooftop, SOUTHEAST-ASIA hydrogen).  Full 200G / 24 cores like
# the dc campaign, so a large region cannot OOM (the SouthAm-SE crash earlier
# was an out-of-memory kill in a small salloc).
#
# Submitted by reoptimize_lowcost.sh, or directly:
#   sbatch --export=ALL,REGION=SOUTHAM-SE,CASE=case3,SEED=101,OUTSUFFIX=lccase3s101 \
#          scripts/reoptimize_one_slurm.sh
# ============================================================================
#SBATCH --job-name=lm-reopt
#SBATCH --output=logs/reopt_%j.out
#SBATCH --error=logs/reopt_%j.err
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

: "${REGION:?set REGION}"; : "${CASE:?set CASE}"; : "${SEED:?set SEED}"; : "${OUTSUFFIX:?set OUTSUFFIX}"

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

SEED_FILE="data/results_verification/$REGION/genetic_factors.dat"
[ -f "$SEED_FILE" ] || { echo "no base seed $SEED_FILE"; exit 1; }

echo "=== reoptimize $REGION $CASE  seed=$SEED  -> _$OUTSUFFIX   ($(date)) ==="
# shellcheck disable=SC2086
python -m scripts.run_full_workflow --region "$REGION" --optimizer ga \
    --ga-population 37 --ga-generations 50 --ga-mutation-rate 0.15 \
    --ga-mutation-scale 0.2 --ga-elite-frac 0.2 --ga-mutation-cooling 0.985 \
    --parallel-evals 24 --baseline-start "$SEED_FILE" $SCEN --hj-lock $LOCKS \
    --max-land-pct 7 --seed "$SEED" --out-suffix "$OUTSUFFIX" --no-plots
echo "=== done $REGION $CASE seed=$SEED   ($(date)) ==="

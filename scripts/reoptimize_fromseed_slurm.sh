#!/bin/bash
# ============================================================================
# reoptimize_fromseed_slurm.sh   (env: REGION, CASE, SEEDFILE, OUTSUFFIX,
#                                 [MODE=eval|ga], [SEED])
#
# Seed a data-center case from an ARBITRARY solution file (e.g. the previous,
# old-cost optimum in data/results_verification.oldcosts/) and either:
#   MODE=eval  -> evaluate that build ONCE under the current (new-cost) binary
#                 and stop.  This is the PI's hypothesis test: "the last
#                 version's solution, re-costed with the new T&D costs, should
#                 be cheaper than the current run."  One Fortran run, no GA.
#   MODE=ga    -> re-optimize with the GA starting FROM that seed (polish it in
#                 its own basin instead of the overbuilt basin the feasibility
#                 bootstrap lands in).
#
# Writes to a throwaway OUTSUFFIX (own <REGION>_<OUTSUFFIX>/ + xx_optimized_
# <OUTSUFFIX>/), so the live dc result is never touched.  Full 200G/24 like the
# dc campaign so a large region cannot OOM (the earlier SOUTHAM-SE crash was an
# out-of-memory kill in a small salloc).
#
# Examples:
#   # 1) test the PI's idea (cheap):
#   sbatch --export=ALL,REGION=SOUTHAM-SE,CASE=case3,\
# SEEDFILE=data/results_verification.oldcosts/SOUTHAM-SE_dc2rc/genetic_factors.dat,\
# OUTSUFFIX=oldseed_serc,MODE=eval scripts/reoptimize_fromseed_slurm.sh
#
#   # 2) if the eval is cheaper+feasible, polish it:
#   sbatch --export=ALL,REGION=SOUTHAM-SE,CASE=case3,\
# SEEDFILE=data/results_verification.oldcosts/SOUTHAM-SE_dc2rc/genetic_factors.dat,\
# OUTSUFFIX=oldseed_serc_ga,MODE=ga scripts/reoptimize_fromseed_slurm.sh
# ============================================================================
#SBATCH --job-name=lm-fromseed
#SBATCH --output=logs/fromseed_%j.out
#SBATCH --error=logs/fromseed_%j.err
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

: "${REGION:?set REGION}"; : "${CASE:?set CASE}"
: "${SEEDFILE:?set SEEDFILE}"; : "${OUTSUFFIX:?set OUTSUFFIX}"
MODE="${MODE:-eval}"
SEED="${SEED:-12345}"

[ -f "$SEEDFILE" ] || { echo "seed file not found: $SEEDFILE"; exit 1; }

# Same lock convention as the live dc campaign, so locked vars use the Fortran
# default (identical to how the deliverable is produced) and only the free
# variables come from the seed.
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

echo "=== from-seed $MODE   $REGION $CASE   seed=$SEEDFILE   -> _$OUTSUFFIX   ($(date)) ==="

if [ "$MODE" = "eval" ]; then
  # shellcheck disable=SC2086
  python -m scripts.run_full_workflow --region "$REGION" --optimizer ga \
      --parallel-evals 1 --baseline-start "$SEEDFILE" $SCEN --hj-lock $LOCKS \
      --max-land-pct 7 --evaluate-only --out-suffix "$OUTSUFFIX" --no-plots
elif [ "$MODE" = "ga" ]; then
  # shellcheck disable=SC2086
  python -m scripts.run_full_workflow --region "$REGION" --optimizer ga \
      --ga-population 37 --ga-generations 50 --ga-mutation-rate 0.15 \
      --ga-mutation-scale 0.2 --ga-elite-frac 0.2 --ga-mutation-cooling 0.985 \
      --parallel-evals 24 --baseline-start "$SEEDFILE" $SCEN --hj-lock $LOCKS \
      --max-land-pct 7 --seed "$SEED" --out-suffix "$OUTSUFFIX" --no-plots
else
  echo "unknown MODE '$MODE' (want eval|ga)"; exit 1
fi

echo "=== done ($(date)) ==="
R=data/results_verification
echo "--- result ---"
grep -H "annual_cost_mn_bil_per_yr\|\"feasible\"" "$R/${REGION}_${OUTSUFFIX}/optimal_summary.json" 2>/dev/null || true

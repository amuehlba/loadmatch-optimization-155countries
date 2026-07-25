#!/bin/bash
# ============================================================================
# reeval_all_slurm.sh
#
# Re-EVALUATE (not re-optimize) every saved optimum through the CURRENT binary,
# OVERWRITING the existing results in place.  Use this to regenerate the xx
# deliverables after a cost-neutral Fortran tweak (e.g. the FACSHT .GT.SMAL30
# fix) without re-running the GA campaign.
#
# ARRANGEMENT: the array dimension is the CASE (7 tasks), and each task loops
# over ALL regions internally, one evaluate-only Fortran run at a time.  So the
# whole re-eval is a SINGLE submission using just 7 node allocations, which is
# gentle on the cluster.  (Each task is sequential over regions, ~a few hours.)
#
# The 7 cases, their saved-optimum seed, and where each lands:
#   base     <REGION>/genetic_factors.dat          IFDATCEN=0     -> <REGION>/,          xx_optimized/
#   scratch2 <REGION>_scratch2/genetic_factors.dat IFDATCEN=0     -> <REGION>_scratch2/, xx_optimized_scratch2/  (--out-suffix)
#   dc1      <REGION>/genetic_factors.dat          IFDATCEN=1     -> <REGION>_dc1/,       xx_optimized_dc1/
#   dc2      <REGION>_dc2/genetic_factors.dat      IFDATCEN=2     -> <REGION>_dc2/,       xx_optimized_dc2/
#   dc2rc    <REGION>_dc2rc/genetic_factors.dat    IFDATCEN=2 rc  -> <REGION>_dc2rc/,     xx_optimized_dc2rc/
#   dc2bat   <REGION>_dc2bat/genetic_factors.dat   IFDATCEN=2 bat -> <REGION>_dc2bat/,    xx_optimized_dc2bat/
#   dc2h2    <REGION>_dc2h2/genetic_factors.dat    IFDATCEN=2 h2  -> <REGION>_dc2h2/,     xx_optimized_dc2h2/
#
# A region/case with no saved optimum (e.g. dc2rc for GREENLAND/ICELAND) is
# skipped.  Because the FACSHT tweak is cost-neutral, ONLY the xx files change;
# do NOT regenerate comparison_summary.csv / dc_comparison_summary.csv /
# results_export.xlsx afterward (the evaluate-only re-run re-stamps each
# optimal_summary.json timing to evaluate-mode; the CSVs already hold the real
# GA solve times).  genetic_factors.dat is never overwritten (idempotent).
#
# Usage (from the repo root) -- ONE submission does everything:
#   sbatch scripts/reeval_all_slurm.sh
# ============================================================================

#SBATCH --job-name=lm-reeval
#SBATCH --output=logs/reeval_%A_%a.out
#SBATCH --error=logs/reeval_%A_%a.err
#SBATCH --array=0-6
#SBATCH --time=12:00:00
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=2
#SBATCH --mem=64G
#SBATCH --partition=serc

set -uo pipefail
REPO_ROOT="${SLURM_SUBMIT_DIR:-$(pwd)}"
cd "$REPO_ROOT"
mkdir -p logs
module load python/3.9.0
source .venv/bin/activate

CASES=(base scratch2 dc1 dc2 dc2rc dc2bat dc2h2)
CASE="${CASES[$SLURM_ARRAY_TASK_ID]}"

mapfile -t REGIONS < <(sed -n 's/^[[:space:]]*-[[:space:]]*//p' config/workflow.yaml \
                       | grep -E '^[A-Z]')
R=data/results_verification

# Set SEED and EXTRA (extra CLI flags) for the current CASE and a given region.
seed_and_flags() {
  local reg="$1"
  case "$CASE" in
    base)     SEED="$R/$reg/genetic_factors.dat";            EXTRA="" ;;
    scratch2) SEED="$R/${reg}_scratch2/genetic_factors.dat"; EXTRA="--out-suffix scratch2" ;;
    dc1)      SEED="$R/$reg/genetic_factors.dat";            EXTRA="--datacenter 1" ;;
    dc2)      SEED="$R/${reg}_dc2/genetic_factors.dat";      EXTRA="--datacenter 2" ;;
    dc2rc)    SEED="$R/${reg}_dc2rc/genetic_factors.dat";    EXTRA="--datacenter 2 --dc-label rc" ;;
    dc2bat)   SEED="$R/${reg}_dc2bat/genetic_factors.dat";   EXTRA="--datacenter 2 --dc-label bat" ;;
    dc2h2)    SEED="$R/${reg}_dc2h2/genetic_factors.dat";    EXTRA="--datacenter 2 --dc-label h2" ;;
    *) echo "Unknown CASE '$CASE' (base|scratch2|dc1|dc2|dc2rc|dc2bat|dc2h2)"; exit 1 ;;
  esac
}

echo "=================================================================="
echo "Re-eval CASE=$CASE  over ${#REGIONS[@]} regions   ($(date))  Host: $(hostname)"
echo "=================================================================="

# Regions to skip (space-separated), e.g. run GREENLAND separately via its
# re-optimization: sbatch --export=ALL,SKIP_REGIONS=GREENLAND scripts/reeval_all_slurm.sh
SKIP_REGIONS="${SKIP_REGIONS:-}"

nok=0; nskip=0; nfail=0; ninfeas=0; flagged=""
for REGION in "${REGIONS[@]}"; do
  case " $SKIP_REGIONS " in
    *" $REGION "*) echo "SKIP $REGION ($CASE): in SKIP_REGIONS"; nskip=$((nskip+1)); continue ;;
  esac
  seed_and_flags "$REGION"
  if [ ! -f "$SEED" ]; then
    echo "SKIP $REGION ($CASE): no saved optimum at $SEED"
    nskip=$((nskip + 1)); continue
  fi
  echo "--- $REGION ($CASE)   $(date) ---"
  LOG=$(mktemp)
  python -m scripts.run_full_workflow --region "$REGION" \
       --baseline-start "$SEED" --evaluate-only --parallel-evals 2 \
       --max-land-pct 7 --no-plots $EXTRA >"$LOG" 2>&1
  rc=$?
  cat "$LOG"
  if grep -q "\[evaluate\] feasible=True" "$LOG"; then
    nok=$((nok + 1))
  elif grep -qE "REMAINING INFLEX LOAD|EXCESIN\)>0|feasible=False|UNMET|UNSERVED" "$LOG"; then
    echo "*** INFEASIBLE: $REGION ($CASE) ***"
    ninfeas=$((ninfeas + 1)); flagged="$flagged $REGION"
  else
    echo "WARNING: $REGION ($CASE) re-eval failed (rc=$rc, no feasibility verdict)"
    nfail=$((nfail + 1)); flagged="$flagged ${REGION}(fail)"
  fi
  rm -f "$LOG"
done

echo "=== CASE=$CASE complete: ok=$nok infeasible=$ninfeas skipped=$nskip failed=$nfail   ($(date)) ==="
[ -n "$flagged" ] && echo "=== CASE=$CASE INFEASIBLE/FAILED:$flagged ==="

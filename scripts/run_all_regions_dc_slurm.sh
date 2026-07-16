#!/bin/bash
# ============================================================================
# run_all_regions_dc_slurm.sh
#
# SLURM job array: data-center cases for all 30 regions, per the PI's plan.
# Select the case with DC_CASE (default case1):
#
#   sbatch --export=ALL,DC_CASE=case1    scripts/run_all_regions_dc_slurm.sh
#   sbatch --export=ALL,DC_CASE=case2    scripts/run_all_regions_dc_slurm.sh
#   sbatch --export=ALL,DC_CASE=case3    scripts/run_all_regions_dc_slurm.sh
#   sbatch --export=ALL,DC_CASE=case2bat scripts/run_all_regions_dc_slurm.sh
#   sbatch --export=ALL,DC_CASE=case2h2  scripts/run_all_regions_dc_slurm.sh
#
# Cases (all seeded from the region's no-data-center GA optimum,
# data/results_verification/<REGION>/genetic_factors.dat — the base array must
# have finished first):
#   case1    IFDATCEN=1 (EGS powers the data centers automatically).  One
#            evaluation only, no re-optimization needed.     -> <REGION>_dc1/
#   case2    IFDATCEN=2, re-optimize allowing ONLY utility PV, on/offshore
#            wind, batteries, and hydrogen (FC + electrolyser + storage days)
#            to change; everything else locked at the no-dc optimum.
#                                                            -> <REGION>_dc2/
#   case3    IFDATCEN=2, re-optimize allowing ONLY residential + commercial
#            PV, batteries, and hydrogen to change.          -> <REGION>_dc2rc/
#   case2bat case2 but storage flexibility restricted to batteries (hydrogen
#            locked at the no-dc optimum).                   -> <REGION>_dc2bat/
#   case2h2  case2 but storage flexibility restricted to hydrogen (batteries
#            locked at the no-dc optimum).                   -> <REGION>_dc2h2/
#
# All results and xx deliverables are isolated per case (suffix folders), so
# nothing overwrites the base, scratch, or other dc results.
# ============================================================================

#SBATCH --job-name=lm-ga-dc
#SBATCH --output=logs/slurm_dc_%A_%a.out
#SBATCH --error=logs/slurm_dc_%A_%a.err
#SBATCH --array=0-29
#SBATCH --time=48:00:00
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=24
#SBATCH --mem=200G
#SBATCH --partition=serc

set -euo pipefail

REPO_ROOT="${SLURM_SUBMIT_DIR:-$(pwd)}"
cd "$REPO_ROOT"

module load python/3.9.0
source .venv/bin/activate

mapfile -t REGIONS < <(sed -n 's/^[[:space:]]*-[[:space:]]*//p' config/workflow.yaml \
                       | grep -E '^[A-Z]' )
REGION="${REGIONS[$SLURM_ARRAY_TASK_ID]}"

DC_CASE="${DC_CASE:-case1}"

# Rooftop PV (case3) is not meaningful at high latitude (rooftop nameplate
# bases of 0.08-0.5 MW, polar-night winters) — skip per PI decision.
# Override with FORCE=1 to run it anyway (e.g. the PI needs the xx for his
# postprocessing):  sbatch --export=ALL,DC_CASE=case3,FORCE=1 ...
if [ "$DC_CASE" = "case3" ] && { [ "$REGION" = "GREENLAND" ] || [ "$REGION" = "ICELAND" ]; } && [ "${FORCE:-0}" != "1" ]; then
  echo "Skipping case3 for $REGION (rooftop strategy not applicable; PI decision; set FORCE=1 to run)."
  exit 0
fi

# Always-locked registry defaults + per-case lock sets.  Free variables:
#   case2:    FACONWIN FACOFFWIN FACUTILPV BATDISCH STORHBAT FCDISCH FCCHARG DAYH2STOR
#   case3:    FACRESPV FACCOMPV BATDISCH STORHBAT FCDISCH FCCHARG DAYH2STOR
#   case2bat: case2 minus hydrogen;  case2h2: case2 minus batteries.
ALWAYS_LOCK="HCDDADD FMORTBAU"
LOCK_CASE2="FACRESPV FACCOMPV CSPTURBFAC FACSHT STORHCOLD STORHHWAT STORHPHS STORUGDYS CPERFORM"
LOCK_CASE3="FACONWIN FACOFFWIN FACUTILPV CSPTURBFAC FACSHT STORHCOLD STORHHWAT STORHPHS STORUGDYS CPERFORM"

case "$DC_CASE" in
  case1)    DATACENTER=1; LABEL="";    EXTRA="--evaluate-only"; LOCKS="$ALWAYS_LOCK" ;;
  case2)    DATACENTER=2; LABEL="";    EXTRA="";  LOCKS="$LOCK_CASE2 $ALWAYS_LOCK" ;;
  case3)    DATACENTER=2; LABEL="rc";  EXTRA="";  LOCKS="$LOCK_CASE3 $ALWAYS_LOCK" ;;
  case2bat) DATACENTER=2; LABEL="bat"; EXTRA="";  LOCKS="$LOCK_CASE2 FCDISCH FCCHARG DAYH2STOR $ALWAYS_LOCK" ;;
  case2h2)  DATACENTER=2; LABEL="h2";  EXTRA="";  LOCKS="$LOCK_CASE2 BATDISCH STORHBAT $ALWAYS_LOCK" ;;
  *) echo "Unknown DC_CASE '$DC_CASE'"; exit 1 ;;
esac

SEED="data/results_verification/${REGION}/genetic_factors.dat"
if [ ! -f "$SEED" ]; then
  echo "ERROR: no-data-center optimum not found: $SEED"
  echo "Run the base array (run_all_regions_array_slurm.sh) to completion first."
  exit 1
fi

echo "=================================================================="
echo "Array task : $SLURM_ARRAY_TASK_ID   Region: $REGION"
echo "DC case    : $DC_CASE  (IFDATCEN=$DATACENTER, label='$LABEL')"
echo "Locked     : $LOCKS"
echo "Start      : $(date)   Host: $(hostname)"
echo "=================================================================="

# shellcheck disable=SC2086
python -m scripts.run_full_workflow \
    --region          "$REGION" \
    --optimizer       ga \
    --parallel-evals  "${SLURM_CPUS_PER_TASK:-24}" \
    --ga-population    37 \
    --ga-generations   50 \
    --ga-mutation-rate 0.15 \
    --ga-mutation-scale 0.2 \
    --ga-elite-frac    0.2 \
    --ga-mutation-cooling 0.985 \
    --baseline-start  "$SEED" \
    --datacenter      "$DATACENTER" \
    --dc-label        "$LABEL" \
    --hj-lock         $LOCKS \
    --max-land-pct     7 \
    $EXTRA \
    --no-plots

echo "Done: $REGION ($DC_CASE)   $(date)"

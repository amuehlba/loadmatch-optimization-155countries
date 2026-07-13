#!/bin/bash
# ============================================================================
# confirm_base_reeval_slurm.sh
#
# CONFIRM that the updated powerworld.f (260711 mechanics incl. the FACSHT=0
# fix) reproduces the SAME base-case "optimized" results that were already sent
# to the PI, for all 30 regions and NO other cases.
#
# This does NOT re-run the genetic algorithm.  The GA is stochastic (no fixed
# random seed), so a fresh GA run would differ run-to-run regardless of the
# code change and could never be "exactly the same".  Instead each task
# DETERMINISTICALLY RE-EVALUATES the already-found optimum: it feeds the region's
# saved optimal factors (data/results_verification/<REGION>/genetic_factors.dat)
# through the new binary in --evaluate-only mode (a single model run, no search).
# If the FACSHT fix is inert for a region, its output is byte-identical to what
# was sent; if the fix mattered there, the diff shows exactly what changed.
#
# One array task per region.  IFDATCEN=0 (base WWS only).
#
# Usage (from the repo root on a Sherlock login node), AFTER building the binary
# and backing up the sent results (see STEPS below):
#   sbatch scripts/confirm_base_reeval_slurm.sh
# ============================================================================

#SBATCH --job-name=lm-confirm
#SBATCH --output=logs/confirm_%A_%a.out
#SBATCH --error=logs/confirm_%A_%a.err
#SBATCH --array=0-29
#SBATCH --time=04:00:00
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=2
#SBATCH --mem=64G
#SBATCH --partition=serc

set -euo pipefail

REPO_ROOT="${SLURM_SUBMIT_DIR:-$(pwd)}"
cd "$REPO_ROOT"
mkdir -p logs

module load python/3.9.0
source .venv/bin/activate

# Region list from config/workflow.yaml (same source the base run used).
mapfile -t REGIONS < <(sed -n 's/^[[:space:]]*-[[:space:]]*//p' config/workflow.yaml \
                       | grep -E '^[A-Z]' )
REGION="${REGIONS[$SLURM_ARRAY_TASK_ID]}"

SEED="data/results_verification/${REGION}/genetic_factors.dat"
if [ ! -f "$SEED" ]; then
  echo "ERROR: $SEED not found. Cannot re-evaluate ${REGION}'s optimum."
  echo "       (This file is written by the base GA run; it must be present"
  echo "        from the run whose results were sent to the PI.)"
  exit 1
fi

echo "=================================================================="
echo "Confirm re-eval  task=$SLURM_ARRAY_TASK_ID  region=$REGION"
echo "Seed factors     : $SEED"
echo "Start            : $(date)   Host: $(hostname)"
echo "=================================================================="

# --evaluate-only: single deterministic model run of the seeded optimum, no GA.
# --parallel-evals 2 selects the isolated-workspace path so concurrent array
# tasks don't serialize on the global factor-file lock.
python -m scripts.run_full_workflow \
    --region         "$REGION" \
    --baseline-start "$SEED" \
    --evaluate-only \
    --parallel-evals 2 \
    --max-land-pct   7 \
    --no-plots

echo "Done: $REGION   $(date)"

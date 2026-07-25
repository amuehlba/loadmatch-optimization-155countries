#!/bin/bash
# ============================================================================
# rerun_greenland_slurm.sh
#
# Re-OPTIMIZE every Greenland case after the EXCESIN strict-threshold fix
# (2026-07-25).  The loose 1.0E-6 TWh threshold was ~equal to Greenland's
# ~1E-6 TWh per-step demand, so the GA could zero out generation and leave a
# full step's load unmet under the threshold -> energy did not conserve and all
# Greenland optima are invalid.  With the strict 1.0E-12 threshold the GA is
# forced to a real, energy-conserving solution.
#
# Greenland is ONE region (array index 11), so its cases run in parallel:
#   wave 1: base + scratch          (parallel; independent)
#   wave 2: the five dc cases        (parallel; each seeds from the NEW base, so
#           case1 case2 case3 case2bat case2h2   they wait for base).  case3
#           (dc2rc) uses FORCE=1 to bypass the high-latitude rooftop skip.
#
# Feasibility of every Greenland case is reported at the end.
#
# Usage (from the repo root; the strict-threshold binary must be built first):
#   sbatch scripts/rerun_greenland_slurm.sh
# ============================================================================
#SBATCH --job-name=lm-greenland
#SBATCH --output=logs/greenland_rerun_%j.out
#SBATCH --error=logs/greenland_rerun_%j.err
#SBATCH --time=1-00:00:00
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=2G
#SBATCH --partition=serc

set -uo pipefail
REPO_ROOT="${SLURM_SUBMIT_DIR:-$(pwd)}"
cd "$REPO_ROOT"
mkdir -p logs
GL=11   # GREENLAND array index (config/workflow.yaml region order)

echo "=== GREENLAND re-optimize: base + scratch (parallel)   ($(date)) ==="
sbatch --wait --array=$GL scripts/run_all_regions_array_slurm.sh   & PB=$!
sbatch --wait --array=$GL scripts/run_all_regions_scratch_slurm.sh & PS=$!
wait "$PB"; RCB=$?
echo "GREENLAND base exit=$RCB   ($(date))"
if [ "$RCB" -ne 0 ]; then
  echo "ERROR: GREENLAND base failed; dc cases seed from it -> NOT starting them."
  wait "$PS" || true
  exit 1
fi

echo "=== GREENLAND re-optimize: 5 dc cases (parallel; seed from new base)   ($(date)) ==="
PIDS=()
for c in case1 case2 case3 case2bat case2h2; do
  sbatch --wait --array=$GL --export=ALL,DC_CASE="$c",FORCE=1 \
      scripts/run_all_regions_dc_slurm.sh & PIDS+=($!)
done
for p in "${PIDS[@]}"; do wait "$p" || true; done
wait "$PS" || true   # scratch, if still running

echo ""
echo "=== GREENLAND feasibility of every case   ($(date)) ==="
R=data/results_verification
for d in GREENLAND GREENLAND_scratch2 GREENLAND_dc1 GREENLAND_dc2 \
         GREENLAND_dc2rc GREENLAND_dc2bat GREENLAND_dc2h2; do
  s="$R/$d/optimal_summary.json"
  if [ -f "$s" ]; then
    python3 - "$s" "$d" <<'PY'
import json, sys
s, d = sys.argv[1], sys.argv[2]
try:
    j = json.load(open(s))
    f = j.get("feasible"); c = j.get("annual_cost_mn_bil_per_yr")
    tag = "OK " if f is True else "*** INFEASIBLE ***"
    print(f"  {tag}  {d:22} feasible={f}  cost={c}")
except Exception as e:
    print(f"  ??? {d:22} ERROR reading summary ({e})")
PY
  else
    echo "  ??? $d  (no summary written)"
  fi
done
echo ""
echo "Any '*** INFEASIBLE ***' case needs a one-variable nudge to real feasibility"
echo "(scripts/nudge_dc1_feasible.sh GREENLAND <VAR> for dc1; same idea for others)."
echo "=== GREENLAND re-optimization complete   ($(date)) ==="

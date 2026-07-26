#!/bin/bash
# ============================================================================
# reoptimize_lowcost.sh
#
# Try to find a lower-cost solution for the two poor-quality optima flagged in
# the results (feasible but the GA parked in a bad basin):
#   SOUTHAM-SE   case3  (dc2rc)   rooftop over-built, +200% vs base
#   SOUTHEAST-ASIA case2h2 (dc2h2) hydrogen ~35x its battery counterpart
#
# For each, it re-optimizes with a few alternative random seeds (into throwaway
# suffixes, via reoptimize_one_slurm.sh at full 200G), then compares each seed's
# cost against the CURRENT result and prints a ready promote command for any
# seed that is meaningfully cheaper.  It does NOT overwrite the current results
# automatically -- you review the costs and run the promote command yourself.
#
# Usage (from the repo root):
#   sbatch scripts/reoptimize_lowcost.sh
# ============================================================================
#SBATCH --job-name=lm-lowcost
#SBATCH --output=logs/lowcost_%j.out
#SBATCH --error=logs/lowcost_%j.err
#SBATCH --time=1-12:00:00
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=2G
#SBATCH --partition=serc

set -uo pipefail
cd "${SLURM_SUBMIT_DIR:-$(pwd)}"
mkdir -p logs
R=data/results_verification

# "REGION CASE REALSUFFIX"  (REALSUFFIX = the live dc dir/xx suffix for that case)
PAIRS=("SOUTHAM-SE case3 dc2rc" "SOUTHEAST-ASIA case2h2 dc2h2")
SEEDS=(101 202)   # alternatives to the current run's default seed (12345)

echo "=== low-cost search: launching $(( ${#PAIRS[@]} * ${#SEEDS[@]} )) GA runs   ($(date)) ==="
PIDS=()
for entry in "${PAIRS[@]}"; do
  read -r REG CS _RS <<< "$entry"
  for s in "${SEEDS[@]}"; do
    sbatch --wait --export=ALL,REGION="$REG",CASE="$CS",SEED="$s",OUTSUFFIX="lc${CS}s${s}" \
        scripts/reoptimize_one_slurm.sh & PIDS+=($!)
  done
done
for p in "${PIDS[@]}"; do wait "$p" || true; done

echo ""
echo "=== RESULTS ($(date)) ==="
for entry in "${PAIRS[@]}"; do
  read -r REG CS RS <<< "$entry"
  SEEDS_CSV="$(IFS=,; echo "${SEEDS[*]}")"
  python3 - "$REG" "$CS" "$RS" "$SEEDS_CSV" "$R" <<'PY'
import json, os, sys
reg, case, realsfx, seeds_csv, R = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]
seeds = [s for s in seeds_csv.split(",") if s]
def load(d):
    p = os.path.join(R, d, "optimal_summary.json")
    if not os.path.exists(p): return (None, None)
    j = json.load(open(p))
    return (j.get("annual_cost_mn_bil_per_yr"), j.get("feasible"))
cur_cost, cur_feas = load(f"{reg}_{realsfx}")
print(f"\n{reg} {case}:  current (_{realsfx}) cost={cur_cost}  feasible={cur_feas}")
best = (cur_cost if (cur_cost is not None and cur_feas is True) else None, None)  # (cost, seed)
for s in seeds:
    sfx = f"lc{case}s{s}"
    c, f = load(f"{reg}_{sfx}")
    print(f"   seed {s} (_{sfx}): cost={c}  feasible={f}")
    if c is not None and f is True and (best[0] is None or c < best[0]):
        best = (c, s)
if best[1] is not None and cur_cost is not None and best[0] < cur_cost * 0.99:
    s = best[1]; sfx = f"lc{case}s{s}"
    print(f"   -> seed {s} is cheaper ({best[0]:.4g} < {cur_cost:.4g}).  PROMOTE with:")
    print(f"      rm -rf {R}/{reg}_{realsfx} && mv {R}/{reg}_{sfx} {R}/{reg}_{realsfx} && "
          f"cp {R}/xx_optimized_{sfx}/xx.* {R}/xx_optimized_{realsfx}/ && "
          f"rm -rf {R}/xx_optimized_{sfx}")
else:
    print(f"   -> no seed beats the current result by >1%; keep the current one.")
PY
done
echo ""
echo "Throwaway dirs (_lc*) are left in place until you promote or delete them:"
echo "  cleanup: rm -rf $R/*_lccase* $R/xx_optimized_lccase*"
echo "=== low-cost search complete ($(date)) ==="

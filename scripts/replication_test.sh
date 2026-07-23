#!/bin/bash
# ============================================================================
# replication_test.sh
#
# Confirm the refactored cost code reproduces the PREVIOUS results when the
# three changed cost parameters are set back to their old values.  If it does,
# the structural change (new DISTCOSL/DISTCOSH parameters + per-generator
# DISTRIB calc, SDTRCOSM removal, etc.) is behaviour-preserving, so the NEW cost
# VALUES are the only thing that will move the results.
#
# fortran/src/powerworld_replication.f is the reconciled source with exactly
# those parameters reverted:
#   SDTRCOSL/H rooftop entries -> the other-generator values (1.0 / 1.1)
#   DISTCOSL/H rooftop entries -> the other-generator values (2.3 / 2.45),
#     so the new weighted DISTRIB collapses to the old constant
#   COSTLDTLO/MN/HI            -> 0.42 / 0.89 / 1.00
#
# This builds a SEPARATE binary (fortran/bin/powerworld_repl), re-EVALUATES a
# few already-saved base optima through it (no re-optimization; via the
# LOADMATCH_FORTRAN_EXE override and --out-suffix so nothing is overwritten),
# and compares the mean annual cost to the currently-saved value.  A match to
# ~5 significant figures means the refactor is sound.
#
# Run from the repo root on Sherlock (inside an salloc):
#   bash scripts/replication_test.sh                       # default: US, EUROPE, CHINA
#   bash scripts/replication_test.sh UNITED-STATES MAURITIUS
# ============================================================================
set -uo pipefail
cd "${SLURM_SUBMIT_DIR:-$(pwd)}"
module load python/3.9.0
source .venv/bin/activate

REGIONS=("$@"); [ ${#REGIONS[@]} -eq 0 ] && REGIONS=(UNITED-STATES EUROPE CHINA)
R=data/results_verification

echo "== building replication binary (new code, OLD cost values) =="
mkdir -p fortran/build fortran/bin
gfortran -O2 -mcmodel=medium -fdefault-real-8 -fdefault-double-8 -fno-automatic \
    -J fortran/build fortran/src/powerworld_replication.f \
    -o fortran/bin/powerworld_repl || { echo "BUILD FAILED"; exit 1; }
echo "built fortran/bin/powerworld_repl"
echo ""

for REGION in "${REGIONS[@]}"; do
  seed="$R/$REGION/genetic_factors.dat"
  saved="$R/$REGION/optimal_summary.json"
  if [ ! -f "$seed" ] || [ ! -f "$saved" ]; then
    echo "SKIP $REGION (missing $seed or $saved)"; continue
  fi
  echo "--- $REGION: re-evaluating saved base optimum through the replication binary ---"
  LOADMATCH_FORTRAN_EXE=fortran/bin/powerworld_repl \
    python -m scripts.run_full_workflow --region "$REGION" \
      --baseline-start "$seed" --evaluate-only --parallel-evals 1 \
      --out-suffix repltest --no-plots >/dev/null 2>&1
  python - "$saved" "$R/${REGION}_repltest/optimal_summary.json" "$REGION" <<'PY'
import json, sys
saved, new, region = sys.argv[1], sys.argv[2], sys.argv[3]
K = "annual_cost_mn_bil_per_yr"
try:
    cs = json.load(open(saved)).get(K)
    cn = json.load(open(new)).get(K)
except Exception as e:
    print(f"  [ERROR] {region}: {e}"); sys.exit(0)
if cs is None or cn is None:
    print(f"  [ERROR] {region}: cost missing (saved={cs}, replication={cn})"); sys.exit(0)
rel = abs(cn - cs) / max(abs(cs), 1e-9)
tag = "PASS" if rel < 1e-4 else "FAIL"
print(f"  [{tag}] {region}: saved={cs:.4f}  replication={cn:.4f}  reldiff={rel:.2e}")
PY
done

rm -rf "$R"/*_repltest "$R"/xx_optimized_repltest 2>/dev/null || true
echo ""
echo "If every region PASSes, the refactor is behaviour-preserving.  Then rebuild"
echo "the production binary from the new source and start the full re-optimization:"
echo "  gfortran -O2 -mcmodel=medium -fdefault-real-8 -fdefault-double-8 -fno-automatic \\"
echo "      -J fortran/build fortran/src/powerworld.f -o fortran/bin/powerworld"

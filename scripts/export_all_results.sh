#!/bin/bash
# ============================================================================
# export_all_results.sh
#
# Run AFTER a full campaign (scripts/run_full_campaign_slurm.sh) has finished,
# from the repo root on a Sherlock login node (or anywhere the results tree +
# CSVs are present). Regenerates every comparison table, the XLSX, the PI xx
# deliverables, and all publication figures, then runs a data-integrity check
# that would have caught the baseline-clobber bug (trial-and-error must NOT be
# identical to GA-from-trial-and-error).
#
# Usage:
#   bash scripts/export_all_results.sh
# ============================================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"
source .venv/bin/activate 2>/dev/null || true

echo "=== 1/6  Cross-region comparison table (base vs GA vs scratch) ==="
python -m scripts.export_comparison

echo "=== 2/6  Data-center comparison table ==="
python -m scripts.export_dc_comparison

echo "=== 3/6  Full results workbook (XLSX) ==="
python -m scripts.export_results

echo "=== 4/6  PI xx deliverables (strip override echo; no-op-safe) ==="
python -m scripts.rebuild_xx_deliverables

echo "=== 5/6  Publication figures ==="
python -m scripts.plot_comparison
python -m scripts.plot_dc_comparison
python -m scripts.plot_structure                    # what changed vs trial-and-error
python -m scripts.plot_results --all-regions || echo "  (overview figures skipped)"

echo "=== 6/6  Data-integrity check ==="
python - <<'PY'
import sys, pandas as pd
csv = "data/results_verification/comparison_summary.csv"
df = pd.read_csv(csv)
df = df[~df["region"].astype(str).str.startswith("TOTAL")].copy()
for c in ["baseline_cost_bil_per_yr","ga_cost_bil_per_yr","pct_savings",
          "bl_newland_pct_regland","ga_newland_pct_regland",
          "ga_scratch2_newland_pct_regland"]:
    if c in df.columns:
        df[c] = pd.to_numeric(df[c], errors="coerce")
n = len(df)
cost_equal = ((df["baseline_cost_bil_per_yr"] - df["ga_cost_bil_per_yr"]).abs() < 1e-6).sum()
print(f"  regions: {n}")
print(f"  baseline_cost == ga_cost exactly: {cost_equal}/{n}")
print(f"  pct_savings min/max: {df['pct_savings'].min():.2f}% / {df['pct_savings'].max():.2f}%")
bad = False
if cost_equal == n:
    print("  ERROR: every baseline == GA -> baseline was CLOBBERED (see paper2-baseline-clobber). "
          "Do NOT run confirm_base_reeval into the main dirs; re-run the base campaign.")
    bad = True
# Taiwan land-cap check (scratch)
if "ga_scratch2_newland_pct_regland" in df.columns:
    t = df[df["region"] == "TAIWAN"]
    if not t.empty and pd.notna(t.iloc[0]["ga_scratch2_newland_pct_regland"]):
        v = float(t.iloc[0]["ga_scratch2_newland_pct_regland"])
        print(f"  TAIWAN from-scratch new-land: {v:.2f}% (cap 7%) -> {'OK' if v <= 7.0 else 'STILL OVER CAP'}")
# any scratch region over the 7% cap
if "ga_scratch2_newland_pct_regland" in df.columns:
    over = df[df["ga_scratch2_newland_pct_regland"] > 7.0]["region"].tolist()
    print(f"  from-scratch regions over 7% cap: {over if over else 'none'}")
sys.exit(1 if bad else 0)
PY

echo "=== Export complete. Pull data/results_verification/ to local for the paper. ==="

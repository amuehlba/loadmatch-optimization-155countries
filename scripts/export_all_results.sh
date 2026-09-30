#!/bin/bash
# ============================================================================
# export_all_results.sh
#
# Regenerate every table, xx report and figure from the results tree, then run
# a consistency check.  Run from the repo root after the campaign
# (scripts/run_full_campaign_slurm.sh) has finished.
#
# Figures from the post-processed LOADMATCH tables (fig_wws_vs_bau and the
# data-center land/jobs/nameplate/LCOE figures) need the per-case workbooks in
# data/results_verification/Tables/ and are skipped without them.
#
# Usage:
#   bash scripts/export_all_results.sh
# ============================================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"
if [ -f .venv/bin/activate ]; then source .venv/bin/activate; fi
RESULTS=data/results_verification

echo "=== 1/4  Tables ==="
python -m scripts.export_comparison          # comparison_summary.csv
python -m scripts.export_dc_comparison       # dc_comparison_summary.csv
python -m scripts.export_bau_comparison      # bau_comparison_summary.csv
python -m scripts.export_results             # results_export.xlsx

echo "=== 2/4  xx reports ==="
python -m scripts.rebuild_xx_reports

echo "=== 3/4  Figures ==="
python -m scripts.plot_comparison            # cost, land, solve time
python -m scripts.plot_structure             # factor, cost-category, generation-mix changes
python -m scripts.plot_convergence           # SI: GA convergence
python -m scripts.plot_dispatch              # SI: dispatch + storage state of charge
python -m scripts.plot_dc_comparison         # data-center cost
if [ -d "$RESULTS/Tables" ]; then
  python -m scripts.plot_bau_comparison      # WWS vs BAU
  python -m scripts.plot_dc_tables           # data-center land use, jobs
  python -m scripts.plot_dc_economics        # data-center nameplate, LCOE
else
  echo "  ($RESULTS/Tables/ not found: table-based figures skipped)"
fi

echo "=== 4/4  Consistency check ==="
python - <<'PY'
import sys
import pandas as pd

df = pd.read_csv("data/results_verification/comparison_summary.csv")
df = df[~df["region"].astype(str).str.startswith("TOTAL")].copy()
for c in ["baseline_cost_bil_per_yr", "ga_cost_bil_per_yr", "pct_savings",
          "ga_newland_pct_regland", "ga_scratch2_newland_pct_regland"]:
    if c in df.columns:
        df[c] = pd.to_numeric(df[c], errors="coerce")
n = len(df)
equal = ((df["baseline_cost_bil_per_yr"] - df["ga_cost_bil_per_yr"]).abs() < 1e-6).sum()
print(f"  regions: {n}")
print(f"  baseline cost == GA cost: {equal}/{n}")
print(f"  pct_savings min/max: {df['pct_savings'].min():.2f}% / {df['pct_savings'].max():.2f}%")
for col, name in (("ga_newland_pct_regland", "Baseline GA"),
                  ("ga_scratch2_newland_pct_regland", "Scratch GA")):
    if col in df.columns:
        over = df[df[col] > 7.0]["region"].tolist()
        print(f"  {name} regions over the 7% land cap: {over if over else 'none'}")
if n and equal == n:
    # A seeded re-evaluation must never overwrite the trial-and-error baseline
    # of the main results directories.
    print("  ERROR: every baseline cost equals the GA cost; the baseline summaries "
          "were overwritten.  Re-run the baseline optimization.")
    sys.exit(1)
PY

echo "=== Export complete ==="

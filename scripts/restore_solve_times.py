"""Restore real GA solve times into freshly-exported comparison CSVs.

The export re-derives per-region timing from optimal_summary.json, but those are
unreliable after evaluate-only re-evals (stamped to ~0) and partial re-runs.  The
real GA solve times live in the .SOLVETIMES backup CSVs.  This merges them back:
for each timing cell, KEEP the freshly-exported value when it looks like a real
GA run (optimize_seconds above GA_MIN_S), otherwise fall back to the backup.  So
regions you just re-ran keep their new real times; everything else recovers the
backup time.  Cost / factor / land columns are left untouched (the fresh export
is authoritative for those).

Run on the Mac after pulling the fresh CSVs:
    python -m scripts.restore_solve_times
Writes a .PRERESTORE copy of each CSV first.
"""
from pathlib import Path
import shutil

import pandas as pd

R = Path(__file__).resolve().parent.parent / "data" / "results_verification"

# A real GA optimize_seconds is thousands of seconds; an evaluate-only stamp is
# a few.  Anything below this is treated as "not a real run" -> use the backup.
GA_MIN_S = 120.0

# (fresh csv, backup csv, [(optimize_seconds col, companion cols carried with it)])
# Each timing group is gated on its optimize_seconds: if that is a real GA time
# we keep the whole group fresh, else we take the whole group from the backup.
JOBS = [
    ("comparison_summary.csv", "comparison_summary.SOLVETIMES.csv", [
        ("optimize_seconds", ["total_seconds", "n_evaluations"]),
        ("scratch_optimize_seconds", ["scratch_n_evaluations"]),
        ("scratch2_optimize_seconds", ["scratch2_n_evaluations"]),
    ]),
    ("dc_comparison_summary.csv", "dc_comparison_summary.SOLVETIMES.csv", [
        ("dc1_optimize_seconds", []),
        ("dc2_optimize_seconds", []),
        ("dc2rc_optimize_seconds", []),
        ("dc2bat_optimize_seconds", []),
        ("dc2h2_optimize_seconds", []),
    ]),
]


def _num(s):
    return pd.to_numeric(s, errors="coerce")


def main():
    for fresh_name, backup_name, groups in JOBS:
        fresh_p, backup_p = R / fresh_name, R / backup_name
        if not fresh_p.exists():
            print(f"SKIP {fresh_name}: not present"); continue
        if not backup_p.exists():
            print(f"SKIP {fresh_name}: no backup {backup_name}"); continue

        fresh = pd.read_csv(fresh_p)
        backup = pd.read_csv(backup_p).set_index("region")
        shutil.copy(fresh_p, str(fresh_p) + ".PRERESTORE")

        restored = 0
        for lead, companions in groups:
            if lead not in fresh.columns:
                continue
            for i, region in enumerate(fresh["region"].astype(str)):
                if region.startswith("TOTAL") or region not in backup.index:
                    continue
                fresh_val = pd.to_numeric(fresh.at[i, lead], errors="coerce")
                if pd.notna(fresh_val) and fresh_val >= GA_MIN_S:
                    continue  # real fresh GA run -> keep it
                for col in [lead] + companions:
                    if col in fresh.columns and col in backup.columns:
                        fresh.at[i, col] = backup.at[region, col]
                restored += 1

        fresh.to_csv(fresh_p, index=False)
        print(f"{fresh_name}: restored backup times for {restored} region/group cells "
              f"(kept fresh where optimize_seconds >= {GA_MIN_S:.0f}s)")


if __name__ == "__main__":
    main()

"""Pre-share QA gate: physical / economic sanity checks on the exported result
tables.  Complements scripts.check_locked_drift (lock integrity) by catching the
things a locked-variable diff cannot: infeasible runs, impossible costs, missing
regions, and broken case orderings.

Checks (read from the two comparison CSVs):

  base optimization (comparison_summary.csv)
    * every GA / scratch run feasible
    * baseline cost NOT identical to GA cost (the baseline-clobber bug)
    * new-land within cap flags (land_ok / scratch2_land_ok)

  data centers (dc_comparison_summary.csv)
    * every dc case feasible in every region
    * no data-center case cheaper than the no-dc optimum (delta_pct >= 0)
    * nesting: dc2 (all storage flexible) <= dc2bat and dc2h2 (whose free sets
      are subsets of dc2's, so dc2 can always match them); a violation beyond GA
      noise means dc2 under-converged or something is wrong
    * new-land within the 7% cap (small overshoot reported, not failed)
    * completeness: all regions present, every case column populated

Run from the repo root after export (Sherlock or Mac):
    python -m scripts.qa_results
Exits nonzero if any HARD check fails (feasibility, clobber, completeness).
"""
from pathlib import Path
import sys

import pandas as pd

R = Path(__file__).resolve().parent.parent / "data" / "results_verification"
DC_CASES = ["dc1", "dc2", "dc2rc", "dc2bat", "dc2h2"]
LAND_CAP = 7.0
DELTA_FLOOR = -0.1     # % ; dc cost below no-dc by more than this is suspicious
NEST_TOL = 1.0         # % ; dc2 above a restricted case by more than this is a flag

_hard_fail = False


def _num(df, col):
    return pd.to_numeric(df[col], errors="coerce") if col in df.columns else None


def _mark(ok, label, detail=""):
    global _hard_fail
    tag = "PASS" if ok else "FAIL"
    if not ok:
        _hard_fail = True
    print(f"  [{tag}] {label}" + (f"  -> {detail}" if detail else ""))


def _warn(clean, label, detail=""):
    tag = "ok" if clean else "WARN"
    print(f"  [{tag}] {label}" + (f"  -> {detail}" if detail else ""))


def check_base():
    p = R / "comparison_summary.csv"
    print("=" * 72)
    print(f"base optimization  ({p.name})")
    print("=" * 72)
    if not p.exists():
        _mark(False, "file present", "missing")
        return
    df = pd.read_csv(p)
    df = df[~df["region"].astype(str).str.startswith("TOTAL")].copy()
    n = len(df)
    print(f"  regions: {n}")
    _mark(n == 30, "all 30 regions present", "" if n == 30 else f"{n} found")

    # GA optimum feasibility.  The *_start_feasible columns are the pre-bootstrap
    # starting point (infeasible by design for scratch), so they are NOT an error
    # signal; scratch success is instead confirmed by a recorded optimum cost.
    if "ga_feasible" in df.columns:
        bad = df[df["ga_feasible"].astype(str).str.lower().isin(["false", "0", "nan"])]["region"].tolist()
        _mark(not bad, "GA optimum feasible", ", ".join(bad))
    sc = _num(df, "ga_scratch2_cost_bil_per_yr")
    if sc is not None:
        miss = df.loc[sc.isna(), "region"].tolist()
        _mark(not miss, "scratch2 optimum found (cost recorded)", ", ".join(miss))

    bcost, gcost = _num(df, "baseline_cost_bil_per_yr"), _num(df, "ga_cost_bil_per_yr")
    if bcost is not None and gcost is not None:
        clob = int(((bcost - gcost).abs() < 1e-6).sum())
        _mark(clob < n, "baseline cost != GA cost (no clobber)",
              "" if clob < n else "every region identical -> baseline CLOBBERED")
        worse = df.loc[gcost > bcost * 1.0001, "region"].tolist()
        _warn(not worse, "GA no worse than baseline", ", ".join(worse))

    for col in ["land_ok", "scratch2_land_ok"]:
        if col in df.columns:
            bad = df[df[col].astype(str).str.lower().isin(["false", "0"])]["region"].tolist()
            _warn(not bad, f"{col}", ", ".join(bad))


def check_dc():
    p = R / "dc_comparison_summary.csv"
    print("=" * 72)
    print(f"data centers  ({p.name})")
    print("=" * 72)
    if not p.exists():
        _mark(False, "file present", "missing")
        return
    df = pd.read_csv(p)
    df = df[~df["region"].astype(str).str.startswith("TOTAL")].copy()
    n = len(df)
    print(f"  regions: {n}")
    _mark(n == 30, "all 30 regions present", "" if n == 30 else f"{n} found")

    for case in DC_CASES:
        fcol, dcol = f"{case}_feasible", f"{case}_delta_pct"
        # completeness: cost column populated for every region
        ccol = f"{case}_cost_bil_per_yr"
        if ccol in df.columns:
            miss = df[_num(df, ccol).isna()]["region"].tolist()
            _mark(not miss, f"{case}: result in every region", ", ".join(miss))
        # feasibility
        if fcol in df.columns:
            bad = df[df[fcol].astype(str).str.lower().isin(["false", "0"])]["region"].tolist()
            _mark(not bad, f"{case}: feasible everywhere", ", ".join(bad))
        # cost cannot fall below the no-dc optimum
        d = _num(df, dcol)
        if d is not None:
            neg = df.loc[d < DELTA_FLOOR, "region"].tolist()
            _warn(not neg, f"{case}: cost >= no-dc optimum (delta >= 0)",
                  ", ".join(f"{r}({v:.1f}%)" for r, v in zip(df['region'], d) if v < DELTA_FLOOR))
        # land cap
        lcol = f"{case}_land_pct"
        l = _num(df, lcol)
        if l is not None:
            over = [(r, v) for r, v in zip(df["region"], l) if pd.notna(v) and v > LAND_CAP]
            _warn(not over, f"{case}: new land <= {LAND_CAP:.0f}%",
                  ", ".join(f"{r} {v:.1f}%" for r, v in over))

    # nesting invariant: dc2 <= dc2bat and dc2 <= dc2h2 (restricted subproblems)
    c2 = _num(df, "dc2_cost_bil_per_yr")
    for restricted in ("dc2bat", "dc2h2"):
        cr = _num(df, f"{restricted}_cost_bil_per_yr")
        if c2 is not None and cr is not None:
            viol = [(r, cv, rv) for r, cv, rv in zip(df["region"], c2, cr)
                    if pd.notna(cv) and pd.notna(rv) and cv > rv * (1 + NEST_TOL / 100.0)]
            _warn(not viol, f"dc2 <= {restricted} (dc2 has the superset of free vars)",
                  ", ".join(f"{r} dc2={cv:.3g}>{rv:.3g}" for r, cv, rv in viol))


def main():
    check_base()
    print()
    check_dc()
    print()
    print("HARD checks:", "FAILED" if _hard_fail else "passed",
          "(WARN lines are advisory: GA noise, accepted land overshoots, feasibility statements)")
    sys.exit(1 if _hard_fail else 0)


if __name__ == "__main__":
    main()

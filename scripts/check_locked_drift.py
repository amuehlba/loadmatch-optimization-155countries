"""Check whether any LOCKED variable drifted from the base optimum in the
storage-restricted data-center cases -- WITHOUT re-running anything.

Every dc case seeds its locked variables from the region's base optimum and is
supposed to hold them there, so in a correct run each locked variable in the dc
result equals the base value exactly.  Any difference means the now-fixed
lock-violation bug (seed-population spread / end-of-run polish ignoring the lock
set) moved it in that region.

This compares each region's saved genetic_factors.dat against the base optimum
over the case's FULL lock set, for all three restricted cases:

  case2bat (WSB,     _dc2bat)  batteries free; hydrogen + rest locked
  case2h2  (WSH,     _dc2h2)   hydrogen free; batteries + rest locked
  case3    (rooftop, _dc2rc)   residential/commercial PV + storage free;
                               wind + utility PV + rest locked

It prints, per case, every region with a drifted locked key (base -> dc value)
and a ready-to-paste `--array=` list of just those regions.

Run from the repo root on Sherlock (genetic_factors.dat files are not on the Mac):
    python -m scripts.check_locked_drift
"""
from pathlib import Path

from src.io.dat_parser import read_dat

REPO_ROOT = Path(__file__).resolve().parent.parent
RESULTS = REPO_ROOT / "data" / "results_verification"

# Lock sets, copied verbatim from run_all_regions_dc_slurm.sh.  "fixed" keys are
# never written to genetic_factors.dat, so they are absent from the files and
# skipped automatically by the key-intersection below.
ALWAYS_LOCK = ["HCDDADD", "FMORTBAU"]
LOCK_CASE2 = ["FACRESPV", "FACCOMPV", "CSPTURBFAC", "FACSHT", "STORHCOLD",
              "STORHHWAT", "STORHPHS", "STORUGDYS", "CPERFORM"]
LOCK_CASE3 = ["FACONWIN", "FACOFFWIN", "FACUTILPV", "CSPTURBFAC", "FACSHT",
              "STORHCOLD", "STORHHWAT", "STORHPHS", "STORUGDYS", "CPERFORM"]
CASES = [
    ("case2bat", "dc2bat", LOCK_CASE2 + ["FCDISCH", "FCCHARG", "DAYH2STOR"] + ALWAYS_LOCK),
    ("case2h2",  "dc2h2",  LOCK_CASE2 + ["BATDISCH", "STORHBAT"] + ALWAYS_LOCK),
    ("case3",    "dc2rc",  LOCK_CASE3 + ALWAYS_LOCK),
]


# A locked value is copied from the seed and never mutated, so a correct run
# reproduces it exactly; anything past float round-trip noise is real drift.
def _differs(a: float, b: float) -> bool:
    return abs(a - b) > max(1e-9, 1e-6 * abs(b))


def _regions():
    regions = []
    for line in (REPO_ROOT / "config" / "workflow.yaml").read_text().splitlines():
        s = line.strip()
        if s.startswith("-"):
            name = s[1:].strip()
            if name and name[0].isupper():
                regions.append(name)
    return regions


def main():
    regions = _regions()
    idx = {r: i for i, r in enumerate(regions)}

    for case, suffix, locked in CASES:
        print("=" * 72)
        print(f"{case}  (_{suffix})  checking {len(locked)} locked keys")
        print("=" * 72)

        affected, missing = [], []
        for r in regions:
            base_f = RESULTS / r / "genetic_factors.dat"
            dc_f = RESULTS / f"{r}_{suffix}" / "genetic_factors.dat"
            if not base_f.exists() or not dc_f.exists():
                missing.append(r)
                continue
            base, dc = read_dat(str(base_f)), read_dat(str(dc_f))
            drift = [(k, base[k], dc[k]) for k in locked
                     if k in base and k in dc and _differs(dc[k], base[k])]
            if drift:
                affected.append(r)
                cols = ", ".join(f"{k} {b:.6g}->{d:.6g}" for k, b, d in drift)
                print(f"  DRIFT  {r:<16}[{idx[r]:>2}]  {cols}")

        if not affected:
            print("  no locked-variable drift in any region")
        if missing:
            print(f"  [no saved result yet: {', '.join(missing)}]")

        ids = sorted(idx[r] for r in affected)
        print(f"\n  affected ({len(affected)}): {', '.join(affected) or '-'}")
        if ids:
            print(f"  re-run: sbatch --array={','.join(map(str, ids))} "
                  f"--export=ALL,DC_CASE={case} scripts/run_all_regions_dc_slurm.sh")
        print()


if __name__ == "__main__":
    main()

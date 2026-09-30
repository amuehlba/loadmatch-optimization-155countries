#!/usr/bin/env python3
"""Build a seed factor file from a base optimum plus explicit KEY=VALUE overrides.

For a hand-tuned solution described as "the base case, except for these few
factors": takes the base optimum's full factor set, overlays only the named
factors, and writes a KEY = VALUE file that --baseline-start can read.

Example (hand-tuned rooftop (RBH) solution for SOUTHAM-SE):

  python -m scripts.build_seed_from_overrides \
      --base data/results_verification/SOUTHAM-SE/genetic_factors.dat \
      --out  data/seed_southamse_rbh.dat \
      FACONWIN=1.0 FACOFFWIN=0.283 FACUTILPV=0.657 FACRESPV=6.5 FACCOMPV=7.0 \
      BATDISCH=0.2 FCCHARG=0.019 FCDISCH=0.019 DAYH2STOR=3.9629 STORUGDYS=0.9383

Then evaluate/optimize it via reoptimize_fromseed_slurm.sh with SEEDFILE=<out>.
"""
import argparse
from pathlib import Path

from scripts.run_full_workflow import load_baseline_start, FACTOR_KEYS, PARAM_REGISTRY


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", required=True, help="base optimum factor file")
    ap.add_argument("--out", required=True, help="seed file to write")
    ap.add_argument("overrides", nargs="+", metavar="KEY=VALUE",
                    help="factors to overlay on the base, e.g. BATDISCH=0.2")
    args = ap.parse_args()

    factors = load_baseline_start(Path(args.base))  # full FACTOR_KEYS dict

    applied, unknown = {}, []
    for tok in args.overrides:
        if "=" not in tok:
            ap.error(f"override '{tok}' is not KEY=VALUE")
        k, v = tok.split("=", 1)
        k = k.strip().upper()
        if k not in PARAM_REGISTRY:
            unknown.append(k); continue
        if k not in FACTOR_KEYS:
            unknown.append(k + " (fixed, not a design variable)"); continue
        factors[k] = float(v)
        applied[k] = float(v)

    if unknown:
        raise SystemExit("refusing to write: unknown/non-design keys: "
                         + ", ".join(unknown))

    with open(args.out, "w") as f:
        for k in FACTOR_KEYS:
            f.write(f"{k} = {factors[k]}\n")

    print(f"wrote {args.out} ({len(FACTOR_KEYS)} factors)")
    print("overrode:")
    for k in sorted(applied):
        print(f"  {k:12s} = {applied[k]}")


if __name__ == "__main__":
    main()

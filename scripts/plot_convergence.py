"""GA convergence of all regions (fig_convergence).

For every region: the best feasible cost found up to each generation, as a
reduction relative to the trial-and-error cost, for the Baseline GA (solid,
<REGION>/) and the Scratch GA (dashed, <REGION>_scratch2/).  Reads each
results directory's factor_history.log.

Usage:
    python -m scripts.plot_convergence
"""
import argparse
import json
import re
from pathlib import Path
from typing import Dict, List, Optional

import matplotlib
matplotlib.use("Agg")
import matplotlib as mpl
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

from scripts.plot_style import LABEL_GA, LABEL_SCRATCH, TAE_IT, region_label
from src.regions import config_regions

REPO_ROOT = Path(__file__).resolve().parent.parent
RESULTS_ROOT = REPO_ROOT / "data" / "results_verification"
SCRATCH_SUFFIX = "_scratch2"

mpl.rcParams.update({
    "font.family":       "sans-serif",
    "font.sans-serif":   ["Helvetica", "Arial", "DejaVu Sans"],
    "mathtext.fontset":  "custom",       # \mathit{Baseline/Scratch} in the text font
    "mathtext.rm":       "sans",
    "mathtext.it":       "sans:italic",
    "mathtext.bf":       "sans:bold",
    "font.size":         8,
    "axes.labelsize":    8,
    "xtick.labelsize":   7,
    "ytick.labelsize":   7,
    "legend.fontsize":   7,
    "lines.linewidth":   1.5,
    "axes.linewidth":    0.8,
    "xtick.major.width": 0.8,
    "ytick.major.width": 0.8,
    "axes.spines.top":   False,
    "axes.spines.right": False,
    "axes.grid":         True,
    "grid.linewidth":    0.4,
    "grid.alpha":        0.4,
    "savefig.dpi":       300,
    "figure.dpi":        150,
})

# tab20 plus 12 tab20b colours: distinct colours for all 30 regions.
_PALETTE = (list(plt.cm.tab20.colors)
            + [plt.cm.tab20b.colors[i] for i in [0, 4, 8, 12, 16, 1, 5, 9, 13, 17, 2, 6]])


def parse_factor_history(log_path: Path) -> List[Dict]:
    """factor_history.log -> one dict per evaluation (label, feasible,
    cost_mn_bil_per_year, and the lower-case design variables)."""
    records: List[Dict] = []
    current: Dict = {}
    with log_path.open() as handle:
        for raw in handle:
            line = raw.strip()
            if not line:
                if current:
                    records.append(current)
                    current = {}
                continue
            if line.startswith("LABEL:"):
                if current:
                    records.append(current)
                    current = {}
                current["label"] = line.split(":", 1)[1].strip()
            elif line.startswith("FEASIBLE:"):
                current["feasible"] = line.split(":", 1)[1].strip().lower() == "true"
            elif line.startswith("COST_MN_BIL_PER_YEAR:"):
                current["cost_mn_bil_per_year"] = float(line.split(":", 1)[1].strip())
            elif "=" in line:
                key, value = line.split("=")
                current[key.strip().lower()] = float(value)
    if current:
        records.append(current)
    return records


def _generation_best(results_dir: Path):
    """(records, [(generation, cumulative best feasible cost), ...]) for one
    results directory, or (records, None) without GA generations or any
    feasible evaluation."""
    log_path = results_dir / "factor_history.log"
    if not log_path.exists():
        return None, None
    records = parse_factor_history(log_path)
    by_gen: Dict[int, List[Dict]] = {}
    for rec in records:
        m = re.match(r"GA-gen(\d+)-ind(\d+)", rec.get("label", ""))
        if m:
            by_gen.setdefault(int(m.group(1)), []).append(rec)
    if not by_gen or not any(r.get("feasible") is True
                             and r.get("cost_mn_bil_per_year", float("inf")) < float("inf")
                             for r in records):
        return records, None
    best, cum = [], float("inf")
    for g in range(1, max(by_gen) + 1):
        costs = [r["cost_mn_bil_per_year"] for r in by_gen.get(g, [])
                 if r.get("feasible") is True]
        if costs:
            cum = min(cum, min(costs))
        best.append((g, cum))
    return records, best


def _trial_and_error_cost(records, results_dir: Path) -> Optional[float]:
    """Cost of the trial-and-error start: the first evaluation logged by the
    Baseline run, or else its baseline / reference summary JSON."""
    for rec in records or []:
        c = rec.get("cost_mn_bil_per_year", float("inf"))
        if c < float("inf"):
            return c
    for jname in ("baseline_summary.json", "canonical_baseline_summary.json"):
        jp = results_dir / jname
        if jp.exists():
            try:
                v = json.loads(jp.read_text()).get("annual_cost_mn_bil_per_yr")
                if v:
                    return float(v)
            except ValueError:
                pass
    return None


def fig_convergence(results_root: Path, outdir: Path, regions=None) -> None:
    regions = regions or config_regions()
    fig, ax = plt.subplots(figsize=(13, 7))
    region_handles, ymin, ymax = [], 0.0, 0.0
    n_base = n_scr = 0

    for idx, region in enumerate(regions):
        color = _PALETTE[idx % len(_PALETTE)]
        base_dir = results_root / region
        records_b, best_b = _generation_best(base_dir)
        reference = _trial_and_error_cost(records_b, base_dir)
        if not reference or reference <= 0:
            print(f"  [convergence] {region}: no trial-and-error cost -> skipped")
            continue
        drew = False
        for best, ls, lw in ((best_b, "-", 1.5),
                             (_generation_best(results_root / (region + SCRATCH_SUFFIX))[1],
                              "--", 1.3)):
            valid = [(g, c) for g, c in (best or []) if c < float("inf")]
            if not valid:
                continue
            gens = [g for g, _ in valid]
            red = [(1 - c / reference) * 100 for _, c in valid]
            ax.plot(gens, red, lw=lw, color=color, ls=ls, alpha=0.9)
            ymin, ymax = min(ymin, min(red)), max(ymax, max(red))
            if ls == "-":
                n_base += 1
            else:
                n_scr += 1
            drew = True
        if drew:
            region_handles.append(Line2D([0], [0], color=color, lw=2.4,
                                         label=region_label(region)))

    ax.axhline(0, color="gray", lw=0.8, ls=":", alpha=0.6)
    ax.set_xlabel("Generation")
    ax.set_ylabel(f"Cost reduction vs LOADMATCH ({TAE_IT}) (%)")
    ax.set_xlim(left=1)
    ax.set_ylim(max(-60.0, ymin - 3.0), min(100.0, ymax + 3.0))

    # Legend 1: line style = optimization case.  Legend 2: region colours.
    style_handles = [
        Line2D([0], [0], color="0.25", lw=2.4, ls="-",  label=LABEL_GA),
        Line2D([0], [0], color="0.25", lw=2.4, ls="--", label=LABEL_SCRATCH),
    ]
    leg1 = ax.legend(handles=style_handles, loc="lower right", frameon=False, fontsize=10)
    ax.add_artist(leg1)
    ax.legend(handles=region_handles, bbox_to_anchor=(1.01, 1), loc="upper left",
              borderaxespad=0, frameon=False, ncol=2, fontsize=8, title="Region")
    print(f"  convergence: drew {n_base} Baseline + {n_scr} Scratch region curves "
          f"(of {len(regions)} regions)")
    fig.tight_layout()
    for ext in ("pdf", "png"):
        fig.savefig(outdir / f"fig_convergence.{ext}", bbox_inches="tight")
    plt.close(fig)
    print("  wrote fig_convergence.pdf/.png")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results-root", type=Path, default=RESULTS_ROOT)
    ap.add_argument("--outdir", type=Path, default=RESULTS_ROOT)
    args = ap.parse_args(argv)
    args.outdir.mkdir(parents=True, exist_ok=True)
    fig_convergence(args.results_root, args.outdir)


if __name__ == "__main__":
    main()

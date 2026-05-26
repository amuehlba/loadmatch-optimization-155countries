"""
export_results.py
-----------------
Export all LoadMatch regional optimisation results to a single colour-coded
XLSX file for easy inspection in Excel / LibreOffice Calc.

Layout
------
• Single sheet "Results"
• Two frozen header rows
    Row 1 – colour-coded group label (merged across the group's columns)
    Row 2 – individual column names
• Data rows: five consecutive rows per region
    (Baseline | LP | First feasible | GA (bl) | GA (LP))
  with alternating light-gray / white row-band shading so each region block
  is visually distinct.  The "region" column is always filled → fully
  machine-readable (no merged cells, no empty key columns).
• Frozen panes at row 3 / column 3 so headers and the two ID columns stay
  visible while scrolling.

Column groups
-------------
  Identification  |  Annual cost  |  Cost by category  |  Generation  |
  End use & load  |  Losses       |  Storage net flow   |
  Optimised factors  |  Fixed factors

Missing cases (LP / First feasible / GA-LP not run for a region) produce blank data rows.
  First feasible falls back to parsing lp_ga_factor_history.log if the JSON is absent.

Usage
-----
    python -m scripts.export_results
    python -m scripts.export_results --regions UNITED-STATES EUROPE JAPAN
    python -m scripts.export_results --out path/to/custom.xlsx
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REPO_ROOT    = Path(__file__).resolve().parent.parent
RESULTS_DIR  = REPO_ROOT / "data" / "results_verification"
DEFAULT_OUT  = RESULTS_DIR / "results_export.xlsx"

# ---------------------------------------------------------------------------
# Import PARAM_REGISTRY to get factor metadata
# ---------------------------------------------------------------------------
try:
    from scripts.run_full_workflow import PARAM_REGISTRY, FACTOR_KEYS
except ImportError:
    # Fallback: minimal stub so the script can still be imported stand-alone
    PARAM_REGISTRY = {}
    FACTOR_KEYS    = []

# Separate optimised vs fixed parameters (preserving registry order)
_OPT_KEYS   = [k for k in PARAM_REGISTRY if k in set(FACTOR_KEYS)]
_FIXED_KEYS = [k for k in PARAM_REGISTRY if k not in set(FACTOR_KEYS)]

# ---------------------------------------------------------------------------
# Default region list (same as Snakefile)
# ---------------------------------------------------------------------------
ALL_REGIONS = [
    "AFRICA-EAST",    "AFRICA-NORTH",   "AFRICA-SOUTH",  "AFRICA-WEST",
    "AUSTRALIA",      "CANADA",         "CENTRAL-AMERIC","CENTRAL-ASIA",
    "CHINA",          "CUBA",           "EUROPE",        "HAITI",
    "ICELAND",        "INDIA",          "ISRAEL",        "JAMAICA",
    "JAPAN",          "MADAGASCAR",     "MAURITIUS",     "MIDEAST",
    "NEW-ZEALAND",    "PHILIPPINES",    "RUSSIA",        "SOUTHAM-NW",
    "SOUTHAM-SE",     "SOUTHEAST-ASIA", "SOUTH-KOREA",   "TAIWAN",
    "UNITED-STATES",
]

# ---------------------------------------------------------------------------
# Column specification
# Each entry: (group_name, col_key, display_name, excel_number_format)
# col_key is the JSON key *or* a special token handled in _get_value().
# ---------------------------------------------------------------------------

# fmt: off
_IDENT_COLS = [
    ("Identification", "region",   "Region",   "@"),
    ("Identification", "case",     "Case",     "@"),
    ("Identification", "feasible", "Feasible", "@"),
    ("Identification", "run_type", "Run type", "@"),
]

_COST_COLS = [
    ("Annual cost ($B/yr)",  "annual_cost_lo_bil_per_yr",     "Cost LO ($B/yr)",      "0.00"),
    ("Annual cost ($B/yr)",  "annual_cost_mn_bil_per_yr",     "Cost MN ($B/yr)",      "0.00"),
    ("Annual cost ($B/yr)",  "annual_cost_hi_bil_per_yr",     "Cost HI ($B/yr)",      "0.00"),
    ("Annual cost ($B/yr)",  "capital_cost_mn_tril_per_yr",   "Capital ($T/yr, MN)",  "0.000"),
]

# cost_per_kwh_by_category is a nested dict — handled dynamically
# placeholder group name; actual columns discovered at runtime
_COST_CAT_GROUP = "Cost by category (c/kWh, MN)"

_GEN_COLS = [
    ("Generation (TWh/yr)", "wind_twh",            "Wind",             "0.0"),
    ("Generation (TWh/yr)", "solar_twh",           "Solar",            "0.0"),
    ("Generation (TWh/yr)", "hydro_twh",           "Hydro",            "0.0"),
    ("Generation (TWh/yr)", "wave_twh",            "Wave",             "0.0"),
    ("Generation (TWh/yr)", "geo_elec_twh",        "Geo (elec)",       "0.0"),
    ("Generation (TWh/yr)", "tidal_twh",           "Tidal",            "0.0"),
    ("Generation (TWh/yr)", "solar_heat_twh",      "Solar heat",       "0.0"),
    ("Generation (TWh/yr)", "geo_heat_twh",        "Geo (heat)",       "0.0"),
    ("Generation (TWh/yr)", "total_supply_twh",    "Total supply",     "0.0"),
    ("Generation (TWh/yr)", "total_generation_twh","Total gen (sum)",  "0.0"),
]

_ENDUSE_COLS = [
    ("End use & load (TWh/yr)", "end_use_total_twh",      "End-use total",    "0.0"),
    ("End use & load (TWh/yr)", "end_use_elec_twh",       "Electricity",      "0.0"),
    ("End use & load (TWh/yr)", "end_use_heat_twh",       "Heat",             "0.0"),
    ("End use & load (TWh/yr)", "end_use_cold_twh",       "Cold",             "0.0"),
    ("End use & load (TWh/yr)", "end_use_hitemp_twh",     "Hi-temp heat",     "0.0"),
    ("End use & load (TWh/yr)", "h2_elec_input_twh",      "H₂ elec input",   "0.0"),
    ("End use & load (TWh/yr)", "end_use_h2_stored_twh",  "H₂ stored use",   "0.0"),
    ("End use & load (TWh/yr)", "end_energy_generated_twh","End-energy gen",  "0.0"),
]

_LOSS_COLS = [
    ("Losses (TWh/yr)", "td_loss_twh",              "T&D",              "0.0"),
    ("Losses (TWh/yr)", "curtailment_twh",           "Curtailment",      "0.0"),
    ("Losses (TWh/yr)", "total_losses_twh",          "Total losses",     "0.0"),
    ("Losses (TWh/yr)", "elec_storage_losses_twh",   "Elec stor (sum)",  "0.0"),
    ("Losses (TWh/yr)", "thermal_storage_losses_twh","Therm stor (sum)", "0.0"),
    ("Losses (TWh/yr)", "bat_loss_twh",              "Battery",          "0.0"),
    ("Losses (TWh/yr)", "phs_loss_twh",              "PHS",              "0.0"),
    ("Losses (TWh/yr)", "h2e_loss_twh",              "H₂ elec stor",    "0.0"),
    ("Losses (TWh/yr)", "csp_loss_twh",              "CSP",              "0.0"),
    ("Losses (TWh/yr)", "cw_loss_twh",               "CW-STES",          "0.0"),
    ("Losses (TWh/yr)", "hw_loss_twh",               "HW-STES",          "0.0"),
    ("Losses (TWh/yr)", "utes_loss_twh",             "UTES",             "0.0"),
    ("Losses (TWh/yr)", "brick_loss_twh",            "Brick",            "0.0"),
    ("Losses (TWh/yr)", "bat_loss_charge_twh",       "Bat charge",       "0.0"),
    ("Losses (TWh/yr)", "bat_loss_discharge_twh",    "Bat discharge",    "0.0"),
    ("Losses (TWh/yr)", "phs_loss_charge_twh",       "PHS charge",       "0.0"),
    ("Losses (TWh/yr)", "phs_loss_discharge_twh",    "PHS discharge",    "0.0"),
    ("Losses (TWh/yr)", "h2e_loss_discharge_twh",    "H₂ discharge",    "0.0"),
]

_NET_COLS = [
    ("Storage net (TWh/yr)", "bat_net_twh",    "Battery",  "0.0"),
    ("Storage net (TWh/yr)", "phs_net_twh",    "PHS",      "0.0"),
    ("Storage net (TWh/yr)", "h2e_net_twh",    "H₂ elec", "0.0"),
    ("Storage net (TWh/yr)", "csp_net_twh",    "CSP",      "0.0"),
    ("Storage net (TWh/yr)", "cw_net_twh",     "CW-STES",  "0.0"),
    ("Storage net (TWh/yr)", "hw_net_twh",     "HW-STES",  "0.0"),
    ("Storage net (TWh/yr)", "utes_net_twh",   "UTES",     "0.0"),
    ("Storage net (TWh/yr)", "brick_net_twh",  "Brick",    "0.0"),
    ("Storage net (TWh/yr)", "h2_gas_net_twh", "H₂ gas",  "0.0"),
]
# fmt: on

# ---------------------------------------------------------------------------
# Group header colours (openpyxl ARGB hex, no alpha prefix needed)
# ---------------------------------------------------------------------------
_GROUP_FILL = {
    "Identification":               "FFF2CC",   # amber
    "Annual cost ($B/yr)":          "FCE4D6",   # light orange
    _COST_CAT_GROUP:                "FDDDC4",   # peach
    "Generation (TWh/yr)":          "DDEEFF",   # light blue
    "End use & load (TWh/yr)":      "D9EAD3",   # light green
    "Losses (TWh/yr)":              "FFD7D7",   # light red
    "Storage net (TWh/yr)":         "EAD1DC",   # light mauve
    "Optimised factors":            "D0E4F0",   # teal-blue
    "Fixed factors":                "EEEEEE",   # light gray
}
_GROUP_FILL_DARK = {k: _darken(v) for k, v in _GROUP_FILL.items()} if False else {}


def _hex_fill(hex6: str) -> "PatternFill":
    from openpyxl.styles import PatternFill
    return PatternFill("solid", fgColor=hex6)


# Alternating row bands per region group
_BAND_EVEN = "F7F7F7"   # very light gray
_BAND_ODD  = "FFFFFF"   # white

# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def _load_json(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _first_feasible_from_log(rdir: Path) -> Optional[dict]:
    """Build a minimal summary dict from lp_ga_factor_history.log.

    Used as a fallback when first_feasible_summary.json hasn't been written yet
    (i.e. the workflow ran before this feature was added).  Only factors and
    cost are populated; all other fields are absent (export shows blanks).
    """
    log_path = rdir / "lp_ga_factor_history.log"
    if not log_path.exists():
        return None
    try:
        from scripts.factor_history_tools import parse_factor_history
    except ModuleNotFoundError:
        from factor_history_tools import parse_factor_history
    recs = parse_factor_history(log_path)
    ff_rec = next((r for r in recs if r.get("feasible")), None)
    if ff_rec is None:
        return None
    factors = {
        k.upper(): float(v)
        for k, v in ff_rec.items()
        if k not in ("label", "feasible", "cost_mn_bil_per_year")
        and isinstance(v, (int, float))
    }
    cost = ff_rec.get("cost_mn_bil_per_year")
    return {
        "feasible": True,
        "annual_cost_mn_bil_per_yr": float(cost) if cost is not None else None,
        "factors": factors,
        "run_type": "first_feasible_from_log",
    }


def _lp_eval_cost_from_log(rdir: Path) -> Optional[float]:
    """Read the LP-eval cost from lp_ga_factor_history.log.

    The lp_summary.json produced by older workflow runs may have
    annual_cost_mn_bil_per_yr=null even though the value was logged correctly.
    This fallback reads the cost directly from the log.

    Searches for a record with label "LP-eval" (the exact label written by
    run_ga_from_lp) so it works correctly even if the log was appended to
    across multiple manual runs.
    """
    log_path = rdir / "lp_ga_factor_history.log"
    if not log_path.exists():
        return None
    try:
        from scripts.factor_history_tools import parse_factor_history
    except ModuleNotFoundError:
        from factor_history_tools import parse_factor_history
    for rec in parse_factor_history(log_path):
        label = str(rec.get("label", "")).strip()
        # "LP-eval" is the canonical label; "LP" catches older log formats
        if label == "LP-eval" or label == "LP":
            cost = rec.get("cost_mn_bil_per_year")
            if cost is not None and not (isinstance(cost, float) and cost != cost):
                c = float(cost)
                if not (c == float("inf") or c == float("-inf")):
                    return c
    return None


def _cases_for_region(region: str) -> List[Tuple[str, Optional[dict]]]:
    """Return list of (case_label, data_dict_or_None) for all five cases."""
    rdir = RESULTS_DIR / region
    ff_data = (_load_json(rdir / "first_feasible_summary.json")
               or _first_feasible_from_log(rdir))

    lp_data = _load_json(rdir / "lp_summary.json")
    if lp_data is not None and not lp_data.get("annual_cost_mn_bil_per_yr"):
        # Older runs wrote a different key or left cost null — patch from log
        log_cost = _lp_eval_cost_from_log(rdir)
        if log_cost is not None:
            lp_data = dict(lp_data)   # don't mutate cached dict
            lp_data["annual_cost_mn_bil_per_yr"] = log_cost

    return [
        ("Baseline",       _load_json(rdir / "baseline_summary.json")),
        ("LP",             lp_data),
        ("First feasible", ff_data),
        ("GA (bl)",        _load_json(rdir / "optimal_summary.json")),
        ("GA (LP)",        _load_json(rdir / "lp_ga_summary.json")),
    ]


def _get_value(data: dict, key: str) -> Any:
    """Retrieve a value from the summary dict by key."""
    if key.startswith("factor:"):
        param = key[len("factor:"):]
        fac = data.get("factors", {})
        if param in fac:
            return fac[param]
        # Fixed params are not in the factors dict; use PARAM_REGISTRY default
        if param in PARAM_REGISTRY:
            return PARAM_REGISTRY[param][0]
        return None
    if key.startswith("cost_cat:"):
        cat = key[len("cost_cat:"):]
        return data.get("cost_per_kwh_by_category", {}).get(cat, None)
    return data.get(key, None)


def _collect_cost_categories(regions: List[str]) -> List[str]:
    """Scan all JSON files to discover the union of cost category names."""
    cats: dict = {}   # preserve insertion order, de-duplicate
    for region in regions:
        rdir = RESULTS_DIR / region
        for fname in ("baseline_summary.json", "optimal_summary.json",
                      "lp_summary.json", "lp_ga_summary.json"):
            data = _load_json(rdir / fname)
            if data:
                for cat in data.get("cost_per_kwh_by_category", {}):
                    cats[cat] = None
    return list(cats)


# ---------------------------------------------------------------------------
# Build full column spec at runtime (cost categories discovered dynamically)
# ---------------------------------------------------------------------------

def _build_col_spec(cost_categories: List[str]) -> List[Tuple[str, str, str, str]]:
    """Return list of (group, key, display_name, number_format) for all columns."""
    spec = list(_IDENT_COLS) + list(_COST_COLS)

    for cat in cost_categories:
        spec.append((_COST_CAT_GROUP, f"cost_cat:{cat}", cat, "0.000"))

    spec += list(_GEN_COLS) + list(_ENDUSE_COLS) + list(_LOSS_COLS) + list(_NET_COLS)

    for k in _OPT_KEYS:
        desc = PARAM_REGISTRY[k][2] if k in PARAM_REGISTRY else k
        spec.append(("Optimised factors", f"factor:{k}", k, "0.0000"))

    for k in _FIXED_KEYS:
        desc = PARAM_REGISTRY[k][2] if k in PARAM_REGISTRY else k
        spec.append(("Fixed factors", f"factor:{k}", k, "0.0000"))

    return spec


# ---------------------------------------------------------------------------
# XLSX writer
# ---------------------------------------------------------------------------

def _write_xlsx(regions: List[str], out_path: Path) -> None:
    try:
        import openpyxl
        from openpyxl.styles import (
            Alignment, Border, Font, PatternFill, Side
        )
        from openpyxl.utils import get_column_letter
    except ImportError:
        sys.exit(
            "openpyxl is required for XLSX export.  "
            "Install with:  pip install openpyxl>=3.1"
        )

    cost_cats = _collect_cost_categories(regions)
    col_spec  = _build_col_spec(cost_cats)
    n_cols    = len(col_spec)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Results"

    # ── Styles ────────────────────────────────────────────────────────────────
    def _fill(hex6: str) -> PatternFill:
        return PatternFill("solid", fgColor=hex6)

    def _font(bold: bool = False, size: int = 10) -> Font:
        return Font(name="Calibri", bold=bold, size=size)

    thin  = Side(style="thin",   color="AAAAAA")
    thick = Side(style="medium", color="444444")

    def _border(top_thick: bool = False) -> Border:
        t = thick if top_thick else thin
        return Border(top=t, bottom=thin, left=thin, right=thin)

    center_align = Alignment(horizontal="center", vertical="center", wrap_text=True)
    left_align   = Alignment(horizontal="left",   vertical="center")
    right_align  = Alignment(horizontal="right",  vertical="center")

    # ── Row 1: group headers (merged) ─────────────────────────────────────────
    group_runs: List[Tuple[str, int, int]] = []   # (group, start_col, end_col)
    cur_grp, cur_start = col_spec[0][0], 1
    for ci, (grp, key, disp, fmt) in enumerate(col_spec, start=1):
        if grp != cur_grp:
            group_runs.append((cur_grp, cur_start, ci - 1))
            cur_grp, cur_start = grp, ci
    group_runs.append((cur_grp, cur_start, n_cols))

    for grp, c1, c2 in group_runs:
        hex6  = _GROUP_FILL.get(grp, "FFFFFF")
        cell  = ws.cell(row=1, column=c1, value=grp)
        cell.font      = _font(bold=True, size=9)
        cell.fill      = _fill(hex6)
        cell.alignment = center_align
        cell.border    = Border(top=thick, bottom=thin, left=thick, right=thick)
        if c2 > c1:
            ws.merge_cells(
                start_row=1, start_column=c1, end_row=1, end_column=c2
            )

    # ── Row 2: column names ───────────────────────────────────────────────────
    for ci, (grp, key, disp, fmt) in enumerate(col_spec, start=1):
        hex6  = _GROUP_FILL.get(grp, "FFFFFF")
        # Slightly darken: add 0x18 to each RGB channel (cap at FF)
        r = min(int(hex6[0:2], 16) + 0x18, 0xFF)
        g = min(int(hex6[2:4], 16) + 0x18, 0xFF)
        b = min(int(hex6[4:6], 16) + 0x18, 0xFF)
        dark_hex = f"{r:02X}{g:02X}{b:02X}"
        cell = ws.cell(row=2, column=ci, value=disp)
        cell.font      = _font(bold=True, size=8)
        cell.fill      = _fill(dark_hex)
        cell.alignment = center_align
        cell.border    = _border()

    # ── Data rows ─────────────────────────────────────────────────────────────
    row_idx = 3
    for band_i, region in enumerate(regions):
        band_hex = _BAND_EVEN if band_i % 2 == 0 else _BAND_ODD
        cases    = _cases_for_region(region)

        for case_i, (case_label, data) in enumerate(cases):
            is_first = (case_i == 0)
            for ci, (grp, key, disp, fmt) in enumerate(col_spec, start=1):
                if key == "region":
                    val = region
                elif key == "case":
                    val = case_label
                elif data is None:
                    val = None
                else:
                    val = _get_value(data, key)

                cell = ws.cell(row=row_idx, column=ci, value=val)
                cell.fill   = _fill(band_hex)
                cell.border = _border(top_thick=is_first and ci == 1)
                cell.font   = _font(bold=is_first)

                if val is None:
                    cell.alignment = center_align
                elif isinstance(val, bool):
                    cell.alignment = center_align
                    cell.value = "TRUE" if val else "FALSE"
                elif isinstance(val, (int, float)):
                    cell.number_format = fmt
                    cell.alignment     = right_align
                else:
                    cell.alignment = left_align

                # Thick top border across the entire first row of a region block
                if is_first:
                    cell.border = Border(
                        top=thick, bottom=thin, left=thin, right=thin
                    )

            row_idx += 1

    # ── Column widths ─────────────────────────────────────────────────────────
    # Fixed-width heuristic: ID cols wider, numeric cols narrower
    for ci, (grp, key, disp, fmt) in enumerate(col_spec, start=1):
        col_letter = get_column_letter(ci)
        if key == "region":
            ws.column_dimensions[col_letter].width = 18
        elif key == "case":
            ws.column_dimensions[col_letter].width = 10
        elif key == "feasible" or key == "run_type":
            ws.column_dimensions[col_letter].width = 10
        elif grp in ("Optimised factors", "Fixed factors"):
            ws.column_dimensions[col_letter].width = 11
        else:
            ws.column_dimensions[col_letter].width = 12

    # ── Freeze panes: rows 1–2 and first 2 columns ───────────────────────────
    ws.freeze_panes = "C3"

    # ── Auto-filter on header row 2 ───────────────────────────────────────────
    ws.auto_filter.ref = (
        f"A2:{get_column_letter(n_cols)}{row_idx - 1}"
    )

    # ── Row heights ───────────────────────────────────────────────────────────
    ws.row_dimensions[1].height = 22
    ws.row_dimensions[2].height = 32   # wrapped column names

    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)
    print(f"Saved {out_path}  ({row_idx - 3} data rows, {n_cols} columns)")


# ---------------------------------------------------------------------------
# CSV writer
# ---------------------------------------------------------------------------

def _write_csv(regions: List[str], out_path: Path) -> None:
    """Write a flat CSV with one header row and four data rows per region."""
    import csv

    cost_cats = _collect_cost_categories(regions)
    col_spec  = _build_col_spec(cost_cats)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)

        # Single header row: group/column name pairs joined with "/"
        header = []
        for grp, key, disp, _ in col_spec:
            header.append(f"{grp}/{disp}")
        writer.writerow(header)

        for region in regions:
            cases = _cases_for_region(region)
            for case_label, data in cases:
                row = []
                for grp, key, disp, _ in col_spec:
                    if key == "region":
                        row.append(region)
                    elif key == "case":
                        row.append(case_label)
                    elif data is None:
                        row.append("")
                    else:
                        val = _get_value(data, key)
                        row.append("" if val is None else val)
                writer.writerow(row)

    n_rows = len(regions) * 5
    print(f"Saved {out_path}  ({n_rows} data rows, {len(col_spec)} columns)")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        description="Export LoadMatch results to XLSX and/or CSV."
    )
    parser.add_argument(
        "--regions", nargs="+", default=None,
        metavar="REGION",
        help="Regions to include (default: all regions that have results)",
    )
    parser.add_argument(
        "--out", type=Path, default=DEFAULT_OUT,
        metavar="PATH",
        help=f"Output XLSX path (default: {DEFAULT_OUT})",
    )
    parser.add_argument(
        "--csv", type=Path, default=None,
        metavar="PATH",
        help="Also write a flat CSV to this path (optional)",
    )
    args = parser.parse_args(argv)

    if args.regions:
        regions = args.regions
    else:
        # Auto-discover: include any region that has at least a baseline_summary
        regions = [
            r for r in ALL_REGIONS
            if (RESULTS_DIR / r / "baseline_summary.json").exists()
               or (RESULTS_DIR / r / "optimal_summary.json").exists()
        ]
        if not regions:
            sys.exit(
                "No result JSON files found under "
                f"{RESULTS_DIR}.  Run the workflow first."
            )

    print(f"Exporting {len(regions)} region(s) → {args.out}")
    _write_xlsx(regions, args.out)

    if args.csv:
        print(f"Writing CSV → {args.csv}")
        _write_csv(regions, args.csv)


if __name__ == "__main__":
    main()

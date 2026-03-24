"""
parse_fortran_output.py
-----------------------
Parse a LoadMatch Fortran output file and save a structured JSON summary.

Both the raw output file and the JSON summary are preserved so that nothing
is lost — the JSON is purely for fast downstream access (plotting, CSV export,
comparisons).

Public API used by run_full_workflow.py:

    from scripts.parse_fortran_output import parse_and_save

    parse_and_save(
        text       = stdout_text,        # full Fortran stdout string
        factors    = best_factors_dict,  # factor dict used for this run (or None)
        region     = "UNITED-STATES",
        run_type   = "ga_optimal",       # label stored in the JSON
        out_path   = Path(".../optimal_summary.json"),
    )
"""
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional


# ---------------------------------------------------------------------------
# Scalar patterns — each captures one float value
# ---------------------------------------------------------------------------
_SCALAR: Dict[str, str] = {

    # ── Generation supply (TWh/yr, before T&D losses) ────────────────────────
    "wind_twh":                    r'TWH ON\+OFFSHORE WIND SUPPLY BEFORE T&D LOSS\s+([\d.]+)',
    "solar_twh":                   r'TWH PV\+CSP SUPPLY BEFORE T&D LOSS\s+([\d.]+)',
    "hydro_twh":                   r'TWH HYDROELECTRIC SUPPLY BEFORE T&D LOSS\s+([\d.]+)',
    "wave_twh":                    r'TWH WAVE SUPPLY BEFORE T&D LOSS\s+([\d.]+)',
    "geo_elec_twh":                r'TWH GEOTHERMAL ELEC SUPPLY BEFORE T&D LOSS\s+([\d.]+)',
    "tidal_twh":                   r'TWH TIDAL SUPPLY BEFORE T&D LOSS\s+([\d.]+)',
    "solar_heat_twh":              r'TWH SOL HOT FLUID SUPPLY BEFORE T&D LOSS\s+([\d.]+)',
    "geo_heat_twh":                r'TWH GEOTHERMAL HEAT SUPPLY BEFORE T&D LOSS\s+([\d.]+)',
    "total_supply_twh":            r'TWH TOTAL ENERGY SUPPLY BEFORE T&D LOSSES\s+([\d.]+)',

    # ── End uses (TWh/yr) ────────────────────────────────────────────────────
    "end_use_total_twh":           r'TWH END-USE TOTAL LOAD\s+MET DURING SIM\s+([\d.]+)',
    "end_use_elec_twh":            r'TWH END USE ELECTRICITY LOAD MET DURING SIM\s+([\d.]+)',
    "end_use_heat_twh":            r'TWH END USE HEAT LOAD MET BY STORAGE\s+([\d.]+)',
    "end_use_cold_twh":            r'TWH END USE COLD LOAD MET BY STORAGE\s+([\d.]+)',
    "end_use_hitemp_twh":          r'TWH END USE HI-T LOAD MET BY BRICK STORAGE\s+([\d.]+)',
    "h2_elec_input_twh":           r'TWH ELECTRICITY FOR H2 DURING SIMULATION\s+([\d.]+)',
    "end_use_h2_stored_twh":       r'TWH END USE LOAD MET BY STORED H2\+CUR ELEC\s+([\d.]+)',
    "end_energy_generated_twh":    r'END-ENERGY-GENERATED\(TWH/Y\)\s+([\d.]+)',

    # ── Losses (TWh/yr) ──────────────────────────────────────────────────────
    "td_loss_twh":                 r'TWH TRANSMISSION AND DISTRIBUTION LOSSES\s+([\d.]+)',
    "curtailment_twh":             r'TWH LOSSES FROM CURTAILMENT\s+([\d.]+)',
    "total_losses_twh":            r'TWH TOTAL LOSSES DURING SIMULATION\s+([\d.]+)',

    # ── Storage losses: charge + discharge combined (TWh/yr) ─────────────────
    "bat_loss_twh":                r'TWH LOSSES CHARG\+DISCH BATTERY STORAGE\s+([\d.]+)',
    "phs_loss_twh":                r'TWH LOSSES CHARG\+DISCHARGING PHS STORAGE\s+([\d.]+)',
    "h2e_loss_twh":                r'TWH LOSSES CHARG\+DISCH H2 ELEC STORAGE\s+([\d.]+)',
    "csp_loss_twh":                r'TWH LOSSES CHARG\+DISCHARGING CSP STORAGE\s+([\d.]+)',
    "cw_loss_twh":                 r'TWH LOSSES CHARG\+DISCH CW-STES\+PCM-ICE STOR\s+([\d.]+)',
    "hw_loss_twh":                 r'TWH LOSSES CHARG\+DISCH HW-STES STORAGE\s+([\d.]+)',
    "utes_loss_twh":               r'TWH LOSSES CHARG\+DISCHARGING UTES STORAGE\s+([\d.]+)',
    "brick_loss_twh":              r'TWH LOSSES CHARG\+DISCHARGING BRICK HT STOR\s+([\d.]+)',

    # ── Storage charge-only losses (TWh/yr) ──────────────────────────────────
    "bat_loss_charge_twh":         r'TWH LOSSES CHARGING BATTERY STORAGE\s+([\d.]+)',
    "bat_loss_discharge_twh":      r'TWH LOSSES DISCHARG BATTERY STORAGE\s+([\d.]+)',
    "phs_loss_charge_twh":         r'TWH LOSSES DURING CHARGING PHS STORAGE\s+([\d.]+)',
    "phs_loss_discharge_twh":      r'TWH LOSSES DURING DISCHARGING PHS STORAGE\s+([\d.]+)',
    "h2e_loss_discharge_twh":      r'TWH LOSSES DISCHARG H2 ELEC STORAGE\s+([\d.]+)',

    # ── Net storage change: positive=withdrawn, negative=added (TWh/yr) ──────
    "bat_net_twh":                 r'TWH USED FROM\(\+\) ADDED TO\(-\) BAT STORAGE\s+([-\d.]+)',
    "phs_net_twh":                 r'TWH USED FROM\(\+\) ADDED TO\(-\) PHS STORAGE\s+([-\d.]+)',
    "h2e_net_twh":                 r'TWH USED FROM\(\+\) ADDED TO\(-\) H2 ELEC STOR\s+([-\d.]+)',
    "csp_net_twh":                 r'TWH USED FROM\(\+\) ADDED TO\(-\) CSP STORAGE\s+([-\d.]+)',
    "cw_net_twh":                  r'TWH USED FROM\(\+\) ADDED TO\(-\) CW-STES\+PCMICE\s+([-\d.]+)',
    "hw_net_twh":                  r'TWH USED FROM\(\+\) ADDED TO\(-\) HW-STES STOR\s+([-\d.]+)',
    "utes_net_twh":                r'TWH USED FROM\(\+\) ADDED TO\(-\) UTES STORAGE\s+([-\d.]+)',
    "brick_net_twh":               r'TWH USED FROM\(\+\) ADDED TO\(-\) BRICK STORAGE\s+([-\d.]+)',
    "h2_gas_net_twh":              r'TWH USED FROM\(\+\) ADDED TO\(-\) H2 STORAGE\s+([-\d.]+)',

    # ── Capital cost (annualised, $trillion/yr, medium estimate) ─────────────
    "capital_cost_mn_tril_per_yr": (
        r'ANNCOSTL\s+ANNCOSTM\s+ANNCOSTH\s+\(\$TRIL\)\s+=\s+[\d.]+\s+([\d.]+)'
    ),
}

# Annual total system cost (lo / mn / hi in $B/yr)
_ANNUAL_COST_RE = re.compile(
    r'ANNUAL TOT ENERGY COST.*?LO MN HI=\s*([-\d.Ee+]+)\s+([-\d.Ee+]+)\s+([-\d.Ee+]+)'
)

# Per-category costs:  COST <name> (C/KWH) LO MN HI= lo mn hi
_COST_LINE_RE = re.compile(
    r'COST\s+(.+?)\s+\(C/KWH\)\s+LO MN HI=\s*([\d.]+)\s+([\d.]+)\s+([\d.]+)'
)

_ELEC_STOR_LOSS_KEYS  = ("bat_loss_twh", "phs_loss_twh", "h2e_loss_twh", "csp_loss_twh")
_THERM_STOR_LOSS_KEYS = ("cw_loss_twh",  "hw_loss_twh",  "utes_loss_twh", "brick_loss_twh")


# ---------------------------------------------------------------------------
# Core parsing
# ---------------------------------------------------------------------------

def _check_feasibility(text: str) -> bool:
    upper = text.upper()
    return not (
        "REMAINING INFLEX LOAD" in upper
        or "EXCESIN)>0" in upper
        or "UNMET" in upper
        or "UNSERVED" in upper
    )


def parse_output(
    text: str,
    factors: Optional[Dict[str, float]] = None,
    region: Optional[str] = None,
    run_type: Optional[str] = None,
) -> dict:
    """
    Parse a full Fortran stdout string and return a structured summary dict.

    Parameters
    ----------
    text:      Complete Fortran output text.
    factors:   Dict of parameter values used in this run (written to JSON).
               Pass None for runs where the factors are not known (e.g. the
               canonical Jacobson baseline file).
    region:    Region label (e.g. "UNITED-STATES").
    run_type:  Short label stored in the JSON (e.g. "baseline", "ga_optimal").
    """
    summary: dict = {
        "region":    region,
        "run_type":  run_type,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "feasible":  _check_feasibility(text),
    }

    # ── Scalar fields ─────────────────────────────────────────────────────────
    for key, pattern in _SCALAR.items():
        m = re.search(pattern, text)
        summary[key] = float(m.group(1)) if m else None

    # ── Annual total system cost ($B/yr) ──────────────────────────────────────
    m = _ANNUAL_COST_RE.search(text)
    if m:
        summary["annual_cost_lo_bil_per_yr"] = float(m.group(1))
        summary["annual_cost_mn_bil_per_yr"] = float(m.group(2))
        summary["annual_cost_hi_bil_per_yr"] = float(m.group(3))
    else:
        summary["annual_cost_lo_bil_per_yr"] = None
        summary["annual_cost_mn_bil_per_yr"] = None
        summary["annual_cost_hi_bil_per_yr"] = None

    # ── Per-category costs (c/kWh, mean value) ────────────────────────────────
    summary["cost_per_kwh_by_category"] = {
        m.group(1).strip(): float(m.group(3))
        for m in _COST_LINE_RE.finditer(text)
    }

    # ── Convenience derived totals ────────────────────────────────────────────
    gen_keys = ["wind_twh", "solar_twh", "hydro_twh", "wave_twh",
                "geo_elec_twh", "tidal_twh", "solar_heat_twh", "geo_heat_twh"]
    gen_vals = [summary[k] for k in gen_keys if summary.get(k) is not None]
    summary["total_generation_twh"] = round(sum(gen_vals), 5) if gen_vals else None

    e_vals = [summary[k] for k in _ELEC_STOR_LOSS_KEYS if summary.get(k) is not None]
    t_vals = [summary[k] for k in _THERM_STOR_LOSS_KEYS if summary.get(k) is not None]
    summary["elec_storage_losses_twh"]    = round(sum(e_vals), 5) if e_vals else None
    summary["thermal_storage_losses_twh"] = round(sum(t_vals), 5) if t_vals else None

    # ── Factor values used (uppercase keys for consistency with PARAM_REGISTRY) -
    if factors is not None:
        summary["factors"] = {k.upper(): round(float(v), 10) for k, v in factors.items()}

    return summary


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------

def save_summary(data: dict, path: Path) -> None:
    """Write summary as pretty-printed JSON.  Creates parent dirs if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str))
    print(f"  Saved {path.name}")


def parse_and_save(
    text: str,
    factors: Optional[Dict[str, float]],
    region: str,
    run_type: str,
    out_path: Path,
) -> dict:
    """Parse *text*, write JSON to *out_path*, print a one-line summary."""
    data = parse_output(text, factors=factors, region=region, run_type=run_type)
    save_summary(data, out_path)
    cost = data.get("annual_cost_mn_bil_per_yr")
    cost_str = f"  cost = ${cost:.1f} B/yr" if cost is not None else ""
    print(f"  [{run_type}] feasible={data['feasible']}{cost_str}")
    return data

"""
model_builder.py — LoadMatch LP warm-start model.

Decision variables map directly to the 20 optimisable PARAM_REGISTRY keys
(all non-"fixed" parameters after the 2026-04 update).  Fixed parameters
(CSPSTORGAT, HCHARCSP, UGFAC, HWFAC, MXHRDRM, HPTURBRAT, DAYBASHYD, …)
are not decision variables — their values come from the region defaults.

Sectors modelled
----------------
  Electric : onshore wind, offshore wind, PV (res/com/util), CSP
             + fixed baseload: hydro, tidal, wave, geothermal-electric
               (constant dispatch = SUPHYD/TID/WAV/GEL2050 × 1000 MW from countrystats)
             → battery, PHS, H2 fuel-cell output
  Heat     : solar thermal + fixed geothermal-heat (SUPGHT2050) + heat pumps
             → hot-water STES, heat battery, UTES (seasonal)
  Cold     : AC (from electricity)
             → cold TES
  H2       : electrolysers → H2 storage → fuel cells → electricity

Storage sizing strategy
-----------------------
For storage technologies whose **power rate** in LoadMatch is load-derived
(PHS, cold TES, hot-water STES, UTES), the power rate is treated as a
fixed constant computed from the region load data.  The duration variable
is then the only LP decision variable and the energy capacity is linear.

For technologies with an **explicit power-rate parameter** (battery, H2
fuel cell/electrolyser, heat battery), power and energy capacity are
independent LP variables to avoid bilinear products.  The LoadMatch
duration parameter (STORHBAT etc.) is recovered post-solve as energy/power.

Fixed physical constants
------------------------
  UGFAC   = 3.0   UTES charge rate multiplier (× avg heat load)
  HWFAC   = 1.0   hot-water STES charge rate multiplier (× WARMMAX)
  PHSMIN  = 0.016 TW   minimum PHS power
  HEAT_COP = 4.0  CPERFORM
  COLD_COP = 3.0
  H2_EFF   ≈ 0.447 round-trip H2 efficiency (H2CHAREFF × H2DCEFF from powerworld.f)
"""

from typing import Dict, List, Tuple
import numpy as np
import pyomo.environ as pyo


# ── Fixed physical / policy constants ──────────────────────────────────────
UGFAC    = 3.0         # UTES charge rate factor (fixed)
HWFAC    = 1.0         # hot-water STES charge rate factor (fixed)
PHSMIN   = 0.016       # TW — minimum PHS nameplate power (fixed)
HEAT_COP = 4.0         # CPERFORM (fixed)
COLD_COP = 3.0         # cold COP (fixed)
HRSPDAY  = 24.0
DAYSPY   = 365.0

# H2 efficiencies — from powerworld.f
H2_CHAREFF = 0.8338    # electrolyser charge efficiency (H2CHAREFF)
H2_DCEFF   = 0.5362    # fuel-cell discharge efficiency (H2DCEFF = 0.65 × 0.846 × 0.975)
H2_RT_EFF  = H2_CHAREFF * H2_DCEFF   # ≈ 0.447 round-trip

# Storage one-way efficiencies = sqrt(round-trip) — from powerworld.f
BAT_EFF    = 0.895 ** 0.5   # EFFBAT=0.895 RT   → ≈ 0.9461
PHS_EFF    = 0.80  ** 0.5   # EFFPHS=0.80  RT   → ≈ 0.8944
HWSTES_EFF = 0.83  ** 0.5   # EFFHSTES=0.83 RT  → ≈ 0.9110
HBAT_EFF   = 0.98  ** 0.5   # EFFHTBAT=0.98 RT  → ≈ 0.9899
UTES_EFF   = 0.56  ** 0.5   # EFFUTES=0.56  RT  → ≈ 0.7483
COLD_EFF   = 0.88  ** 0.5   # EFFCSTES=0.88 RT  → ≈ 0.9381

# ── Fortran-aligned techno-economic costs ───────────────────────────────────
# Discount rate: DISCOUNTM = 2% (mean social rate, powerworld.f lines ~150-200)
# Generator lifetimes: YEARLIFEM = (YEARLIFEL + YEARLIFEH) / 2
# Storage lifetimes:   STORLIFEM = (25 + 40) / 2 = 32.5 yr (most storage)
_D       = 0.02    # DISCOUNTM
_STOR_OM = 0.015   # OPMAINTM: storage O&M = 1.5%/yr of upfront capital

def _crf(n: float) -> float:
    """Capital recovery factor at DISCOUNTM=2% for n-year lifetime."""
    return _D * (1 + _D)**n / ((1 + _D)**n - 1)

# Generator annualized costs [$/MW/yr]:
#   capex × CRF(2%, life) × (1 + decom_frac) + om_$/KW/yr × 1000
# capex: mean(CAP2022LO + CAP2022HI + CAP2050LO + CAP2050HI) / 4 × 1e6 $/MW
# om:    OPMANTM ($/KW/yr) × 1000 → $/MW/yr
# decom: DECOMCOSTM ≈ 0.02 (2% of capital, approximate mean across technologies)
_DECOM = 0.02

_GEN = {   # (capex_$/MW, lifetime_yr, om_$/MW/yr)
    "onshore_wind":  (1_010_000, 30, 37_500),   # (1.025+1.45+0.648+0.917)/4 M$/MW; 37.5 $/KW/yr
    "offshore_wind": (2_336_000, 30, 80_000),   # (2.50+4.00+1.236+1.609)/4 M$/MW; 80.0 $/KW/yr
    "res_pv":        (1_837_000, 30, 27_500),   # (2.23+2.825+0.897+1.396)/4 M$/MW; 27.5 $/KW/yr
    "com_pv":        (1_266_000, 30, 16_500),   # (1.20+2.16+0.538+1.167)/4 M$/MW; 16.5 $/KW/yr
    "utility_pv":    (  710_000, 30, 19_500),   # (0.775+1.060+0.383+0.621)/4 M$/MW; 19.5 $/KW/yr
    "csp":           (5_326_000, 30, 50_000),   # (6.00+9.09+2.138+4.075)/4 M$/MW; 50.0 $/KW/yr
    "solar_thermal": (1_177_000, 35, 50_000),   # (1.30+1.50+0.822+1.086)/4 M$/MW; 50.0 $/KW/yr
}
GEN_COST = {   # $/MW/yr
    k: cap * _crf(life) * (1 + _DECOM) + om
    for k, (cap, life, om) in _GEN.items()
}

# H2 power-equipment installed costs and O&M — from powerworld.f
# Electrolyser + rectifier: (334.5+93.5) $/KW × 1.25 install = 535 $/KW = 535,000 $/MW
# O&M: electrolyser 7.8%/yr, rectifier 1%/yr of respective installed costs
_h2_el_mw   = (334.5 + 93.5) * 1.25 * 1_000                         # $/MW installed
_h2_el_om   = (334.5 * 1.25 * 0.078 + 93.5 * 1.25 * 0.010) * 1_000 # $/MW/yr O&M
# Compressor: 39.3 $/KW × 1.87 install = 73.5 $/KW = 73,500 $/MW; O&M 4%/yr
_h2_comp_mw = 39.3 * 1.87 * 1_000
_h2_comp_om = _h2_comp_mw * 0.04
# Fuel cell: 500 $/KW × 1.33 install = 665 $/KW = 665,000 $/MW; O&M 3.5%/yr
_h2_fc_mw   = 500.0 * 1.33 * 1_000
_h2_fc_om   = _h2_fc_mw * 0.035
_H2_LIFE    = 30   # H2 power equipment lifetime [yr]

STOR_COST_POWER = {   # $/MW/yr (annualised power-equipment capital + O&M)
    "battery":  0.0,   # battery priced per energy only in powerworld.f (COSTSTORM $/KWh)
    "h2_chg":  (_h2_el_mw + _h2_comp_mw) * _crf(_H2_LIFE) + _h2_el_om + _h2_comp_om,
    "h2_fc":   _h2_fc_mw * _crf(_H2_LIFE) + _h2_fc_om,
    "heat_bat": 0.0,   # heat battery priced per energy only in powerworld.f
}

# Storage energy costs [$/MWh/yr] = COSTSTORM ($/KWh × 1000) × (CRF + OPMAINTM)
# COSTSTORM from powerworld.f (upfront lifecycle capital, electrical-equivalent):
#   PHS=14 $/KWh-elec; cold-TES=3 $/KWh-th (COP=1 for cold charging);
#   HW-STES=12 $/KWh-elec (= 3 $/KWh-th × CPERFORM=4);
#   battery=60 $/KWh-elec; firebrick=6 $/KWh-th;
#   UTES=1.6 $/KWh-elec (= 0.4 $/KWh-th × CPERFORM=4)
# H2 tank: COSTH2TKM=250 $/kg ÷ 33.3 kWh/kg = 7508 $/MWh-H2-HHV;
#   converted to LP elec-equiv by ÷ H2DCEFF; O&M=1%/yr; lifetime 15 yr mean
_STOR_LIFE = 32.5   # STORLIFEM = (25+40)/2 yr  (PHS, HW-STES, cold-TES, UTES)
_BAT_LIFE  = 17.0   # STORLIFBM = (12+22)/2 yr  (Li-ion battery)
_HBAT_LIFE = 40.0   # STORLIFHBM (firebrick heat battery)
_H2TK_LIFE = 15.0   # H2 tank mean lifetime [yr]

STOR_COST_ENERGY = {   # $/MWh/yr
    "battery":  60_000 * (_crf(_BAT_LIFE)  + _STOR_OM),
    "phs":      14_000 * (_crf(_STOR_LIFE) + _STOR_OM),
    "cold_tes":  3_000 * (_crf(_STOR_LIFE) + _STOR_OM),
    "hw_stes":  12_000 * (_crf(_STOR_LIFE) + _STOR_OM),
    "h2":   (250.0 / 0.0333 / H2_DCEFF) * (_crf(_H2TK_LIFE) + 0.01),
    "heat_bat":  6_000 * (_crf(_HBAT_LIFE) + _STOR_OM),
    "utes":      1_600 * (_crf(_STOR_LIFE) + _STOR_OM),
}

# CSP PCM (phase-change salt) storage cost — from COSTSTORM(IPCMCSP) in powerworld.f
# Fortran accounts for PCM material separately from the turbine+mirror capital (AVCAPMN).
# PCM cost = 20 $/KWh-elec × storage_energy_MWh/MW × (CRF + OPMAINTM)
# Storage energy per MW turbine = HCHARCSP (14 h) × CSPSTORGAT (2.61244594) ≈ 36.57 MWh/MW
# Both HCHARCSP and CSPSTORGAT are fixed — CSP PCM cost is proportional to turbine MW.
_CSP_PCM_KWH     = 20_000.0             # $/MWh-elec (COSTSTORM[IPCMCSP])
_CSP_STOR_PER_MW = 14.0 * 2.61244594   # MWh stored per MW turbine (HCHARCSP × CSPSTORGAT)
CSP_PCM_PER_MW   = _CSP_PCM_KWH * _CSP_STOR_PER_MW * (_crf(_STOR_LIFE) + _STOR_OM)  # $/MW/yr

LOAD_SHED_PENALTY = 5_000_000   # $/MWh — unmet demand
CURTAIL_PENALTY   =         1   # $/MWh — excess generation (small, allow curtailment)


# ── Build model ─────────────────────────────────────────────────────────────

def build_model(inputs: Dict) -> pyo.ConcreteModel:
    """Construct the LoadMatch LP.

    Parameters
    ----------
    inputs : dict
        Output of ``src.io.data_loader.load_inputs``.  All power in MW,
        energy in MWh.

    Returns
    -------
    pyo.ConcreteModel
        Solved or unsolved Pyomo model.  Call ``collect_results(model)``
        after solving to extract PARAM_REGISTRY-aligned factors.
    """
    hours   = inputs["hours"]
    n       = len(hours)
    T       = range(n)

    base     = inputs["base_capacities_mw"]        # dict tech → MW (2050 Jacobson target)
    detail   = inputs.get("base_capacities_detail", {})
    existing = inputs.get("existing_capacities_mw", {})  # current installed MW
    avail    = inputs["availability"]              # dict tech → array[n]
    sol_av   = inputs["solar_thermal_availability"]  # array[n]
    fixed    = inputs.get("fixed_baseload_mw", {}) # dict tech → avg MW (constant dispatch)

    elec_load = np.asarray(inputs["electric_load_mw"], dtype=float)
    heat_load = np.asarray(inputs["heat_load_mw"],     dtype=float)
    cold_load = np.asarray(inputs["cold_load_mw"],     dtype=float)

    # Fixed baseload dispatch [MW] — constant at every hour.
    # Hydro, tidal, wave, geothermal-electric reduce the remaining electric load
    # that variable renewables + storage must cover.
    # Geothermal-heat is a constant direct heat supply (like solar thermal but dispatchable).
    fixed_elec_mw = (fixed.get("hydro",    0.0)
                     + fixed.get("tidal",   0.0)
                     + fixed.get("wave",    0.0)
                     + fixed.get("geo_elec",0.0))
    fixed_heat_mw = fixed.get("geo_heat", 0.0)

    # ── Derived constants (region-specific) ─────────────────────────────────

    # Rooftop PV split
    res_base = detail.get("res_rooftop_pv", base["rooftop_pv"] * 0.6)
    com_base = detail.get("com_rooftop_pv", base["rooftop_pv"] * 0.4)

    # Load averages used as proxy power rates for storage with no explicit
    # power-rate parameter
    avg_heat  = float(np.mean(heat_load))   # ≈ WARMMAX proxy (MW)
    avg_cold  = float(np.mean(cold_load))   # ≈ TSTORCOOL proxy (MW)
    avg_elec  = float(np.mean(elec_load))

    # PHS power: max(existing PHS, PHSMIN) in MW — mirrors Fortran line 10984
    phs_power_mw = max(
        inputs.get("storage", {}).get("electric", {}).get("base_power_mw", 0.0),
        PHSMIN * 1e6,
    )

    # UTES charge power = UGFAC × avg heat load (mirrors Fortran STORUGFAC usage)
    utes_power_mw = UGFAC * avg_heat

    # H2 annual load proxy (for DAYH2STOR seasonal capacity)
    # TLOADH2 in Fortran ≈ fraction of electric load used for H2 production
    # We use a conservative 5% of annual electric load as H2 demand proxy.
    annual_h2_proxy_mwh = float(np.sum(elec_load)) * 0.05

    # ── Per-technology lower bounds from existing installations ───────────────
    # For any technology currently deployed (EMW > 0), force the LP to build at
    # least LB_MIN_FRAC of the Jacobson 2050 target.
    #
    # Rationale: EMW/TMW ratios are tiny (e.g. US offshore wind = 41/526 242 ≈
    # 0.008%) so a pure EMW/TMW lower bound is invisible in practice — the LP
    # still zeroes out offshore wind and rooftop PV because utility PV is
    # cheaper.  LB_MIN_FRAC = 10% ensures every deployed technology appears at
    # meaningful scale without over-constraining the optimisation.
    LB_MIN_FRAC = 0.10

    def _gen_lb(exist_key: str, target_mw: float) -> float:
        exist = existing.get(exist_key, 0.0)
        if target_mw <= 0.0 or exist <= 0.0:
            return 0.0
        # Use whichever is larger: the exact existing/target ratio or the floor.
        return max(exist / target_mw, LB_MIN_FRAC)

    # ── Model ────────────────────────────────────────────────────────────────
    m = pyo.ConcreteModel(name=f"LoadMatchLP_{inputs['region']}")
    m.T = pyo.RangeSet(0, n - 1)

    # ════════════════════════════════════════════════════════════════════════
    # Decision variables — generation factors (→ PARAM_REGISTRY)
    # Installed capacity [MW] = factor × base_capacity_mw (2050 Jacobson target)
    # Lower bound = existing_mw / target_mw so current installations are preserved.
    # ════════════════════════════════════════════════════════════════════════
    m.faconwin   = pyo.Var(within=pyo.NonNegativeReals,
                           bounds=(_gen_lb("onshore_wind",  base["onshore_wind"]),  8))
    m.facoffwin  = pyo.Var(within=pyo.NonNegativeReals,
                           bounds=(_gen_lb("offshore_wind", base["offshore_wind"]), 8))
    m.facutilpv  = pyo.Var(within=pyo.NonNegativeReals,
                           bounds=(_gen_lb("utility_pv",    base["utility_pv"]),    8))
    m.facrespv   = pyo.Var(within=pyo.NonNegativeReals,
                           bounds=(_gen_lb("res_pv",        res_base),              8))
    m.faccompv   = pyo.Var(within=pyo.NonNegativeReals,
                           bounds=(_gen_lb("com_pv",        com_base),              8))
    m.cspturbfac = pyo.Var(within=pyo.NonNegativeReals,
                           bounds=(_gen_lb("csp",           base["csp"]),          12))
    m.facsht     = pyo.Var(within=pyo.NonNegativeReals,
                           bounds=(_gen_lb("solar_thermal", base["solar_thermal"]), 8))

    # ════════════════════════════════════════════════════════════════════════
    # Decision variables — storage with load-derived power rates
    # (duration [h or days] is the only free sizing variable → energy linear)
    # ════════════════════════════════════════════════════════════════════════

    # STORHPHS  — PHS duration (hours).  Energy = phs_power_mw × storhphs
    m.storhphs   = pyo.Var(within=pyo.NonNegativeReals, bounds=(1, 200))

    # STORHCOLD — cold TES duration (hours).  Energy = avg_cold_mw × storhcold
    m.storhcold  = pyo.Var(within=pyo.NonNegativeReals, bounds=(1, 72))

    # STORHHWAT — hot-water STES duration (hours).  Energy = avg_heat_mw × storhhwat
    m.storhhwat  = pyo.Var(within=pyo.NonNegativeReals, bounds=(1, 72))

    # STORUGDYS — UTES seasonal storage (days).  Energy = utes_power_mw × storugdys × 24
    m.storugdys  = pyo.Var(within=pyo.NonNegativeReals, bounds=(1, 180))

    # ════════════════════════════════════════════════════════════════════════
    # Decision variables — storage with explicit power-rate parameters
    # Power [MW] and energy [MWh] are decoupled LP variables.
    # Duration parameter recovered post-solve as energy / power.
    # ════════════════════════════════════════════════════════════════════════

    # Battery  →  BATDISCH [TW] = bat_power_mw / 1e6
    #             STORHBAT [h]  = bat_energy_mwh / bat_power_mw  (post-solve)
    m.bat_power_mw   = pyo.Var(within=pyo.NonNegativeReals, bounds=(0, 1e7))
    m.bat_energy_mwh = pyo.Var(within=pyo.NonNegativeReals, bounds=(0, 5e8))

    # H2 fuel cell  →  FCDISCH [TW] = h2_fc_mw / 1e6
    # H2 electrolyser → FCCHARG [TW] = h2_chg_mw / 1e6
    # H2 seasonal storage → DAYH2STOR [days] (post-solve)
    # NOTE: STORHHFC (short-term H2 hours) is inert in Fortran when IMERGH2=1 —
    # it is always overridden to 0.  No h2_energy_mwh variable; only seasonal storage.
    m.h2_fc_mw       = pyo.Var(within=pyo.NonNegativeReals, bounds=(0, 5e6))
    m.h2_chg_mw      = pyo.Var(within=pyo.NonNegativeReals, bounds=(0, 5e6))

    # H2 seasonal  →  DAYH2STOR [days] = h2_seasonal_mwh / (annual_h2_proxy / 365)
    m.h2_seasonal_mwh = pyo.Var(within=pyo.NonNegativeReals, bounds=(0, 5e8))

    # Heat battery  →  HBTDISCH [TW] = hbat_power_mw / 1e6
    #                  STORHHBT [h]  = hbat_energy_mwh / hbat_power_mw  (post-solve)
    m.hbat_power_mw   = pyo.Var(within=pyo.NonNegativeReals, bounds=(0, 5e6))
    m.hbat_energy_mwh = pyo.Var(within=pyo.NonNegativeReals, bounds=(0, 2e8))

    # ════════════════════════════════════════════════════════════════════════
    # Dispatch variables — generation (MW)
    # ════════════════════════════════════════════════════════════════════════
    m.gen_wind_on  = pyo.Var(m.T, within=pyo.NonNegativeReals)
    m.gen_wind_off = pyo.Var(m.T, within=pyo.NonNegativeReals)
    m.gen_pv_res   = pyo.Var(m.T, within=pyo.NonNegativeReals)
    m.gen_pv_com   = pyo.Var(m.T, within=pyo.NonNegativeReals)
    m.gen_pv_util  = pyo.Var(m.T, within=pyo.NonNegativeReals)
    m.gen_csp      = pyo.Var(m.T, within=pyo.NonNegativeReals)  # electric
    m.gen_solth    = pyo.Var(m.T, within=pyo.NonNegativeReals)  # heat

    # ════════════════════════════════════════════════════════════════════════
    # Dispatch variables — electric storage
    # ════════════════════════════════════════════════════════════════════════
    # Battery
    m.chg_bat  = pyo.Var(m.T, within=pyo.NonNegativeReals)
    m.dis_bat  = pyo.Var(m.T, within=pyo.NonNegativeReals)
    m.soc_bat  = pyo.Var(m.T, within=pyo.NonNegativeReals)

    # PHS
    m.chg_phs  = pyo.Var(m.T, within=pyo.NonNegativeReals)
    m.dis_phs  = pyo.Var(m.T, within=pyo.NonNegativeReals)
    m.soc_phs  = pyo.Var(m.T, within=pyo.NonNegativeReals)

    # ════════════════════════════════════════════════════════════════════════
    # Dispatch variables — H2 sector
    # ════════════════════════════════════════════════════════════════════════
    m.electrolyser = pyo.Var(m.T, within=pyo.NonNegativeReals)  # MW_elec consumed
    m.fuelcell     = pyo.Var(m.T, within=pyo.NonNegativeReals)  # MW_elec produced
    m.soc_h2       = pyo.Var(m.T, within=pyo.NonNegativeReals)  # MWh equiv.

    # ════════════════════════════════════════════════════════════════════════
    # Dispatch variables — heat sector
    # ════════════════════════════════════════════════════════════════════════
    m.heat_pump   = pyo.Var(m.T, within=pyo.NonNegativeReals)  # MW_elec → heat
    # Hot-water STES
    m.chg_hwstes  = pyo.Var(m.T, within=pyo.NonNegativeReals)
    m.dis_hwstes  = pyo.Var(m.T, within=pyo.NonNegativeReals)
    m.soc_hwstes  = pyo.Var(m.T, within=pyo.NonNegativeReals)
    # Heat battery
    m.chg_hbat    = pyo.Var(m.T, within=pyo.NonNegativeReals)
    m.dis_hbat    = pyo.Var(m.T, within=pyo.NonNegativeReals)
    m.soc_hbat    = pyo.Var(m.T, within=pyo.NonNegativeReals)
    # UTES seasonal
    m.chg_utes    = pyo.Var(m.T, within=pyo.NonNegativeReals)
    m.dis_utes    = pyo.Var(m.T, within=pyo.NonNegativeReals)
    m.soc_utes    = pyo.Var(m.T, within=pyo.NonNegativeReals)

    # ════════════════════════════════════════════════════════════════════════
    # Dispatch variables — cold sector
    # ════════════════════════════════════════════════════════════════════════
    m.ac       = pyo.Var(m.T, within=pyo.NonNegativeReals)  # MW_elec
    m.chg_cold = pyo.Var(m.T, within=pyo.NonNegativeReals)
    m.dis_cold = pyo.Var(m.T, within=pyo.NonNegativeReals)
    m.soc_cold = pyo.Var(m.T, within=pyo.NonNegativeReals)

    # ════════════════════════════════════════════════════════════════════════
    # Slack variables
    # ════════════════════════════════════════════════════════════════════════
    m.shed_elec    = pyo.Var(m.T, within=pyo.NonNegativeReals)
    m.surplus_elec = pyo.Var(m.T, within=pyo.NonNegativeReals)
    m.shed_heat    = pyo.Var(m.T, within=pyo.NonNegativeReals)
    m.surplus_heat = pyo.Var(m.T, within=pyo.NonNegativeReals)
    m.shed_cold    = pyo.Var(m.T, within=pyo.NonNegativeReals)
    m.surplus_cold = pyo.Var(m.T, within=pyo.NonNegativeReals)

    # ════════════════════════════════════════════════════════════════════════
    # Helper: availability at time t (safe for techs with zero base capacity)
    # ════════════════════════════════════════════════════════════════════════
    def av(tech, t):
        arr = avail.get(tech)
        return float(arr[t]) if arr is not None else 0.0

    # ════════════════════════════════════════════════════════════════════════
    # Generation limits
    # ════════════════════════════════════════════════════════════════════════
    m.c_wind_on  = pyo.Constraint(m.T, rule=lambda _m, t:
        _m.gen_wind_on[t]  <= av("onshore_wind", t)  * base["onshore_wind"]  * _m.faconwin)
    m.c_wind_off = pyo.Constraint(m.T, rule=lambda _m, t:
        _m.gen_wind_off[t] <= av("offshore_wind", t) * base["offshore_wind"] * _m.facoffwin)
    m.c_pv_res   = pyo.Constraint(m.T, rule=lambda _m, t:
        _m.gen_pv_res[t]   <= av("rooftop_pv", t)    * res_base              * _m.facrespv)
    m.c_pv_com   = pyo.Constraint(m.T, rule=lambda _m, t:
        _m.gen_pv_com[t]   <= av("rooftop_pv", t)    * com_base              * _m.faccompv)
    m.c_pv_util  = pyo.Constraint(m.T, rule=lambda _m, t:
        _m.gen_pv_util[t]  <= av("utility_pv", t)    * base["utility_pv"]   * _m.facutilpv)
    m.c_csp      = pyo.Constraint(m.T, rule=lambda _m, t:
        _m.gen_csp[t]      <= av("csp", t)            * base["csp"]          * _m.cspturbfac)
    m.c_solth    = pyo.Constraint(m.T, rule=lambda _m, t:
        _m.gen_solth[t]    <= float(sol_av[t])         * base["solar_thermal"] * _m.facsht)

    # ════════════════════════════════════════════════════════════════════════
    # Battery storage
    # Power cap: bat_power_mw (both charge and discharge)
    # Energy cap: bat_energy_mwh
    # ════════════════════════════════════════════════════════════════════════
    m.c_bat_chg_pow  = pyo.Constraint(m.T, rule=lambda _m, t:
        _m.chg_bat[t] <= _m.bat_power_mw)
    m.c_bat_dis_pow  = pyo.Constraint(m.T, rule=lambda _m, t:
        _m.dis_bat[t] <= _m.bat_power_mw)
    m.c_bat_soc_max  = pyo.Constraint(m.T, rule=lambda _m, t:
        _m.soc_bat[t] <= _m.bat_energy_mwh)

    def _bat_soc(m_, t):
        prev = m_.soc_bat[t - 1] if t > 0 else m_.soc_bat[n - 1]
        return m_.soc_bat[t] == prev + BAT_EFF * m_.chg_bat[t] - m_.dis_bat[t] / BAT_EFF
    m.c_bat_soc = pyo.Constraint(m.T, rule=_bat_soc)

    # ════════════════════════════════════════════════════════════════════════
    # PHS storage
    # Power cap: phs_power_mw (fixed constant)
    # Energy cap: phs_power_mw × storhphs  (linear in storhphs)
    # ════════════════════════════════════════════════════════════════════════
    m.c_phs_chg_pow  = pyo.Constraint(m.T, rule=lambda _m, t:
        _m.chg_phs[t] <= phs_power_mw)
    m.c_phs_dis_pow  = pyo.Constraint(m.T, rule=lambda _m, t:
        _m.dis_phs[t] <= phs_power_mw)
    m.c_phs_soc_max  = pyo.Constraint(m.T, rule=lambda _m, t:
        _m.soc_phs[t] <= phs_power_mw * _m.storhphs)

    def _phs_soc(m_, t):
        prev = m_.soc_phs[t - 1] if t > 0 else m_.soc_phs[n - 1]
        return m_.soc_phs[t] == prev + PHS_EFF * m_.chg_phs[t] - m_.dis_phs[t] / PHS_EFF
    m.c_phs_soc = pyo.Constraint(m.T, rule=_phs_soc)

    # ════════════════════════════════════════════════════════════════════════
    # H2 storage (seasonal only — STORHHFC is inert in Fortran at IMERGH2=1)
    # Charge power cap: h2_chg_mw (electrolyser)
    # Discharge power cap: h2_fc_mw (fuel cell)
    # Energy cap: h2_seasonal_mwh (maps to DAYH2STOR, the only active H2 size param)
    # ════════════════════════════════════════════════════════════════════════
    m.c_h2_chg_pow   = pyo.Constraint(m.T, rule=lambda _m, t:
        _m.electrolyser[t] <= _m.h2_chg_mw)
    m.c_h2_dis_pow   = pyo.Constraint(m.T, rule=lambda _m, t:
        _m.fuelcell[t]     <= _m.h2_fc_mw)
    m.c_h2_soc_max   = pyo.Constraint(m.T, rule=lambda _m, t:
        _m.soc_h2[t] <= _m.h2_seasonal_mwh)
    # Seasonal buffer must cover a minimum fraction of annual H2 proxy demand
    m.c_h2_seasonal_min = pyo.Constraint(
        expr=m.h2_seasonal_mwh >= annual_h2_proxy_mwh / DAYSPY)

    def _h2_soc(m_, t):
        prev = m_.soc_h2[t - 1] if t > 0 else m_.soc_h2[n - 1]
        return m_.soc_h2[t] == prev + H2_RT_EFF * m_.electrolyser[t] - m_.fuelcell[t]
    m.c_h2_soc = pyo.Constraint(m.T, rule=_h2_soc)

    # ════════════════════════════════════════════════════════════════════════
    # Hot-water STES
    # Power cap: avg_heat_mw × HWFAC (fixed)
    # Energy cap: avg_heat_mw × storhhwat  (linear in storhhwat)
    # ════════════════════════════════════════════════════════════════════════
    hwstes_power_mw = HWFAC * avg_heat
    m.c_hwstes_chg_pow = pyo.Constraint(m.T, rule=lambda _m, t:
        _m.chg_hwstes[t] <= hwstes_power_mw)
    m.c_hwstes_dis_pow = pyo.Constraint(m.T, rule=lambda _m, t:
        _m.dis_hwstes[t] <= hwstes_power_mw)
    m.c_hwstes_soc_max = pyo.Constraint(m.T, rule=lambda _m, t:
        _m.soc_hwstes[t] <= avg_heat * _m.storhhwat)

    def _hwstes_soc(m_, t):
        prev = m_.soc_hwstes[t - 1] if t > 0 else m_.soc_hwstes[n - 1]
        return m_.soc_hwstes[t] == prev + HWSTES_EFF * m_.chg_hwstes[t] - m_.dis_hwstes[t] / HWSTES_EFF
    m.c_hwstes_soc = pyo.Constraint(m.T, rule=_hwstes_soc)

    # ════════════════════════════════════════════════════════════════════════
    # Heat battery
    # Power cap: hbat_power_mw (both charge and discharge)
    # Energy cap: hbat_energy_mwh
    # ════════════════════════════════════════════════════════════════════════
    m.c_hbat_chg_pow  = pyo.Constraint(m.T, rule=lambda _m, t:
        _m.chg_hbat[t] <= _m.hbat_power_mw)
    m.c_hbat_dis_pow  = pyo.Constraint(m.T, rule=lambda _m, t:
        _m.dis_hbat[t] <= _m.hbat_power_mw)
    m.c_hbat_soc_max  = pyo.Constraint(m.T, rule=lambda _m, t:
        _m.soc_hbat[t] <= _m.hbat_energy_mwh)

    def _hbat_soc(m_, t):
        prev = m_.soc_hbat[t - 1] if t > 0 else m_.soc_hbat[n - 1]
        return m_.soc_hbat[t] == prev + HBAT_EFF * m_.chg_hbat[t] - m_.dis_hbat[t] / HBAT_EFF
    m.c_hbat_soc = pyo.Constraint(m.T, rule=_hbat_soc)

    # ════════════════════════════════════════════════════════════════════════
    # UTES seasonal heat storage
    # Power cap: utes_power_mw = UGFAC × avg_heat (fixed)
    # Energy cap: utes_power_mw × storugdys × 24  (linear in storugdys)
    # ════════════════════════════════════════════════════════════════════════
    m.c_utes_chg_pow  = pyo.Constraint(m.T, rule=lambda _m, t:
        _m.chg_utes[t] <= utes_power_mw)
    m.c_utes_dis_pow  = pyo.Constraint(m.T, rule=lambda _m, t:
        _m.dis_utes[t] <= utes_power_mw)
    m.c_utes_soc_max  = pyo.Constraint(m.T, rule=lambda _m, t:
        _m.soc_utes[t] <= utes_power_mw * _m.storugdys * HRSPDAY)

    def _utes_soc(m_, t):
        prev = m_.soc_utes[t - 1] if t > 0 else m_.soc_utes[n - 1]
        return m_.soc_utes[t] == prev + UTES_EFF * m_.chg_utes[t] - m_.dis_utes[t] / UTES_EFF
    m.c_utes_soc = pyo.Constraint(m.T, rule=_utes_soc)

    # ════════════════════════════════════════════════════════════════════════
    # Cold TES
    # Power cap: avg_cold_mw (fixed, proportional to cold load)
    # Energy cap: avg_cold_mw × storhcold  (linear in storhcold)
    # ════════════════════════════════════════════════════════════════════════
    m.c_cold_chg_pow  = pyo.Constraint(m.T, rule=lambda _m, t:
        _m.chg_cold[t] <= avg_cold)
    m.c_cold_dis_pow  = pyo.Constraint(m.T, rule=lambda _m, t:
        _m.dis_cold[t] <= avg_cold)
    m.c_cold_soc_max  = pyo.Constraint(m.T, rule=lambda _m, t:
        _m.soc_cold[t] <= avg_cold * _m.storhcold)

    def _cold_soc(m_, t):
        prev = m_.soc_cold[t - 1] if t > 0 else m_.soc_cold[n - 1]
        return m_.soc_cold[t] == prev + COLD_EFF * m_.chg_cold[t] - m_.dis_cold[t] / COLD_EFF
    m.c_cold_soc = pyo.Constraint(m.T, rule=_cold_soc)

    # ════════════════════════════════════════════════════════════════════════
    # Energy balance constraints
    # ════════════════════════════════════════════════════════════════════════

    def _elec_balance(m_, t):
        supply  = (m_.gen_wind_on[t] + m_.gen_wind_off[t]
                   + m_.gen_pv_res[t] + m_.gen_pv_com[t] + m_.gen_pv_util[t]
                   + m_.gen_csp[t]
                   + fixed_elec_mw          # hydro + tidal + wave + geo_elec (constant)
                   + m_.dis_bat[t] + m_.dis_phs[t] + m_.fuelcell[t]
                   + m_.shed_elec[t])
        demand  = (float(elec_load[t])
                   + m_.chg_bat[t] + m_.chg_phs[t]
                   + m_.electrolyser[t]
                   + m_.heat_pump[t] + m_.ac[t]
                   + m_.surplus_elec[t])
        return supply == demand
    m.c_elec_balance = pyo.Constraint(m.T, rule=_elec_balance)

    def _heat_balance(m_, t):
        supply  = (m_.gen_solth[t]
                   + fixed_heat_mw          # geothermal direct heat (constant)
                   + HEAT_COP * m_.heat_pump[t]
                   + m_.dis_hwstes[t] + m_.dis_hbat[t] + m_.dis_utes[t]
                   + m_.shed_heat[t])
        demand  = (float(heat_load[t])
                   + m_.chg_hwstes[t] + m_.chg_hbat[t] + m_.chg_utes[t]
                   + m_.surplus_heat[t])
        return supply == demand
    m.c_heat_balance = pyo.Constraint(m.T, rule=_heat_balance)

    def _cold_balance(m_, t):
        supply  = (COLD_COP * m_.ac[t]
                   + m_.dis_cold[t]
                   + m_.shed_cold[t])
        demand  = (float(cold_load[t])
                   + m_.chg_cold[t]
                   + m_.surplus_cold[t])
        return supply == demand
    m.c_cold_balance = pyo.Constraint(m.T, rule=_cold_balance)

    # ════════════════════════════════════════════════════════════════════════
    # Objective — minimise total annualised cost + load-shed penalty
    # ════════════════════════════════════════════════════════════════════════

    # Generation CAPEX
    gen_capex = (
        GEN_COST["onshore_wind"]  * base["onshore_wind"]  * m.faconwin
        + GEN_COST["offshore_wind"] * base["offshore_wind"] * m.facoffwin
        + GEN_COST["res_pv"]        * res_base               * m.facrespv
        + GEN_COST["com_pv"]        * com_base               * m.faccompv
        + GEN_COST["utility_pv"]    * base["utility_pv"]    * m.facutilpv
        + (GEN_COST["csp"] + CSP_PCM_PER_MW) * base["csp"]   * m.cspturbfac
        + GEN_COST["solar_thermal"] * base["solar_thermal"]  * m.facsht
    )

    # Storage CAPEX (power + energy components)
    stor_capex = (
        STOR_COST_POWER["battery"]  * m.bat_power_mw
        + STOR_COST_ENERGY["battery"]  * m.bat_energy_mwh
        + STOR_COST_ENERGY["phs"]      * phs_power_mw * m.storhphs
        + STOR_COST_POWER["h2_fc"]     * m.h2_fc_mw
        + STOR_COST_POWER["h2_chg"]    * m.h2_chg_mw
        + STOR_COST_ENERGY["h2"]       * m.h2_seasonal_mwh
        + STOR_COST_POWER["heat_bat"]  * m.hbat_power_mw
        + STOR_COST_ENERGY["heat_bat"] * m.hbat_energy_mwh
        + STOR_COST_ENERGY["hw_stes"]  * avg_heat * m.storhhwat
        + STOR_COST_ENERGY["cold_tes"] * avg_cold * m.storhcold
        + STOR_COST_ENERGY["utes"]     * utes_power_mw * m.storugdys * HRSPDAY
    )

    # Load-shed penalty
    penalty = LOAD_SHED_PENALTY * (
        sum(m.shed_elec[t] + m.shed_heat[t] + m.shed_cold[t] for t in T)
    )

    # Curtailment (small penalty to avoid free surplus)
    curtail = CURTAIL_PENALTY * (
        sum(m.surplus_elec[t] + m.surplus_heat[t] + m.surplus_cold[t] for t in T)
    )

    m.obj = pyo.Objective(expr=gen_capex + stor_capex + penalty + curtail,
                          sense=pyo.minimize)

    # Store metadata for collect_results
    m._lp_meta = {
        "phs_power_mw":       phs_power_mw,
        "avg_heat_mw":        avg_heat,
        "avg_cold_mw":        avg_cold,
        "utes_power_mw":      utes_power_mw,
        "annual_h2_proxy_mwh": annual_h2_proxy_mwh,
        "fixed_elec_mw":      fixed_elec_mw,
        "fixed_heat_mw":      fixed_heat_mw,
        "n_hours":            n,
    }

    return m


# ── Collect results ──────────────────────────────────────────────────────────

def collect_results(model: pyo.ConcreteModel) -> Dict[str, float]:
    """Extract LP solution as a dict of PARAM_REGISTRY keys → values.

    Duration parameters that depend on power/energy ratios are computed
    here post-solve.  Fixed parameters are not included — they are merged
    in export_fortran_factors.py using PARAM_REGISTRY defaults.
    """
    v = pyo.value
    meta = model._lp_meta

    phs_power_mw   = meta["phs_power_mw"]
    avg_heat_mw    = meta["avg_heat_mw"]
    avg_cold_mw    = meta["avg_cold_mw"]
    utes_power_mw  = meta["utes_power_mw"]
    annual_h2_mwh  = meta["annual_h2_proxy_mwh"]

    bat_power   = max(v(model.bat_power_mw),   1e-6)
    h2_fc       = max(v(model.h2_fc_mw),       1e-6)
    hbat_power  = max(v(model.hbat_power_mw),  1e-6)

    # Duration = energy / power (post-solve, guaranteed finite)
    storhbat  = v(model.bat_energy_mwh) / bat_power
    storhhbt  = v(model.hbat_energy_mwh) / hbat_power

    # DAYH2STOR: seasonal H2 energy / (daily H2 proxy demand in MWh)
    daily_h2_mwh  = max(annual_h2_mwh / DAYSPY, 1e-6)
    dayh2stor = v(model.h2_seasonal_mwh) / daily_h2_mwh

    return {
        # Generation factors
        "FACONWIN":  v(model.faconwin),
        "FACOFFWIN": v(model.facoffwin),
        "FACUTILPV": v(model.facutilpv),
        "FACRESPV":  v(model.facrespv),
        "FACCOMPV":  v(model.faccompv),
        "CSPTURBFAC":v(model.cspturbfac),
        "FACSHT":    v(model.facsht),
        # Storage durations (directly LP variables)
        "STORHPHS":  v(model.storhphs),
        "STORHCOLD": v(model.storhcold),
        "STORHHWAT": v(model.storhhwat),
        "STORUGDYS": v(model.storugdys),
        # Storage durations (computed post-solve)
        "STORHBAT":  storhbat,
        "STORHHBT":  storhhbt,
        "DAYH2STOR": dayh2stor,
        # Storage power rates [TW]
        "BATDISCH":  bat_power  / 1e6,
        "FCDISCH":   h2_fc      / 1e6,
        "FCCHARG":   v(model.h2_chg_mw) / 1e6,
        "HBTDISCH":  hbat_power / 1e6,
    }


# ── Full LP solution extraction ──────────────────────────────────────────────

# Non-leap-year cumulative hour boundaries for each month (Jan=0 … Dec=11)
_MONTH_BOUNDS = [0, 744, 1416, 2160, 2880, 3624, 4344, 5088, 5832, 6552, 7296, 8016, 8760]
_MONTH_NAMES  = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                 "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def collect_lp_solution(
    model: pyo.ConcreteModel,
    inputs: Dict,
) -> Tuple[Dict, List[Dict]]:
    """Extract the full LP solution for post-hoc analysis.

    Returns
    -------
    solution : dict
        JSON-serialisable summary: LP proxy cost (B$/yr, by component),
        installed capacities (GW), storage energy (GWh), annual energy
        totals (TWh/yr), load-shed per sector, and exemplary-month metadata.
        NOTE: costs use Fortran-aligned CAPEX coefficients (DISCOUNTM=2%,
        tech-specific lifetimes from powerworld.f) and are comparable to
        Fortran annual cost output.
    dispatch_rows : list[dict]
        One dict per hour for the exemplary month, containing all dispatch
        variables and loads in MW / MWh.  Convert to a DataFrame or CSV
        in the caller.
    """
    v    = pyo.value
    meta = model._lp_meta
    n    = meta["n_hours"]
    T    = range(n)

    phs_power_mw   = meta["phs_power_mw"]
    avg_heat_mw    = meta["avg_heat_mw"]
    avg_cold_mw    = meta["avg_cold_mw"]
    utes_power_mw  = meta["utes_power_mw"]
    fixed_elec_mw  = meta["fixed_elec_mw"]
    fixed_heat_mw  = meta["fixed_heat_mw"]

    base   = inputs["base_capacities_mw"]
    detail = inputs.get("base_capacities_detail", {})
    res_base = detail.get("res_rooftop_pv", base["rooftop_pv"] * 0.6)
    com_base = detail.get("com_rooftop_pv", base["rooftop_pv"] * 0.4)

    elec_load = np.asarray(inputs["electric_load_mw"], dtype=float)
    heat_load = np.asarray(inputs["heat_load_mw"],     dtype=float)
    cold_load = np.asarray(inputs["cold_load_mw"],     dtype=float)

    # ── Solved scalar values ─────────────────────────────────────────────────
    faconwin  = v(model.faconwin)
    facoffwin = v(model.facoffwin)
    facutilpv = v(model.facutilpv)
    facrespv  = v(model.facrespv)
    faccompv  = v(model.faccompv)
    csptf     = v(model.cspturbfac)
    facsht    = v(model.facsht)
    bat_pow   = v(model.bat_power_mw)
    bat_nrg   = v(model.bat_energy_mwh)
    h2_fc     = v(model.h2_fc_mw)
    h2_chg    = v(model.h2_chg_mw)
    h2_nrg    = v(model.h2_seasonal_mwh)
    hbat_pow  = v(model.hbat_power_mw)
    hbat_nrg  = v(model.hbat_energy_mwh)

    # ── Installed capacities (GW) ────────────────────────────────────────────
    MW2GW  = 1e-3
    MWH2GWH = 1e-3
    MWH2TWH = 1e-6

    capacities_gw = {
        "onshore_wind":  round(faconwin  * base["onshore_wind"]  * MW2GW, 2),
        "offshore_wind": round(facoffwin * base["offshore_wind"] * MW2GW, 2),
        "res_pv":        round(facrespv  * res_base               * MW2GW, 2),
        "com_pv":        round(faccompv  * com_base               * MW2GW, 2),
        "utility_pv":    round(facutilpv * base["utility_pv"]    * MW2GW, 2),
        "csp":           round(csptf     * base["csp"]            * MW2GW, 2),
        "solar_thermal": round(facsht    * base["solar_thermal"]  * MW2GW, 2),
        "battery_power": round(bat_pow   * MW2GW, 2),
        "phs_power":     round(phs_power_mw * MW2GW, 2),
        "h2_fc_power":   round(h2_fc     * MW2GW, 2),
        "h2_chg_power":  round(h2_chg    * MW2GW, 2),
        "hbat_power":    round(hbat_pow  * MW2GW, 2),
    }

    storage_gwh = {
        "battery":   round(bat_nrg * MWH2GWH, 2),
        "phs":       round(phs_power_mw * v(model.storhphs) * MWH2GWH, 2),
        "h2":        round(h2_nrg * MWH2GWH, 2),
        "hw_stes":   round(avg_heat_mw * v(model.storhhwat) * MWH2GWH, 2),
        "cold_tes":  round(avg_cold_mw * v(model.storhcold) * MWH2GWH, 2),
        "utes":      round(utes_power_mw * v(model.storugdys) * HRSPDAY * MWH2GWH, 2),
        "heat_bat":  round(hbat_nrg * MWH2GWH, 2),
    }

    # ── LP proxy cost breakdown (B$/yr) ─────────────────────────────────────
    # Recompute each component from solved values (proxy CAPEX only, not Fortran costs)
    gen_capex_usd = (
        GEN_COST["onshore_wind"]  * base["onshore_wind"]  * faconwin
        + GEN_COST["offshore_wind"] * base["offshore_wind"] * facoffwin
        + GEN_COST["res_pv"]        * res_base               * facrespv
        + GEN_COST["com_pv"]        * com_base               * faccompv
        + GEN_COST["utility_pv"]    * base["utility_pv"]    * facutilpv
        + (GEN_COST["csp"] + CSP_PCM_PER_MW) * base["csp"]   * csptf
        + GEN_COST["solar_thermal"] * base["solar_thermal"]  * facsht
    )
    stor_capex_usd = (
        STOR_COST_POWER["battery"]   * bat_pow
        + STOR_COST_ENERGY["battery"]  * bat_nrg
        + STOR_COST_ENERGY["phs"]      * phs_power_mw * v(model.storhphs)
        + STOR_COST_POWER["h2_fc"]     * h2_fc
        + STOR_COST_POWER["h2_chg"]    * h2_chg
        + STOR_COST_ENERGY["h2"]       * h2_nrg
        + STOR_COST_POWER["heat_bat"]  * hbat_pow
        + STOR_COST_ENERGY["heat_bat"] * hbat_nrg
        + STOR_COST_ENERGY["hw_stes"]  * avg_heat_mw * v(model.storhhwat)
        + STOR_COST_ENERGY["cold_tes"] * avg_cold_mw * v(model.storhcold)
        + STOR_COST_ENERGY["utes"]     * utes_power_mw * v(model.storugdys) * HRSPDAY
    )
    shed_elec_arr = np.array([v(model.shed_elec[t])    for t in T])
    shed_heat_arr = np.array([v(model.shed_heat[t])    for t in T])
    shed_cold_arr = np.array([v(model.shed_cold[t])    for t in T])
    sur_elec_arr  = np.array([v(model.surplus_elec[t]) for t in T])
    sur_heat_arr  = np.array([v(model.surplus_heat[t]) for t in T])
    sur_cold_arr  = np.array([v(model.surplus_cold[t]) for t in T])

    shed_penalty_usd  = LOAD_SHED_PENALTY * float(shed_elec_arr.sum() + shed_heat_arr.sum() + shed_cold_arr.sum())
    curtail_usd       = CURTAIL_PENALTY   * float(sur_elec_arr.sum()  + sur_heat_arr.sum()  + sur_cold_arr.sum())
    total_obj_usd     = v(model.obj)

    USD2B = 1e-9
    cost_proxy = {
        "note":                  "Fortran-aligned annualised CAPEX (DISCOUNTM=2%, tech-specific lifetimes) — comparable to Fortran cost output",
        "total_B_usd_per_yr":    round(total_obj_usd    * USD2B, 4),
        "gen_capex_B_usd_per_yr":  round(gen_capex_usd  * USD2B, 4),
        "stor_capex_B_usd_per_yr": round(stor_capex_usd * USD2B, 4),
        "load_shed_B_usd_per_yr":  round(shed_penalty_usd * USD2B, 4),
        "curtailment_B_usd_per_yr":round(curtail_usd    * USD2B, 4),
    }

    # ── Annual energy totals (TWh/yr) ────────────────────────────────────────
    def _sum_twh(arr_or_var) -> float:
        if isinstance(arr_or_var, np.ndarray):
            return round(float(arr_or_var.sum()) * MWH2TWH, 3)
        return round(float(sum(v(arr_or_var[t]) for t in T)) * MWH2TWH, 3)

    annual_twh = {
        # Generation
        "gen_wind_on":   _sum_twh(np.array([v(model.gen_wind_on[t])  for t in T])),
        "gen_wind_off":  _sum_twh(np.array([v(model.gen_wind_off[t]) for t in T])),
        "gen_pv_res":    _sum_twh(np.array([v(model.gen_pv_res[t])   for t in T])),
        "gen_pv_com":    _sum_twh(np.array([v(model.gen_pv_com[t])   for t in T])),
        "gen_pv_util":   _sum_twh(np.array([v(model.gen_pv_util[t])  for t in T])),
        "gen_csp":       _sum_twh(np.array([v(model.gen_csp[t])      for t in T])),
        "gen_solth":     _sum_twh(np.array([v(model.gen_solth[t])    for t in T])),
        "fixed_elec":    round(fixed_elec_mw * n * MWH2TWH, 3),
        "fixed_heat":    round(fixed_heat_mw * n * MWH2TWH, 3),
        # Storage throughput (discharge only)
        "dis_bat":       _sum_twh(np.array([v(model.dis_bat[t])      for t in T])),
        "dis_phs":       _sum_twh(np.array([v(model.dis_phs[t])      for t in T])),
        "fuelcell":      _sum_twh(np.array([v(model.fuelcell[t])     for t in T])),
        "dis_hwstes":    _sum_twh(np.array([v(model.dis_hwstes[t])   for t in T])),
        "dis_hbat":      _sum_twh(np.array([v(model.dis_hbat[t])     for t in T])),
        "dis_utes":      _sum_twh(np.array([v(model.dis_utes[t])     for t in T])),
        "dis_cold":      _sum_twh(np.array([v(model.dis_cold[t])     for t in T])),
        # Demand
        "elec_load":     round(float(elec_load[:n].sum()) * MWH2TWH, 3),
        "heat_load":     round(float(heat_load[:n].sum()) * MWH2TWH, 3),
        "cold_load":     round(float(cold_load[:n].sum()) * MWH2TWH, 3),
        # Slack (load shedding = LP infeasibility proxy per sector)
        "shed_elec":     _sum_twh(shed_elec_arr),
        "shed_heat":     _sum_twh(shed_heat_arr),
        "shed_cold":     _sum_twh(shed_cold_arr),
        "surplus_elec":  _sum_twh(sur_elec_arr),
        "surplus_heat":  _sum_twh(sur_heat_arr),
        "surplus_cold":  _sum_twh(sur_cold_arr),
    }

    # ── Exemplary month (peak combined demand) ───────────────────────────────
    total_load = elec_load[:n] + heat_load[:n] + cold_load[:n]
    month_avgs = []
    for mi in range(12):
        s = min(_MONTH_BOUNDS[mi],     n)
        e = min(_MONTH_BOUNDS[mi + 1], n)
        month_avgs.append(float(total_load[s:e].mean()) if e > s else 0.0)
    peak_mi  = int(np.argmax(month_avgs))
    m_start  = min(_MONTH_BOUNDS[peak_mi],     n)
    m_end    = min(_MONTH_BOUNDS[peak_mi + 1], n)

    exemplary_month = {
        "month_name":      _MONTH_NAMES[peak_mi],
        "month_index":     peak_mi,
        "start_hour":      m_start,
        "end_hour":        m_end,
        "n_hours":         m_end - m_start,
        "selection_basis": "highest average combined (elec+heat+cold) demand",
    }

    # ── Dispatch rows for the exemplary month ────────────────────────────────
    dispatch_rows: List[Dict] = []
    for t in range(m_start, m_end):
        dispatch_rows.append({
            "hour":             t,
            "elec_load_mw":     float(elec_load[t]),
            "heat_load_mw":     float(heat_load[t]),
            "cold_load_mw":     float(cold_load[t]),
            "fixed_elec_mw":    fixed_elec_mw,
            "fixed_heat_mw":    fixed_heat_mw,
            # Generation
            "gen_wind_on_mw":   v(model.gen_wind_on[t]),
            "gen_wind_off_mw":  v(model.gen_wind_off[t]),
            "gen_pv_res_mw":    v(model.gen_pv_res[t]),
            "gen_pv_com_mw":    v(model.gen_pv_com[t]),
            "gen_pv_util_mw":   v(model.gen_pv_util[t]),
            "gen_csp_mw":       v(model.gen_csp[t]),
            "gen_solth_mw":     v(model.gen_solth[t]),
            # Electric storage
            "chg_bat_mw":       v(model.chg_bat[t]),
            "dis_bat_mw":       v(model.dis_bat[t]),
            "soc_bat_mwh":      v(model.soc_bat[t]),
            "chg_phs_mw":       v(model.chg_phs[t]),
            "dis_phs_mw":       v(model.dis_phs[t]),
            "soc_phs_mwh":      v(model.soc_phs[t]),
            # H2
            "electrolyser_mw":  v(model.electrolyser[t]),
            "fuelcell_mw":      v(model.fuelcell[t]),
            "soc_h2_mwh":       v(model.soc_h2[t]),
            # Heat
            "heat_pump_mw":     v(model.heat_pump[t]),
            "chg_hwstes_mw":    v(model.chg_hwstes[t]),
            "dis_hwstes_mw":    v(model.dis_hwstes[t]),
            "soc_hwstes_mwh":   v(model.soc_hwstes[t]),
            "chg_hbat_mw":      v(model.chg_hbat[t]),
            "dis_hbat_mw":      v(model.dis_hbat[t]),
            "soc_hbat_mwh":     v(model.soc_hbat[t]),
            "chg_utes_mw":      v(model.chg_utes[t]),
            "dis_utes_mw":      v(model.dis_utes[t]),
            "soc_utes_mwh":     v(model.soc_utes[t]),
            # Cold
            "ac_mw":            v(model.ac[t]),
            "chg_cold_mw":      v(model.chg_cold[t]),
            "dis_cold_mw":      v(model.dis_cold[t]),
            "soc_cold_mwh":     v(model.soc_cold[t]),
            # Slack
            "shed_elec_mw":     v(model.shed_elec[t]),
            "surplus_elec_mw":  v(model.surplus_elec[t]),
            "shed_heat_mw":     v(model.shed_heat[t]),
            "surplus_heat_mw":  v(model.surplus_heat[t]),
            "shed_cold_mw":     v(model.shed_cold[t]),
            "surplus_cold_mw":  v(model.surplus_cold[t]),
        })

    solution = {
        "region":           inputs["region"],
        "n_hours_modelled": n,
        "cost_proxy":       cost_proxy,
        "capacities_gw":    capacities_gw,
        "storage_gwh":      storage_gwh,
        "annual_twh":       annual_twh,
        "exemplary_month":  exemplary_month,
    }

    return solution, dispatch_rows

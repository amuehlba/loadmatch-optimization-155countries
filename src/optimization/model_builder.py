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
  H2_EFF   = 0.60  round-trip H2 efficiency (electrolyser × fuel-cell)
"""

from typing import Dict
import numpy as np
import pyomo.environ as pyo


# ── Fixed physical / policy constants ──────────────────────────────────────
UGFAC    = 3.0         # UTES charge rate factor (fixed)
HWFAC    = 1.0         # hot-water STES charge rate factor (fixed)
PHSMIN   = 0.016       # TW — minimum PHS nameplate power (fixed)
HEAT_COP = 4.0         # CPERFORM (fixed)
COLD_COP = 3.0         # cold COP (fixed)
H2_RT_EFF = 0.60       # H2 round-trip efficiency (electrolyser → fuel cell)
BAT_EFF   = 0.95       # battery one-way efficiency
HRSPDAY   = 24.0
DAYSPY    = 365.0

# ── Cost proxies ($/MW-year) for LP objective ───────────────────────────────
# Relative magnitudes matter; absolute values do not affect the GA warm-start.
_CRF = 0.05 * (1.05**25) / ((1.05**25) - 1)   # capital recovery factor (5%, 25 yr)

def _capex(usd_per_mw):
    return usd_per_mw * _CRF

GEN_COST = {   # $/MW installed → annualised
    "onshore_wind":  _capex(1_350_000),
    "offshore_wind": _capex(3_800_000),
    "res_pv":        _capex(  900_000),
    "com_pv":        _capex(  900_000),
    "utility_pv":    _capex(1_100_000),
    "csp":           _capex(4_500_000),
    "solar_thermal": _capex(  800_000),
}

STOR_COST_POWER  = {   # $/MW power
    "battery":    _capex(  300_000),
    "h2_fc":      _capex(  800_000),
    "h2_chg":     _capex(  500_000),
    "heat_bat":   _capex(  200_000),
}
STOR_COST_ENERGY = {   # $/MWh energy
    "battery":    _capex(  150_000),
    "phs":        _capex(   20_000),
    "cold_tes":   _capex(   30_000),
    "hw_stes":    _capex(   15_000),
    "h2":         _capex(    8_000),
    "heat_bat":   _capex(   50_000),
    "utes":       _capex(    5_000),
}

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

    base    = inputs["base_capacities_mw"]        # dict tech → MW
    detail  = inputs.get("base_capacities_detail", {})
    avail   = inputs["availability"]              # dict tech → array[n]
    sol_av  = inputs["solar_thermal_availability"]  # array[n]
    fixed   = inputs.get("fixed_baseload_mw", {}) # dict tech → avg MW (constant dispatch)

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

    # ── Model ────────────────────────────────────────────────────────────────
    m = pyo.ConcreteModel(name=f"LoadMatchLP_{inputs['region']}")
    m.T = pyo.RangeSet(0, n - 1)

    # ════════════════════════════════════════════════════════════════════════
    # Decision variables — generation factors (→ PARAM_REGISTRY)
    # Installed capacity [MW] = factor × base_capacity_mw
    # ════════════════════════════════════════════════════════════════════════
    m.faconwin   = pyo.Var(within=pyo.NonNegativeReals, bounds=(0, 8))
    m.facoffwin  = pyo.Var(within=pyo.NonNegativeReals, bounds=(0, 8))
    m.facutilpv  = pyo.Var(within=pyo.NonNegativeReals, bounds=(0, 8))
    m.facrespv   = pyo.Var(within=pyo.NonNegativeReals, bounds=(0, 8))
    m.faccompv   = pyo.Var(within=pyo.NonNegativeReals, bounds=(0, 8))
    m.cspturbfac = pyo.Var(within=pyo.NonNegativeReals, bounds=(0, 12))
    m.facsht     = pyo.Var(within=pyo.NonNegativeReals, bounds=(0, 8))

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
    # H2 storage    →  STORHHFC [h] = h2_energy_mwh / h2_fc_mw  (post-solve)
    m.h2_fc_mw       = pyo.Var(within=pyo.NonNegativeReals, bounds=(0, 5e6))
    m.h2_chg_mw      = pyo.Var(within=pyo.NonNegativeReals, bounds=(0, 5e6))
    m.h2_energy_mwh  = pyo.Var(within=pyo.NonNegativeReals, bounds=(0, 5e8))

    # H2 seasonal  →  DAYH2STOR [days] = h2_seasonal_mwh / (annual_h2_proxy / 365)
    #                                    (post-solve)
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
        return m_.soc_phs[t] == prev + 0.90 * m_.chg_phs[t] - m_.dis_phs[t] / 0.90
    m.c_phs_soc = pyo.Constraint(m.T, rule=_phs_soc)

    # ════════════════════════════════════════════════════════════════════════
    # H2 storage
    # Charge power cap: h2_chg_mw (electrolyser)
    # Discharge power cap: h2_fc_mw (fuel cell)
    # Energy cap: max(h2_energy_mwh, h2_seasonal_mwh)
    # To keep LP linear: soc_h2 ≤ h2_energy_mwh  AND  soc_h2 ≤ h2_seasonal_mwh
    # would give min; instead use a single combined energy variable.
    # ════════════════════════════════════════════════════════════════════════
    m.c_h2_chg_pow   = pyo.Constraint(m.T, rule=lambda _m, t:
        _m.electrolyser[t] <= _m.h2_chg_mw)
    m.c_h2_dis_pow   = pyo.Constraint(m.T, rule=lambda _m, t:
        _m.fuelcell[t]     <= _m.h2_fc_mw)
    # Total H2 capacity = max of short-term (h2_energy_mwh) and seasonal (h2_seasonal_mwh)
    # LP: soc ≤ h2_energy_mwh + h2_seasonal_mwh (conservative upper bound)
    m.c_h2_soc_max   = pyo.Constraint(m.T, rule=lambda _m, t:
        _m.soc_h2[t] <= _m.h2_energy_mwh + _m.h2_seasonal_mwh)
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
        return m_.soc_hwstes[t] == prev + 0.95 * m_.chg_hwstes[t] - m_.dis_hwstes[t] / 0.95
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
        return m_.soc_hbat[t] == prev + 0.95 * m_.chg_hbat[t] - m_.dis_hbat[t] / 0.95
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
        return m_.soc_utes[t] == prev + 0.90 * m_.chg_utes[t] - m_.dis_utes[t] / 0.90
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
        return m_.soc_cold[t] == prev + 0.95 * m_.chg_cold[t] - m_.dis_cold[t] / 0.95
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
        + GEN_COST["csp"]           * base["csp"]            * m.cspturbfac
        + GEN_COST["solar_thermal"] * base["solar_thermal"]  * m.facsht
    )

    # Storage CAPEX (power + energy components)
    stor_capex = (
        STOR_COST_POWER["battery"]  * m.bat_power_mw
        + STOR_COST_ENERGY["battery"]  * m.bat_energy_mwh
        + STOR_COST_ENERGY["phs"]      * phs_power_mw * m.storhphs
        + STOR_COST_POWER["h2_fc"]     * m.h2_fc_mw
        + STOR_COST_POWER["h2_chg"]    * m.h2_chg_mw
        + STOR_COST_ENERGY["h2"]       * (m.h2_energy_mwh + m.h2_seasonal_mwh)
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
    storhhfc  = v(model.h2_energy_mwh)  / h2_fc
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
        "STORHHFC":  storhhfc,
        "STORHHBT":  storhhbt,
        "DAYH2STOR": dayh2stor,
        # Storage power rates [TW]
        "BATDISCH":  bat_power  / 1e6,
        "FCDISCH":   h2_fc      / 1e6,
        "FCCHARG":   v(model.h2_chg_mw) / 1e6,
        "HBTDISCH":  hbat_power / 1e6,
    }

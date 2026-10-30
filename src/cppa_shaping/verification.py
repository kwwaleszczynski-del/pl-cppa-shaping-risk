"""Verification suite: accounting identities (< 1e-9 PLN/MWh), battery energy balance, and a closed-form check.

Verifies:
1. Hourly PPA settlement accounting identity (`Seller_CfD_Payout + Spot_Revenue_at_Capture == Strike * E_settled + Profile_Gap - Floor_Clawback`)
   across all contract structures and negative-price clauses.
2. 4-term decomposition bridge (`Price Level + Shape + Volume + Cross-Term == Total Settlement Gap`) on the
   reference year and on every bootstrap path, and the hourly surplus minus deficit identity.
3. Battery energy balance of the dispatch rule and the split of the shaping cost into its three parts.
4. Closed-form capture price and shape term on a harmonic diurnal price-generation process.

These are consistency checks of the code: the identities hold by construction, so passing them shows the
implementation is correct, not that the market results are.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .contracts import (
    CONTRACT_STRUCTURES,
    NEGATIVE_CLAUSES,
    decompose_paths,
    decompose_ppa_settlement,
    settle_hourly_ppa,
)
from .market import CONTRACT_VOLUME_MWH, HOURS_PER_YEAR
from .portfolio import BESS_RTE, WIND_SHARES, dispatch_portfolio, shaping_metrics


def verify_sinusoidal_analytical_shape(
    p0: float = 400.0,
    amp_p: float = 200.0,
    alpha_g: float = 0.80,
    phase_rad: float = np.pi,
    strike: float = 380.0,
) -> dict[str, float]:
    """Verify recovery of the closed-form capture price and shape risk on a harmonic diurnal process.

    Since 8,760 = 365 * 24 is an exact multiple of the 24-hour cycle, discrete hourly sums match
    the continuous Fourier integrals identically:
        P_bar_truth = p0
        P_cap_truth = p0 + 0.5 * alpha_g * amp_p * cos(phase_rad)
        Shape_truth = 0.5 * alpha_g * amp_p * cos(phase_rad)
    """
    t = np.arange(HOURS_PER_YEAR, dtype=float)
    omega = 2.0 * np.pi / 24.0
    spot = p0 + amp_p * np.cos(omega * t)
    g0 = CONTRACT_VOLUME_MWH / HOURS_PER_YEAR
    gen = g0 * (1.0 + alpha_g * np.cos(omega * t - phase_rad))
    load = np.full(HOURS_PER_YEAR, g0, dtype=float)

    truth_p_bar = float(p0)
    truth_shape = float(0.5 * alpha_g * amp_p * np.cos(phase_rad))
    truth_p_cap = truth_p_bar + truth_shape

    dec = decompose_ppa_settlement(spot_pln_mwh=spot, gen_mwh=gen, load_mwh=load, strike_pln_mwh=strike)

    return {
        "truth_p_bar_pln_mwh": truth_p_bar,
        "est_p_bar_pln_mwh": dec.spot_mean_pln_mwh,
        "truth_p_cap_pln_mwh": truth_p_cap,
        "est_p_cap_pln_mwh": dec.p_capture_pln_mwh,
        "truth_shape_risk_pln_mwh": truth_shape,
        "est_shape_risk_pln_mwh": dec.shape_risk_pln_mwh,
        "shape_abs_error_pln_mwh": abs(dec.shape_risk_pln_mwh - truth_shape),
        "bridge_abs_error_pln_mwh": dec.bridge_error_pln_mwh,
    }


def verify_dispatch_balance(df_ref: pd.DataFrame, bess_hours: int = 2) -> dict[str, float]:
    """Battery energy balance and shaping-cost split of `dispatch_portfolio` on the reference year (all mixes)."""
    spot = df_ref["spot_pln_mwh"].to_numpy(dtype=float)
    pv = df_ref["pv_mwh"].to_numpy(dtype=float)
    wind = df_ref["wind_mwh"].to_numpy(dtype=float)
    load = df_ref["load_baseload_mw"].to_numpy(dtype=float)
    s = dispatch_portfolio(pv, wind, load, spot, WIND_SHARES, bess_hours)
    m = shaping_metrics(s, load, spot)

    eta = np.sqrt(BESS_RTE)
    soc_err = np.abs(s["charge"] * eta - s["discharge"] / eta - s["soc_end"])
    energy_err = np.abs(s["e_gen"] - s["e_delivered"] - s["curtailed"] - s["charge"] + s["discharge"])
    split_err = np.abs(m["shaping_cost"] - (m["profile_gap"] - m["bess_gain"] - m["curtail_gain"]))
    return {
        "max_soc_balance_error_mwh": float(soc_err.max()),
        "max_energy_balance_error_mwh": float(energy_err.max()),
        "max_shaping_split_error_pln_mwh": float(split_err.max()),
    }


def run_full_verification(
    df_ref: pd.DataFrame,
    boot: dict[str, np.ndarray],
    strike_pln_mwh: float = 380.0,
) -> dict:
    """Run all accounting identity and analytical verification checks and return structured report."""
    spot = df_ref["spot_pln_mwh"].to_numpy(dtype=float)
    pv = df_ref["pv_mwh"].to_numpy(dtype=float)
    wind = df_ref["wind_mwh"].to_numpy(dtype=float)

    # 1. Check all contract structures x negative-price clauses x mixes on reference year
    max_settlement_err = 0.0
    for w_share in (0.0, 0.5, 1.0):
        gen = (1.0 - w_share) * pv + w_share * wind
        for struct in CONTRACT_STRUCTURES:
            load_col = "load_baseload_mw" if struct == "pay_as_produced" else f"load_{struct}_mw"
            load = df_ref[load_col].to_numpy(dtype=float)
            for clause in NEGATIVE_CLAUSES:
                res = settle_hourly_ppa(spot, gen, load, strike_pln_mwh, struct, clause)
                max_settlement_err = max(max_settlement_err, res.accounting_error_pln_mwh)

    # 2. 4-term decomposition on every bootstrap path and the hourly surplus/deficit identity on the reference year
    max_path_bridge_err = 0.0
    max_hourly_res_err = 0.0
    for w_share in (0.0, 0.5, 1.0):
        for prof in ("baseload", "two_shift", "cold_storage"):
            load = df_ref[f"load_{prof}_mw"].to_numpy(dtype=float)
            gen_p = (1.0 - w_share) * boot["pv"] + w_share * boot["wind"]
            dec_p = decompose_paths(boot["spot"], gen_p, load, strike_pln_mwh)
            max_path_bridge_err = max(max_path_bridge_err, float(dec_p["bridge_error_pln_mwh"].max()))
            dec = decompose_ppa_settlement(spot, (1.0 - w_share) * pv + w_share * wind, load, strike_pln_mwh)
            max_hourly_res_err = max(max_hourly_res_err, dec.hourly_residual_error_pln_mwh, dec.bridge_error_pln_mwh)

    dispatch_check = verify_dispatch_balance(df_ref)
    sin_check = verify_sinusoidal_analytical_shape()
    worst = max(
        max_settlement_err,
        max_path_bridge_err,
        max_hourly_res_err,
        dispatch_check["max_shaping_split_error_pln_mwh"],
        sin_check["shape_abs_error_pln_mwh"],
    )

    return {
        "max_settlement_accounting_error_pln_mwh": max_settlement_err,
        "max_bootstrap_bridge_error_pln_mwh": max_path_bridge_err,
        "max_hourly_residual_error_pln_mwh": max_hourly_res_err,
        "dispatch_balance": dispatch_check,
        "all_identities_below_1e9": bool(worst < 1e-9),
        "sinusoidal_analytical_recovery": sin_check,
        "bootstrap_paths": int(boot["spot"].shape[1]),
    }

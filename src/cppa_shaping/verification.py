"""Verification suite: exact accounting identities (< 1e-9 PLN/MWh) and analytical covariance recovery.

Verifies:
1. Hourly PPA settlement accounting identity (`Seller_CfD_Payout + Spot_Revenue_at_Capture == Strike * E_settled + Profile_Gap - Floor_Clawback`)
   across all contract structures and negative-price clauses.
2. Exact 4-term decomposition bridge (`Price Level + Shape + Volume + Cross-Term == Total Settlement Gap`)
   across reference mixes and all 500 Monte Carlo years (< 1e-9 PLN/MWh).
3. Closed-form analytical recovery on sinusoidal diurnal price-generation processes and bivariate Gaussian
   multi-year volume-price covariance processes.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .contracts import (
    CONTRACT_STRUCTURES,
    NEGATIVE_CLAUSES,
    decompose_mc_paths,
    decompose_ppa_settlement,
    settle_hourly_ppa,
)
from .market import CONTRACT_VOLUME_MWH, HOURS_PER_YEAR


def verify_sinusoidal_analytical_shape(
    p0: float = 435.0,
    amp_p: float = 391.5,
    alpha_g: float = 0.80,
    phase_rad: float = np.pi,
    strike: float = 380.0,
) -> dict[str, float]:
    """Verify exact recovery of closed-form capture price and shape risk on a harmonic diurnal process.

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
    truth_price_level = truth_p_bar - strike

    dec = decompose_ppa_settlement(spot_pln_mwh=spot, gen_mwh=gen, load_mwh=load, strike_pln_mwh=strike)

    return {
        "truth_p_bar_pln_mwh": truth_p_bar,
        "est_p_bar_pln_mwh": dec.spot_mean_pln_mwh,
        "truth_p_cap_pln_mwh": truth_p_cap,
        "est_p_cap_pln_mwh": dec.p_capture_pln_mwh,
        "truth_shape_risk_pln_mwh": truth_shape,
        "est_shape_risk_pln_mwh": dec.shape_risk_pln_mwh,
        "truth_price_level_pln_mwh": truth_price_level,
        "est_price_level_pln_mwh": dec.price_level_pln_mwh,
        "shape_abs_error_pln_mwh": abs(dec.shape_risk_pln_mwh - truth_shape),
        "bridge_abs_error_pln_mwh": dec.bridge_error_pln_mwh,
    }


def verify_gaussian_covariance_recovery(
    sigma_vol: float = 0.10,
    sigma_cap: float = 45.0,
    rho: float = -0.60,
    p_bar_0: float = 435.0,
    shape_0: float = -62.0,
    strike: float = 380.0,
    n_samples: int = 2000,
    seed: int = 123,
) -> dict[str, float]:
    """Verify exact recovery of multi-year volume-price covariance Cov(E_gen/E_c, P_cap - P_bar).

    Constructs standardized bivariate normal draws with exact first and second sample moments
    matching the analytical covariance `truth_cov = rho * sigma_vol * sigma_cap`.
    """
    rng = np.random.default_rng(seed)
    z = rng.normal(0.0, 1.0, size=(n_samples, 2))
    # Whiten to exact zero mean and identity covariance so analytical vs empirical moments match to < 1e-12
    z = z - np.mean(z, axis=0, keepdims=True)
    cov_z = (z.T @ z) / n_samples
    L_inv = np.linalg.inv(np.linalg.cholesky(cov_z))
    z_white = z @ L_inv.T

    target_corr = np.array([[1.0, rho], [rho, 1.0]], dtype=float)
    L_target = np.linalg.cholesky(target_corr)
    u = z_white @ L_target.T

    vol_rel = sigma_vol * u[:, 0]
    shape_shock = sigma_cap * u[:, 1]

    e_gen = CONTRACT_VOLUME_MWH * (1.0 + vol_rel)
    p_bar = np.full(n_samples, p_bar_0, dtype=float)
    p_cap = p_bar + shape_0 + shape_shock

    # Apply the 4-term decomposition formula across all N synthetic years
    t1_price = p_bar - strike
    t2_shape = p_cap - p_bar
    t3_vol = vol_rel * p_bar
    t4_cross = vol_rel * (p_cap - p_bar)
    total_4term = t1_price + t2_shape + t3_vol + t4_cross
    exact_gap = (e_gen * p_cap - CONTRACT_VOLUME_MWH * strike) / CONTRACT_VOLUME_MWH

    truth_cov_pln_mwh = float(rho * sigma_vol * sigma_cap)
    est_cov_pln_mwh = float(np.mean(t4_cross))
    max_yearly_bridge_err = float(np.max(np.abs(total_4term - exact_gap)))

    return {
        "truth_cross_cov_pln_mwh": truth_cov_pln_mwh,
        "est_cross_cov_pln_mwh": est_cov_pln_mwh,
        "cov_abs_error_pln_mwh": abs(est_cov_pln_mwh - truth_cov_pln_mwh),
        "truth_expected_shape_pln_mwh": float(shape_0),
        "est_expected_shape_pln_mwh": float(np.mean(t2_shape)),
        "truth_expected_total_gap_pln_mwh": float((p_bar_0 - strike) + shape_0 + truth_cov_pln_mwh),
        "est_expected_total_gap_pln_mwh": float(np.mean(total_4term)),
        "max_yearly_bridge_error_pln_mwh": max_yearly_bridge_err,
    }


def run_full_verification(df_ref: pd.DataFrame, mc_df: pd.DataFrame, strike_pln_mwh: float = 380.0) -> dict:
    """Run all accounting identity and analytical verification checks and return structured report."""
    spot = df_ref["spot_pln_mwh"].to_numpy(dtype=float)
    pv = df_ref["pv_mwh"].to_numpy(dtype=float)
    wind = df_ref["wind_mwh"].to_numpy(dtype=float)
    neg_6h = df_ref["is_neg_6h_run"].to_numpy(dtype=bool)

    # 1. Check all contract structures x negative-price clauses x mixes on reference year
    max_settlement_err = 0.0
    settlement_checks = []
    for w_share, mix_name in [(0.0, "pure_pv"), (0.65, "hybrid_65w_35pv"), (1.0, "pure_wind")]:
        gen = (1.0 - w_share) * pv + w_share * wind
        for struct in CONTRACT_STRUCTURES:
            load_col = "load_baseload_mw" if struct == "pay_as_produced" else f"load_{struct}_mw"
            load = df_ref[load_col].to_numpy(dtype=float)
            for clause in NEGATIVE_CLAUSES:
                res = settle_hourly_ppa(
                    spot_pln_mwh=spot,
                    gen_mwh=gen,
                    load_mwh=load,
                    strike_pln_mwh=strike_pln_mwh,
                    structure=struct,
                    clause=clause,
                    is_neg_6h_run=neg_6h,
                )
                max_settlement_err = max(max_settlement_err, res.accounting_error_pln_mwh)
                settlement_checks.append(
                    {
                        "mix": mix_name,
                        "structure": struct,
                        "clause": clause,
                        "seller_effective_pln_mwh": round(res.seller_effective_pln_mwh, 4),
                        "floor_clawback_pln_mwh": round(res.floor_clawback_pln / res.e_settled_mwh, 4),
                        "accounting_error_pln_mwh": res.accounting_error_pln_mwh,
                    }
                )

    # 2. Check 4-term decomposition across all 500 Monte Carlo years and mixes
    max_mc_bridge_err = 0.0
    for w_share in (0.0, 0.35, 0.50, 0.65, 1.0):
        for prof in ("baseload", "two_shift", "cold_storage"):
            dec_mc = decompose_mc_paths(
                mc_df=mc_df,
                wind_share=w_share,
                strike_pln_mwh=strike_pln_mwh,
                buyer_profile=prof,
            )
            max_mc_bridge_err = max(max_mc_bridge_err, float(dec_mc["bridge_error_pln_mwh"].max()))

    # 3. Check hourly surplus/deficit identity on reference year
    max_hourly_res_err = 0.0
    for w_share in (0.0, 0.65, 1.0):
        gen = (1.0 - w_share) * pv + w_share * wind
        for prof in ("baseload", "two_shift", "cold_storage"):
            load = df_ref[f"load_{prof}_mw"].to_numpy(dtype=float)
            dec = decompose_ppa_settlement(spot_pln_mwh=spot, gen_mwh=gen, load_mwh=load, strike_pln_mwh=strike_pln_mwh)
            max_hourly_res_err = max(max_hourly_res_err, dec.hourly_residual_error_pln_mwh, dec.bridge_error_pln_mwh)

    sin_check = verify_sinusoidal_analytical_shape()
    gauss_check = verify_gaussian_covariance_recovery()

    return {
        "max_settlement_accounting_error_pln_mwh": max_settlement_err,
        "max_mc_500yr_bridge_error_pln_mwh": max_mc_bridge_err,
        "max_hourly_residual_error_pln_mwh": max_hourly_res_err,
        "all_identities_below_1e9": bool(
            max(max_settlement_err, max_mc_bridge_err, max_hourly_res_err) < 1e-9
        ),
        "sinusoidal_analytical_recovery": sin_check,
        "gaussian_covariance_recovery": gauss_check,
        "sample_settlement_checks": settlement_checks[:9],
    }

"""Comprehensive test suite for pl-cppa-shaping-risk (no network, deterministic, fast)."""

from __future__ import annotations

from pathlib import Path
import numpy as np
import pytest

from cppa_shaping.contracts import (
    CONTRACT_STRUCTURES,
    NEGATIVE_CLAUSES,
    compute_strike_uplift,
    decompose_mc_paths,
    decompose_ppa_settlement,
    evaluate_negative_price_clauses,
    settle_hourly_ppa,
)
from cppa_shaping.market import (
    BUYER_PROFILES,
    CONTRACT_VOLUME_MWH,
    HOURS_PER_YEAR,
    detect_consecutive_negative_hours,
    generate_polish_market_year,
    run_block_bootstrap_mc,
)
from cppa_shaping.pipeline import run_full_pipeline
from cppa_shaping.portfolio import dispatch_colocated_bess, run_portfolio_sweep
from cppa_shaping.verification import (
    run_full_verification,
    verify_gaussian_covariance_recovery,
    verify_sinusoidal_analytical_shape,
)


@pytest.fixture(scope="module")
def ref_market():
    return generate_polish_market_year(seed=2026)


@pytest.fixture(scope="module")
def mc_paths(ref_market):
    return run_block_bootstrap_mc(ref_market, n_paths=500, seed=42)


@pytest.fixture(scope="module")
def portfolio_sweep(ref_market, mc_paths):
    return run_portfolio_sweep(ref_market, mc_paths, strike_pln_mwh=380.0)


@pytest.fixture(scope="module")
def neg_clauses_table(ref_market):
    return evaluate_negative_price_clauses(ref_market, base_strike_pln_mwh=380.0)


def test_market_dimensions_and_spot_mean(ref_market):
    assert len(ref_market) == HOURS_PER_YEAR
    spot_mean = float(ref_market["spot_pln_mwh"].mean())
    assert abs(spot_mean - 435.0) < 0.05


def test_calibrated_capture_factors_and_negative_hours(ref_market):
    spot = ref_market["spot_pln_mwh"].to_numpy()
    pv = ref_market["pv_mwh"].to_numpy()
    wind = ref_market["wind_mwh"].to_numpy()
    spot_mean = float(np.mean(spot))

    cf_pv = float(np.sum(pv * spot) / np.sum(pv) / spot_mean)
    cf_wind = float(np.sum(wind * spot) / np.sum(wind) / spot_mean)
    neg_hours = int(np.sum(spot <= 0.0))

    assert abs(cf_pv - 0.64) < 1e-3
    assert abs(cf_wind - 0.94) < 1e-3
    assert neg_hours == 354


def test_three_industrial_buyer_load_profiles(ref_market):
    for code in BUYER_PROFILES:
        col = f"load_{code}_mw"
        assert col in ref_market.columns
        tot_mwh = float(ref_market[col].sum())
        assert abs(tot_mwh - CONTRACT_VOLUME_MWH) < 1e-6
        assert float(ref_market[col].min()) > 0.0

    # Baseload is constant 11.4155 MW
    assert np.allclose(ref_market["load_baseload_mw"], CONTRACT_VOLUME_MWH / HOURS_PER_YEAR)
    # Two-shift has peak around ~18.5 MW and base 3.0 MW
    assert 17.5 <= float(ref_market["load_two_shift_mw"].max()) <= 19.5
    assert 2.8 <= float(ref_market["load_two_shift_mw"].min()) <= 3.2


def test_consecutive_negative_hour_detector():
    p = np.array([10.0, -5.0, -2.0, -1.0, 20.0, -4.0, -8.0, -12.0, -3.0, -1.0, -6.0, 15.0])
    m6 = detect_consecutive_negative_hours(p, min_consecutive_hours=6)
    assert m6.tolist() == [False, False, False, False, False, True, True, True, True, True, True, False]


@pytest.mark.parametrize("structure", CONTRACT_STRUCTURES)
@pytest.mark.parametrize("clause", NEGATIVE_CLAUSES)
def test_hourly_ppa_accounting_identity_all_structures_and_clauses(ref_market, structure, clause):
    spot = ref_market["spot_pln_mwh"].to_numpy()
    gen = 0.35 * ref_market["pv_mwh"].to_numpy() + 0.65 * ref_market["wind_mwh"].to_numpy()
    load_col = "load_baseload_mw" if structure == "pay_as_produced" else f"load_{structure}_mw"
    load = ref_market[load_col].to_numpy()
    neg_6h = ref_market["is_neg_6h_run"].to_numpy()

    res = settle_hourly_ppa(
        spot_pln_mwh=spot,
        gen_mwh=gen,
        load_mwh=load,
        strike_pln_mwh=380.0,
        structure=structure,
        clause=clause,
        is_neg_6h_run=neg_6h,
    )
    assert res.accounting_error_pln_mwh < 1e-9

    if structure == "pay_as_produced":
        # Direct check of: Seller_CfD_Payout + Spot_Revenue_at_Capture == Strike * E_settled - Floor_Clawback
        lhs = res.seller_cfd_payout_pln + res.spot_revenue_pln
        rhs = res.strike_pln_mwh * res.e_settled_mwh - res.floor_clawback_pln
        assert abs(lhs - rhs) / res.e_settled_mwh < 1e-9


def test_four_term_decomposition_bridge_exact_identity(ref_market):
    spot = ref_market["spot_pln_mwh"].to_numpy()
    for w_share in (0.0, 0.35, 0.65, 1.0):
        gen = (1.0 - w_share) * ref_market["pv_mwh"].to_numpy() + w_share * ref_market["wind_mwh"].to_numpy()
        for prof in ("baseload", "two_shift", "cold_storage"):
            load = ref_market[f"load_{prof}_mw"].to_numpy()
            dec = decompose_ppa_settlement(spot_pln_mwh=spot, gen_mwh=gen, load_mwh=load, strike_pln_mwh=380.0)
            assert dec.bridge_error_pln_mwh < 1e-9
            assert dec.hourly_residual_error_pln_mwh < 1e-9


def test_mc_500_paths_four_term_bridge_and_negative_covariance(mc_paths):
    assert len(mc_paths) == 500
    for w_share in (0.0, 0.5, 0.65, 1.0):
        dec_mc = decompose_mc_paths(mc_paths, wind_share=w_share, strike_pln_mwh=380.0, buyer_profile="baseload")
        assert float(dec_mc["bridge_error_pln_mwh"].max()) < 1e-9
        # Verify negative weather-price covariance (low output years coincide with higher prices -> negative cross term)
        assert float(dec_mc["cross_term_pln_mwh"].mean()) < 0.0


def test_negative_price_clause_strike_uplift_ranges(neg_clauses_table):
    # Pure PV uplift should lie in [+28, +44.5] PLN/MWh across 6-hour rule and zero-price floor scenarios
    pv_rows = neg_clauses_table[
        (neg_clauses_table["mix_code"] == "pure_pv") & (neg_clauses_table["clause_code"] != "unfloored")
    ]
    pv_uplifts = pv_rows["required_strike_uplift_pln_mwh"].to_numpy()
    assert float(np.min(pv_uplifts)) >= 27.5
    assert float(np.max(pv_uplifts)) <= 44.5

    # Pure Wind uplift should lie in [+8.8, +14.5] PLN/MWh across 6-hour rule and zero-price floor scenarios
    wind_rows = neg_clauses_table[
        (neg_clauses_table["mix_code"] == "pure_wind") & (neg_clauses_table["clause_code"] != "unfloored")
    ]
    wind_uplifts = wind_rows["required_strike_uplift_pln_mwh"].to_numpy()
    assert float(np.min(wind_uplifts)) >= 8.8
    assert float(np.max(wind_uplifts)) <= 14.5


def test_portfolio_sizing_shaping_cost_and_24_7_cfe_targets(portfolio_sweep):
    base = portfolio_sweep[portfolio_sweep["buyer_profile"] == "baseload"]

    pv_0h = base[(base["wind_share"] == 0.0) & (base["bess_hours"] == 0)].iloc[0]
    hyb_0h = base[(base["wind_share"] == 0.65) & (base["bess_hours"] == 0)].iloc[0]
    hyb_2h = base[(base["wind_share"] == 0.65) & (base["bess_hours"] == 2)].iloc[0]
    hyb_4h = base[(base["wind_share"] == 0.65) & (base["bess_hours"] == 4)].iloc[0]

    # Shaping cost reduction: 142 PLN/MWh (pure PV) -> 31 PLN/MWh (65/35 hybrid) -> 11 PLN/MWh (+ 2h BESS)
    assert abs(float(pv_0h["shaping_cost_pln_mwh"]) - 142.0) < 0.5
    assert abs(float(hyb_0h["shaping_cost_pln_mwh"]) - 31.0) < 0.5
    assert abs(float(hyb_2h["shaping_cost_pln_mwh"]) - 11.0) < 0.5
    assert float(hyb_4h["shaping_cost_pln_mwh"]) < float(hyb_2h["shaping_cost_pln_mwh"])

    # 24/7 Hourly CFE score lifted from 44% (pure PV) to 81% (65/35 hybrid + 2h BESS)
    assert abs(float(pv_0h["cfe_24_7_pct"]) - 44.0) < 0.5
    assert abs(float(hyb_2h["cfe_24_7_pct"]) - 81.0) < 0.5
    assert float(hyb_4h["cfe_24_7_pct"]) > float(hyb_2h["cfe_24_7_pct"])


def test_colocated_bess_energy_conservation_and_arbitrage(ref_market):
    spot = ref_market["spot_pln_mwh"].to_numpy()
    gen = 0.35 * ref_market["pv_mwh"].to_numpy() + 0.65 * ref_market["wind_mwh"].to_numpy()
    load = ref_market["load_baseload_mw"].to_numpy()

    s0 = dispatch_colocated_bess(gen, load, spot, bess_hours=0)
    s2 = dispatch_colocated_bess(gen, load, spot, bess_hours=2)

    assert float(s2["cfe_24_7_pct"]) > float(s0["cfe_24_7_pct"])
    assert float(s2["p_capture_eff_pln_mwh"]) > float(s0["p_capture_raw_pln_mwh"])
    assert float(s2["residual_deficit_mwh"]) < float(s0["residual_deficit_mwh"])


def test_analytical_sinusoidal_shape_recovery():
    res = verify_sinusoidal_analytical_shape()
    assert res["shape_abs_error_pln_mwh"] < 1e-9
    assert res["bridge_abs_error_pln_mwh"] < 1e-9
    assert abs(res["truth_shape_risk_pln_mwh"] - (-156.6)) < 1e-9


def test_analytical_gaussian_covariance_recovery():
    res = verify_gaussian_covariance_recovery()
    assert res["cov_abs_error_pln_mwh"] < 1e-9
    assert res["max_yearly_bridge_error_pln_mwh"] < 1e-9


def test_strike_uplift_formula_monotonicity():
    u0 = compute_strike_uplift(380.0, 0.0)
    u5 = compute_strike_uplift(380.0, 0.05)
    u10 = compute_strike_uplift(380.0, 0.10)
    assert u0 == 0.0
    assert 0.0 < u5 < u10
    assert abs(u10 - 380.0 * (0.10 / 0.90)) < 1e-12


def test_full_verification_suite(ref_market, mc_paths):
    ver = run_full_verification(ref_market, mc_paths, strike_pln_mwh=380.0)
    assert ver["all_identities_below_1e9"] is True
    assert ver["max_settlement_accounting_error_pln_mwh"] < 1e-9
    assert ver["max_mc_500yr_bridge_error_pln_mwh"] < 1e-9
    assert ver["max_hourly_residual_error_pln_mwh"] < 1e-9


def test_pipeline_outputs_exist(tmp_path: Path):
    payload = run_full_pipeline(output_root=tmp_path, strike_pln_mwh=380.0)
    assert (tmp_path / "results" / "portfolio_sweep.csv").exists()
    assert (tmp_path / "results" / "negative_price_clauses.csv").exists()
    assert (tmp_path / "results" / "mc_bootstrap_500yr.csv").exists()
    assert (tmp_path / "results" / "verification.json").exists()
    assert (tmp_path / "docs" / ".nojekyll").exists()
    assert (tmp_path / "docs" / "data.js").exists()
    for img_name in (
        "shaping_cost_mix.png",
        "settlement_waterfall.png",
        "negative_price_clauses.png",
        "cashflow_distribution.png",
    ):
        assert (tmp_path / "docs" / "img" / img_name).exists()
    assert payload["verification"]["all_identities_below_1e9"] is True

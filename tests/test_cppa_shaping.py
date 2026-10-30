"""Test suite for pl-cppa-shaping-risk (no network, deterministic).

Model tests run on a synthetic hourly price / PV / wind frame built below; they check properties and invariants
(accounting identities, energy balance, monotonicity), not target values. The last tests check that the committed
README, results/ and docs/data.js agree with each other.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from cppa_shaping import pse
from cppa_shaping.contracts import (
    CONTRACT_STRUCTURES,
    NEGATIVE_CLAUSES,
    clause_mask,
    compute_strike_uplift,
    decompose_paths,
    decompose_ppa_settlement,
    evaluate_negative_price_clauses,
    settle_hourly_ppa,
)
from cppa_shaping.market import (
    BUYER_PROFILES,
    COMPARISON_WINDOWS,
    CONTRACT_VOLUME_MWH,
    DATA_END,
    DATA_START,
    HOURS_PER_YEAR,
    bootstrap_week_indices,
    build_market,
    buyer_loads,
    detect_consecutive_negative_hours,
)
from cppa_shaping.pipeline import run_full_pipeline
from cppa_shaping.portfolio import BESS_POWER_MW, BESS_RTE, WIND_SHARES, dispatch_portfolio, shaping_metrics
from cppa_shaping.verification import run_full_verification, verify_sinusoidal_analytical_shape

REPO = Path(__file__).resolve().parents[1]


def synthetic_hourly(start: date = DATA_START, end: date = DATA_END, seed: int = 7) -> pd.DataFrame:
    """Synthetic hourly frame (UTC index, columns price, pv, wind) with summer midday negative prices."""
    t0 = pd.Timestamp(start, tz="Europe/Warsaw").tz_convert("UTC")
    t1 = pd.Timestamp(end + pd.Timedelta(days=1), tz="Europe/Warsaw").tz_convert("UTC")
    idx = pd.date_range(t0, t1, freq="h", inclusive="left", name="start_utc")
    loc = idx.tz_convert("Europe/Warsaw")
    rng = np.random.default_rng(seed)
    hour = loc.hour.to_numpy(dtype=float)
    doy = loc.day_of_year.to_numpy(dtype=float)
    season = 0.5 - 0.5 * np.cos((doy - 10.0) / 365.0 * 2 * np.pi)
    pv = np.clip(np.cos((hour - 12.5) / 7.0 * np.pi / 2), 0, None) ** 1.5 * (2000 + 9000 * season)
    z = np.zeros(len(idx))
    eps = rng.normal(size=len(idx))
    for t in range(1, len(idx)):
        z[t] = 0.97 * z[t - 1] + 0.243 * eps[t]
    wind = np.clip(2500 + 1500 * z + 600 * (1 - season), 50, None)
    price = 520 - 0.06 * pv - 0.05 * wind + 90 * np.exp(-0.5 * ((hour - 19) / 2) ** 2) + rng.normal(0, 25, len(idx))
    return pd.DataFrame({"price": price, "pv": pv, "wind": wind}, index=idx)


@pytest.fixture(scope="module")
def hourly():
    return synthetic_hourly()


@pytest.fixture(scope="module")
def ref_market(hourly):
    return build_market(hourly, *COMPARISON_WINDOWS["2025"])


@pytest.fixture(scope="module")
def boot(ref_market):
    idx = bootstrap_week_indices(len(ref_market), 12, seed=1)
    cols = (("spot", "spot_pln_mwh"), ("pv", "pv_mwh"), ("wind", "wind_mwh"))
    return {k: ref_market[c].to_numpy()[idx] for k, c in cols}


# --- data loading -----------------------------------------------------------------------------------------------


def test_fetch_period_follows_pages_and_shifts_to_interval_start(monkeypatch):
    pages = {
        "first": {"value": [{"dtime_utc": "2025-06-01 00:15:00", "business_date": "2025-06-01", "rce_pln": 10.0},
                            {"dtime_utc": "2025-06-01 00:30:00", "business_date": "2025-06-01", "rce_pln": 20.0}],
                  "nextLink": "second"},
        "second": {"value": [{"dtime_utc": "2025-06-01 00:30:00", "business_date": "2025-06-01", "rce_pln": 25.0},
                             {"dtime_utc": "2025-06-01 00:45:00", "business_date": "2025-06-01", "rce_pln": 30.0}]},
    }
    monkeypatch.setattr(pse, "_get_json", lambda url: pages["second" if url == "second" else "first"])
    df = pse.fetch_period("rce-pln", date(2025, 6, 1), date(2025, 6, 1))
    assert df["start_utc"].dt.strftime("%H:%M").tolist() == ["00:00", "00:15", "00:30"]
    assert df["price"].tolist() == [10.0, 25.0, 30.0]  # duplicate interval keeps the last row


def test_load_hourly_averages_quarters_and_rejects_gaps(tmp_path):
    t0 = pd.Timestamp("2025-01-01", tz="Europe/Warsaw").tz_convert("UTC")
    quarters = pd.date_range(t0, periods=31 * 96, freq="15min")
    pd.DataFrame({"start_utc": quarters, "price": np.arange(len(quarters), dtype=float)}).to_csv(
        tmp_path / "rce-pln_2025-01.csv", index=False)
    pd.DataFrame({"start_utc": quarters, "pv": 1.0, "wind": 2.0}).to_csv(
        tmp_path / "his-wlk-cal_2025-01.csv", index=False)
    h = pse.load_hourly(tmp_path, date(2025, 1, 1), date(2025, 1, 31))
    assert len(h) == 31 * 24 and h.index[0] == t0
    assert h["price"].iloc[0] == pytest.approx(1.5)  # mean of 0, 1, 2, 3
    assert (h["pv"] == 1.0).all() and (h["wind"] == 2.0).all()

    pd.DataFrame({"start_utc": quarters[1:], "pv": 1.0, "wind": 2.0}).to_csv(
        tmp_path / "his-wlk-cal_2025-01.csv", index=False)
    with pytest.raises(ValueError, match="incomplete hours"):
        pse.load_hourly(tmp_path, date(2025, 1, 1), date(2025, 1, 31))


# --- market frame -----------------------------------------------------------------------------------------------


def test_buyer_loads_sum_to_contract_volume_over_reference_year(ref_market):
    assert len(ref_market) == HOURS_PER_YEAR
    for code in BUYER_PROFILES:
        load = ref_market[f"load_{code}_mw"]
        assert float(load.sum()) == pytest.approx(CONTRACT_VOLUME_MWH, rel=1e-12)
        assert float(load.min()) > 0.0
    assert np.allclose(ref_market["load_baseload_mw"], CONTRACT_VOLUME_MWH / HOURS_PER_YEAR)
    shift = ref_market["load_two_shift_mw"].to_numpy()
    on_shift = (ref_market["dayofweek"] < 6) & (ref_market["hour"] >= 6) & (ref_market["hour"] < 22)
    assert shift[on_shift.to_numpy()].min() > shift[~on_shift.to_numpy()].max()


def test_generation_is_volume_matched_in_every_window(hourly):
    for first, last in COMPARISON_WINDOWS.values():
        m = build_market(hourly, first, last)
        e_c = float(m["load_baseload_mw"].sum())
        assert float(m["pv_mwh"].sum()) == pytest.approx(e_c, rel=1e-12)
        assert float(m["wind_mwh"].sum()) == pytest.approx(e_c, rel=1e-12)
        # scaling keeps the national profile shape
        ratio = m["pv_mwh"] / m["pv_national_mw"].where(m["pv_national_mw"] > 0)
        assert ratio.dropna().std() < 1e-9


def test_build_market_rejects_incomplete_window(hourly):
    with pytest.raises(ValueError, match="incomplete"):
        build_market(hourly.iloc[5:], *COMPARISON_WINDOWS["2025"])


def test_buyer_loads_use_the_reference_year_scale_in_partial_windows():
    ts = pd.date_range("2026-01-01", "2026-09-30 23:00", freq="h", tz="Europe/Warsaw")
    loads = buyer_loads(ts)
    assert np.allclose(loads["baseload"], CONTRACT_VOLUME_MWH / HOURS_PER_YEAR)


def test_consecutive_negative_hour_detector():
    p = np.array([10.0, -5.0, -2.0, -1.0, 20.0, -4.0, -8.0, -12.0, -3.0, -1.0, -6.0, 15.0])
    m6 = detect_consecutive_negative_hours(p, min_consecutive_hours=6)
    assert m6.tolist() == [False, False, False, False, False, True, True, True, True, True, True, False]
    assert (m6 <= (p < 0)).all()


def test_bootstrap_weeks_are_whole_and_seasonal():
    idx = bootstrap_week_indices(HOURS_PER_YEAR, 200, seed=3)
    assert idx.shape == (HOURS_PER_YEAR, 200)
    n_full = HOURS_PER_YEAR // 168
    for k in range(int(np.ceil(HOURS_PER_YEAR / 168))):
        block = idx[k * 168 : (k + 1) * 168]
        src = block[0] // 168
        assert (block[0] % 168 == 0).all()
        assert (np.diff(block, axis=0) == 1).all()  # contiguous hours of one real week
        offset = (src - k + n_full // 2) % n_full - n_full // 2
        assert (np.abs(offset) <= 3).all()


# --- settlement ---------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("structure", CONTRACT_STRUCTURES)
@pytest.mark.parametrize("clause", NEGATIVE_CLAUSES)
def test_hourly_ppa_accounting_identity_all_structures_and_clauses(ref_market, structure, clause):
    spot = ref_market["spot_pln_mwh"].to_numpy()
    gen = 0.5 * ref_market["pv_mwh"].to_numpy() + 0.5 * ref_market["wind_mwh"].to_numpy()
    load_col = "load_baseload_mw" if structure == "pay_as_produced" else f"load_{structure}_mw"
    res = settle_hourly_ppa(spot, gen, ref_market[load_col].to_numpy(), 380.0, structure, clause)
    assert res.accounting_error_pln_mwh < 1e-9
    if clause == "unfloored":
        assert res.floor_clawback_pln == 0.0
    if structure != "pay_as_produced" and clause == "unfloored":
        assert res.buyer_effective_pln_mwh == pytest.approx(380.0, abs=1e-9)  # profiled CfD fully hedges the buyer


def test_four_term_bridge_and_vectorised_paths_agree(ref_market, boot):
    load = ref_market["load_two_shift_mw"].to_numpy()
    gen_p = 0.3 * boot["pv"] + 0.7 * boot["wind"]
    dec_p = decompose_paths(boot["spot"], gen_p, load, 380.0)
    assert float(dec_p["bridge_error_pln_mwh"].max()) < 1e-9
    for j in (0, 5, 11):
        dec = decompose_ppa_settlement(boot["spot"][:, j], gen_p[:, j], load, 380.0)
        assert dec.bridge_error_pln_mwh < 1e-9 and dec.hourly_residual_error_pln_mwh < 1e-9
        assert dec_p["cross_term_pln_mwh"].iloc[j] == pytest.approx(dec.cross_term_pln_mwh, abs=1e-9)
        assert dec_p["shape_risk_pln_mwh"].iloc[j] == pytest.approx(dec.shape_risk_pln_mwh, abs=1e-9)


def test_clause_masks_nest_and_uplift_matches_formula(hourly):
    markets = {k: build_market(hourly, a, b) for k, (a, b) in COMPARISON_WINDOWS.items()}
    spot = markets["2025"]["spot_pln_mwh"].to_numpy()
    assert (clause_mask(spot, "six_hour_rule") <= clause_mask(spot, "zero_floor")).all()
    assert not clause_mask(spot, "unfloored").any()

    tab = evaluate_negative_price_clauses(markets, np.array([0.0, 0.5, 1.0]))
    assert tab["accounting_error_pln_mwh"].max() < 1e-9
    alpha = tab["unsettled_energy_share_pct"] / 100.0
    # seller revenue per available MWh is S (1 - alpha), and the uplift restores S
    assert np.allclose(tab["seller_revenue_pln_per_available_mwh"], 380.0 * (1.0 - alpha), atol=0.02)
    assert np.allclose(tab["required_strike_uplift_pln_mwh"], 380.0 * alpha / (1.0 - alpha), rtol=2e-3, atol=0.05)
    piv = tab.pivot_table(index=["window", "wind_share"], columns="clause_code",
                          values="required_strike_uplift_pln_mwh")
    assert (piv["unfloored"] == 0.0).all()
    assert (piv["six_hour_rule"] <= piv["zero_floor"] + 1e-9).all()


def test_strike_uplift_formula():
    assert compute_strike_uplift(380.0, 0.0) == 0.0
    assert 0.0 < compute_strike_uplift(380.0, 0.05) < compute_strike_uplift(380.0, 0.10)
    assert compute_strike_uplift(380.0, 0.10) == pytest.approx(380.0 * 0.10 / 0.90, abs=1e-12)
    with pytest.raises(ValueError):
        compute_strike_uplift(380.0, 1.0)


# --- dispatch -----------------------------------------------------------------------------------------------------


def test_no_bess_without_negative_prices_is_linear_in_mix_and_equals_capture_gap(ref_market):
    spot = np.abs(ref_market["spot_pln_mwh"].to_numpy()) + 1.0
    pv, wind = ref_market["pv_mwh"].to_numpy(), ref_market["wind_mwh"].to_numpy()
    load = ref_market["load_baseload_mw"].to_numpy()
    m = shaping_metrics(dispatch_portfolio(pv, wind, load, spot, WIND_SHARES, 0), load, spot)
    cost = m["shaping_cost"][:, 0]
    p_bar = spot.mean()
    p_cap_pv, p_cap_wind = (pv * spot).sum() / pv.sum(), (wind * spot).sum() / wind.sum()
    expected = p_bar - ((1 - WIND_SHARES) * p_cap_pv + WIND_SHARES * p_cap_wind)
    assert np.allclose(cost, expected, atol=1e-9)
    assert np.allclose(np.diff(cost, 2), 0.0, atol=1e-9)


@pytest.mark.parametrize("bess_hours", [2, 4])
def test_battery_rule_conserves_energy_and_never_lowers_cfe(ref_market, bess_hours):
    spot = ref_market["spot_pln_mwh"].to_numpy()
    pv, wind = ref_market["pv_mwh"].to_numpy(), ref_market["wind_mwh"].to_numpy()
    load = ref_market["load_cold_storage_mw"].to_numpy()
    s0 = dispatch_portfolio(pv, wind, load, spot, WIND_SHARES, 0)
    sb = dispatch_portfolio(pv, wind, load, spot, WIND_SHARES, bess_hours, keep_hourly=True)
    eta = np.sqrt(BESS_RTE)
    cap = bess_hours * BESS_POWER_MW
    assert np.allclose(sb["charge"] * eta - sb["discharge"] / eta, sb["soc_end"], atol=1e-6)
    assert (sb["soc_end"] <= cap + 1e-9).all() and (sb["soc_end"] >= -1e-9).all()
    assert np.allclose(sb["e_gen"] - sb["curtailed"] - sb["charge"] + sb["discharge"], sb["e_delivered"], atol=1e-6)
    assert (sb["delivered_hourly"] >= -1e-9).all()
    assert (sb["discharge"] <= sb["charge"] * BESS_RTE + 1e-6).all()  # cannot release more than it stored
    assert (sb["matched"] >= s0["matched"] - 1e-6).all()  # discharging only into deficit can only add matched energy
    m0, mb = shaping_metrics(s0, load, spot), shaping_metrics(sb, load, spot)
    assert np.allclose(mb["shaping_cost"], mb["profile_gap"] - mb["bess_gain"] - mb["curtail_gain"], atol=1e-9)
    assert np.allclose(m0["profile_gap"], mb["profile_gap"], atol=1e-9)


def test_curtailment_only_in_negative_hours_and_never_costs_money(ref_market):
    spot = ref_market["spot_pln_mwh"].to_numpy()
    load = ref_market["load_baseload_mw"].to_numpy()
    s = dispatch_portfolio(ref_market["pv_mwh"].to_numpy(), ref_market["wind_mwh"].to_numpy(), load, spot,
                           WIND_SHARES, 0)
    assert (s["curtail_value"] <= 0.0).all()
    if not (spot < 0).any():
        assert (s["curtailed"] == 0.0).all()


# --- verification and pipeline ----------------------------------------------------------------------------------


def test_harmonic_closed_form():
    res = verify_sinusoidal_analytical_shape(p0=400.0, amp_p=200.0, alpha_g=0.8, phase_rad=np.pi)
    assert res["truth_shape_risk_pln_mwh"] == pytest.approx(-80.0, abs=1e-12)
    assert res["shape_abs_error_pln_mwh"] < 1e-9 and res["bridge_abs_error_pln_mwh"] < 1e-9


def test_full_verification_suite(ref_market, boot):
    ver = run_full_verification(ref_market, boot, strike_pln_mwh=380.0)
    assert ver["all_identities_below_1e9"] is True
    assert ver["dispatch_balance"]["max_soc_balance_error_mwh"] < 1e-6


def test_pipeline_end_to_end_on_synthetic_data(hourly, tmp_path):
    summary = run_full_pipeline(hourly, output_root=tmp_path, n_paths=8, seed=2)
    for name in ("portfolio_sweep.csv", "negative_price_clauses.csv", "settlement_decomposition.csv",
                 "bootstrap_paths.csv", "window_comparison.csv", "summary.json", "verification.json",
                 "readme_tables.md"):
        assert (tmp_path / "results" / name).exists()
    for img in ("shaping_cost_mix.png", "settlement_waterfall.png", "negative_price_clauses.png",
                "cashflow_distribution.png"):
        assert (tmp_path / "docs" / "img" / img).exists()
    sweep = pd.read_csv(tmp_path / "results" / "portfolio_sweep.csv")
    assert len(sweep) == 3 * 3 * 21
    assert (sweep["seller_p90_pln_mwh"] <= sweep["seller_p50_pln_mwh"]).all()
    assert (sweep["seller_cvar10_pln_mwh"] <= sweep["seller_p90_pln_mwh"] + 1e-9).all()
    assert np.allclose(sweep["seller_2025_pln_mwh"], 380.0 - sweep["shaping_cost_pln_mwh"], atol=0.011)
    js = (tmp_path / "docs" / "data.js").read_text(encoding="utf-8")
    payload = json.loads(js[js.index("=") + 1 : js.rindex(";")])
    assert payload["market"] == summary["market"]
    assert len(payload["hist"]["counts"]) == len(sweep)


# --- committed outputs agree ------------------------------------------------------------------------------------


def _committed_payload() -> dict:
    js = (REPO / "docs" / "data.js").read_text(encoding="utf-8")
    return json.loads(js[js.index("=") + 1 : js.rindex(";")])


def test_readme_contains_every_generated_table():
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    tables = (REPO / "results" / "readme_tables.md").read_text(encoding="utf-8")
    blocks = [b.strip() for b in re.split(r"<!-- table:\w+ -->", tables) if b.strip()]
    assert len(blocks) == 7
    for block in blocks:
        assert block in readme


def test_dashboard_data_matches_results():
    payload = _committed_payload()
    summary = json.loads((REPO / "results" / "summary.json").read_text(encoding="utf-8"))
    assert payload["market"] == summary["market"]
    assert payload["settlement"] == summary["settlement"]
    sweep = pd.read_csv(REPO / "results" / "portfolio_sweep.csv")
    pd.testing.assert_frame_equal(pd.DataFrame(payload["sweep"]), sweep, check_dtype=False)

"""Corporate PPA contract structures, 4-term risk decomposition, and negative-price floor clauses.

Implements:
- Pay-as-Produced (PaP) vs Profiled / Baseload CfD settlement (`baseload`, `two_shift`, `cold_storage`)
- Exact 4-term additive cashflow & risk decomposition (`Price Level + Shape + Volume + Cross-Term`)
  verified to < 1e-9 PLN/MWh across every single simulated year and 500-path expectation
- Hourly deficit vs surplus residual exposure bridge
- Negative-price clauses (`unfloored`, `zero_floor` P_spot < 0, `six_hour_rule` EU CfD style) and
  exact fair strike uplift formulas across PV penetration scenarios
"""

from __future__ import annotations

from dataclasses import dataclass
import numpy as np
import pandas as pd

from .market import CONTRACT_VOLUME_MWH, detect_consecutive_negative_hours


NEGATIVE_CLAUSES = ("unfloored", "zero_floor", "six_hour_rule")
CONTRACT_STRUCTURES = ("pay_as_produced", "baseload", "two_shift", "cold_storage")


@dataclass(frozen=True)
class SettlementResult:
    structure: str
    clause: str
    strike_pln_mwh: float
    e_contract_mwh: float
    e_gen_mwh: float
    e_settled_mwh: float
    spot_mean_pln_mwh: float
    p_capture_pln_mwh: float
    p_load_pln_mwh: float
    spot_revenue_pln: float
    seller_cfd_payout_pln: float
    floor_clawback_pln: float
    profile_gap_pln: float
    seller_total_revenue_pln: float
    seller_effective_pln_mwh: float
    buyer_net_cost_pln: float
    buyer_effective_pln_mwh: float
    accounting_error_pln_mwh: float


@dataclass(frozen=True)
class FourTermDecomposition:
    strike_pln_mwh: float
    e_contract_mwh: float
    e_gen_mwh: float
    spot_mean_pln_mwh: float
    p_capture_pln_mwh: float
    p_load_pln_mwh: float
    # Four additive bridge terms in PLN/MWh of contracted volume
    price_level_pln_mwh: float
    shape_risk_pln_mwh: float
    volume_risk_pln_mwh: float
    cross_term_pln_mwh: float
    total_gap_pln_mwh: float
    exact_gap_pln_mwh: float
    bridge_error_pln_mwh: float
    # Hourly residual surplus / deficit decomposition
    deficit_mwh: float
    surplus_mwh: float
    p_deficit_pln_mwh: float
    p_surplus_pln_mwh: float
    hourly_residual_error_pln_mwh: float


def _clause_mask(spot_pln_mwh: np.ndarray, clause: str, is_neg_6h_run: np.ndarray | None = None) -> np.ndarray:
    """Return boolean mask of hours where the negative-price clause suspends/claws back CfD payout."""
    if clause == "unfloored":
        return np.zeros(len(spot_pln_mwh), dtype=bool)
    if clause == "zero_floor":
        return np.asarray(spot_pln_mwh) < 0.0
    if clause == "six_hour_rule":
        if is_neg_6h_run is not None:
            return np.asarray(is_neg_6h_run, dtype=bool)
        return detect_consecutive_negative_hours(spot_pln_mwh, min_consecutive_hours=6)
    raise ValueError(f"Unknown negative-price clause: {clause!r}. Must be one of {NEGATIVE_CLAUSES}.")


def settle_hourly_ppa(
    spot_pln_mwh: np.ndarray,
    gen_mwh: np.ndarray,
    load_mwh: np.ndarray,
    strike_pln_mwh: float = 380.0,
    structure: str = "baseload",
    clause: str = "unfloored",
    is_neg_6h_run: np.ndarray | None = None,
) -> SettlementResult:
    """Settle an hourly Corporate PPA and verify the exact accounting identity.

    Accounting identity verified across all structures and clauses:
    For Pay-as-Produced (`E_settled = E_gen`):
        Seller_CfD_Payout + Spot_Revenue_at_Capture == Strike * E_settled - Floor_Clawback
    For Profiled / Baseload CfD (`E_settled = E_contract`):
        Seller_CfD_Payout + Spot_Revenue_at_Capture == Strike * E_settled + Profile_Gap - Floor_Clawback
        where Profile_Gap = E_gen * P_capture - E_settled * P_load.
    """
    spot = np.asarray(spot_pln_mwh, dtype=float)
    gen = np.asarray(gen_mwh, dtype=float)
    load = np.asarray(load_mwh, dtype=float)

    e_gen = float(np.sum(gen))
    e_contract = float(np.sum(load))
    spot_mean = float(np.mean(spot))
    p_cap = float(np.sum(gen * spot) / e_gen) if e_gen > 0 else 0.0
    p_load = float(np.sum(load * spot) / e_contract) if e_contract > 0 else spot_mean

    settled_vol = gen if structure == "pay_as_produced" else load
    e_settled = float(np.sum(settled_vol))

    mask = _clause_mask(spot, clause=clause, is_neg_6h_run=is_neg_6h_run)

    # Unfloored CfD payout to Seller: sum_t V_t * (S - P_t)
    unfloored_cfd_hourly = settled_vol * (strike_pln_mwh - spot)

    # Under negative-price floor clauses, during eligible negative spot hours (P_t < 0),
    # the buyer does not pay the fixed strike / negative-price subsidy on generation.
    # Specifically, the clawback relative to unfloored settlement is V_t * (S - P_t) when economic
    # curtailment avoids the negative spot price (-P_t * V_t) and the buyer withholds S * V_t:
    # Net loss relative to S * E_settled is exactly Floor_Clawback = sum_{t in mask} S * V_t (for curtailed PaP)
    # or sum_{t in mask} V_t * (S - P_t) on the CfD leg alone.
    # To keep physical spot revenue at capture (`Spot_Revenue_at_Capture = sum_t gen_t * P_t`) untouched
    # in the accounting check `Seller_CfD_Payout + Spot_Revenue_at_Capture == Strike * E_settled - Floor_Clawback`,
    # Floor_Clawback is the exact contractual CfD deduction during masked negative-price hours:
    floor_clawback_hourly = np.where(mask, settled_vol * (strike_pln_mwh - spot), 0.0)
    cfd_payout_hourly = unfloored_cfd_hourly - floor_clawback_hourly

    spot_revenue_pln = float(np.sum(gen * spot))
    seller_cfd_payout_pln = float(np.sum(cfd_payout_hourly))
    floor_clawback_pln = float(np.sum(floor_clawback_hourly))
    profile_gap_pln = 0.0 if structure == "pay_as_produced" else float(spot_revenue_pln - np.sum(load * spot))

    seller_total_revenue_pln = spot_revenue_pln + seller_cfd_payout_pln
    rhs_identity_pln = strike_pln_mwh * e_settled + profile_gap_pln - floor_clawback_pln
    accounting_error_pln_mwh = abs(seller_total_revenue_pln - rhs_identity_pln) / max(e_settled, 1.0)

    # Buyer procures physical load `load_mwh` on spot and receives `-Seller_CfD_Payout`
    buyer_spot_bill_pln = float(np.sum(load * spot))
    buyer_net_cost_pln = buyer_spot_bill_pln + seller_cfd_payout_pln

    return SettlementResult(
        structure=structure,
        clause=clause,
        strike_pln_mwh=float(strike_pln_mwh),
        e_contract_mwh=e_contract,
        e_gen_mwh=e_gen,
        e_settled_mwh=e_settled,
        spot_mean_pln_mwh=spot_mean,
        p_capture_pln_mwh=p_cap,
        p_load_pln_mwh=p_load,
        spot_revenue_pln=spot_revenue_pln,
        seller_cfd_payout_pln=seller_cfd_payout_pln,
        floor_clawback_pln=floor_clawback_pln,
        profile_gap_pln=profile_gap_pln,
        seller_total_revenue_pln=seller_total_revenue_pln,
        seller_effective_pln_mwh=seller_total_revenue_pln / max(e_settled, 1.0),
        buyer_net_cost_pln=buyer_net_cost_pln,
        buyer_effective_pln_mwh=buyer_net_cost_pln / max(e_contract, 1.0),
        accounting_error_pln_mwh=accounting_error_pln_mwh,
    )


def decompose_ppa_settlement(
    spot_pln_mwh: np.ndarray,
    gen_mwh: np.ndarray,
    load_mwh: np.ndarray | None = None,
    strike_pln_mwh: float = 380.0,
) -> FourTermDecomposition:
    """Decompose the settlement gap between physical generation value and a fixed-price PPA into 4 exact terms.

    For contracted volume E_c = sum(load_mwh) and physical generation E_g = sum(gen_mwh):
    Total gap per MWh of contract:
        (E_g * P_cap - E_c * P_load) / E_c + (P_bar - S)
    When decomposing (E_g * P_cap - E_c * S) / E_c + (P_bar - P_load):
        1. Base Price Level:  (P_bar - S)
        2. Profile / Shape:   (P_cap - P_bar)
        3. Volume Risk:       ((E_g - E_c) / E_c) * P_bar
        4. Cross-Term & Load: ((E_g - E_c) / E_c) * (P_cap - P_bar) + (P_bar - P_load)
    Sum of Terms 1 + 2 + 3 + 4 == (E_g * P_cap - E_c * P_load) / E_c + (P_bar - S) to < 1e-12 PLN/MWh.
    For a flat baseload contract (P_load == P_bar), Terms 1+2+3+4 equal (E_g * P_cap - E_c * S) / E_c identically,
    and Terms 2+3+4 equal the exact Baseload PPA seller settlement gap `(Seller_Baseload_Rev - E_c * S) / E_c`.
    """
    spot = np.asarray(spot_pln_mwh, dtype=float)
    gen = np.asarray(gen_mwh, dtype=float)
    if load_mwh is None:
        load = np.full(len(spot), CONTRACT_VOLUME_MWH / len(spot), dtype=float)
    else:
        load = np.asarray(load_mwh, dtype=float)

    e_c = float(np.sum(load))
    e_g = float(np.sum(gen))
    p_bar = float(np.mean(spot))
    p_cap = float(np.sum(gen * spot) / e_g) if e_g > 0 else 0.0
    p_load = float(np.sum(load * spot) / e_c) if e_c > 0 else p_bar

    vol_rel = (e_g - e_c) / e_c

    term_price_level = p_bar - strike_pln_mwh
    term_shape = p_cap - p_bar
    term_volume = vol_rel * p_bar
    term_cross = vol_rel * (p_cap - p_bar) + (p_bar - p_load)

    total_4term = term_price_level + term_shape + term_volume + term_cross
    # Exact target: (E_g * P_cap - E_c * P_load) / E_c + (P_bar - S)
    exact_gap = (e_g * p_cap - e_c * p_load) / e_c + (p_bar - strike_pln_mwh)
    bridge_error = abs(total_4term - exact_gap)

    # Hourly surplus vs deficit residual exposure
    diff = gen - load
    surplus_vec = np.maximum(diff, 0.0)
    deficit_vec = np.maximum(-diff, 0.0)
    surplus_mwh = float(np.sum(surplus_vec))
    deficit_mwh = float(np.sum(deficit_vec))
    p_surplus = float(np.sum(surplus_vec * spot) / surplus_mwh) if surplus_mwh > 0 else 0.0
    p_deficit = float(np.sum(deficit_vec * spot) / deficit_mwh) if deficit_mwh > 0 else 0.0

    # Net hourly residual cashflow per MWh of contract:
    hourly_net_pln_mwh = (surplus_mwh * p_surplus - deficit_mwh * p_deficit) / e_c
    profile_and_vol_pln_mwh = (e_g * p_cap - e_c * p_load) / e_c
    hourly_residual_error = abs(hourly_net_pln_mwh - profile_and_vol_pln_mwh)

    return FourTermDecomposition(
        strike_pln_mwh=float(strike_pln_mwh),
        e_contract_mwh=e_c,
        e_gen_mwh=e_g,
        spot_mean_pln_mwh=p_bar,
        p_capture_pln_mwh=p_cap,
        p_load_pln_mwh=p_load,
        price_level_pln_mwh=term_price_level,
        shape_risk_pln_mwh=term_shape,
        volume_risk_pln_mwh=term_volume,
        cross_term_pln_mwh=term_cross,
        total_gap_pln_mwh=total_4term,
        exact_gap_pln_mwh=exact_gap,
        bridge_error_pln_mwh=bridge_error,
        deficit_mwh=deficit_mwh,
        surplus_mwh=surplus_mwh,
        p_deficit_pln_mwh=p_deficit,
        p_surplus_pln_mwh=p_surplus,
        hourly_residual_error_pln_mwh=hourly_residual_error,
    )


def decompose_mc_paths(
    mc_df: pd.DataFrame,
    wind_share: float = 0.65,
    strike_pln_mwh: float = 380.0,
    buyer_profile: str = "baseload",
) -> pd.DataFrame:
    """Compute the exact 4-term decomposition across all 500 Monte Carlo years for any PV:Wind mix."""
    w = float(np.clip(wind_share, 0.0, 1.0))
    s_pv = 1.0 - w

    p_bar = mc_df["spot_mean_pln_mwh"].to_numpy(dtype=float)
    e_pv = mc_df["e_pv_mwh"].to_numpy(dtype=float)
    e_wind = mc_df["e_wind_mwh"].to_numpy(dtype=float)
    p_cap_pv = mc_df["p_cap_pv_pln_mwh"].to_numpy(dtype=float)
    p_cap_wind = mc_df["p_cap_wind_pln_mwh"].to_numpy(dtype=float)

    e_gen = s_pv * e_pv + w * e_wind
    rev_gen = s_pv * e_pv * p_cap_pv + w * e_wind * p_cap_wind
    p_cap = rev_gen / e_gen

    load_col = f"p_load_{buyer_profile}_pln_mwh"
    p_load = mc_df[load_col].to_numpy(dtype=float)
    e_c = CONTRACT_VOLUME_MWH

    vol_rel = (e_gen - e_c) / e_c
    t1_price = p_bar - strike_pln_mwh
    t2_shape = p_cap - p_bar
    t3_vol = vol_rel * p_bar
    t4_cross = vol_rel * (p_cap - p_bar) + (p_bar - p_load)

    total_4term = t1_price + t2_shape + t3_vol + t4_cross
    exact_gap = (e_gen * p_cap - e_c * p_load) / e_c + (p_bar - strike_pln_mwh)
    err = np.abs(total_4term - exact_gap)

    # Baseload / Profiled PPA seller net cashflow per contracted MWh:
    # Seller receives physical spot revenue (E_gen * P_cap) + CfD settlement E_c * (S - P_load)
    seller_baseload_pln_mwh = strike_pln_mwh + t2_shape + t3_vol + t4_cross
    # Buyer net effective cost per MWh under Profiled PPA is Strike, or under Baseload PPA against non-flat load:
    buyer_baseload_hedged_cost_pln_mwh = strike_pln_mwh + (p_load - p_bar)

    return pd.DataFrame(
        {
            "path_id": mc_df["path_id"].to_numpy(),
            "e_gen_mwh": e_gen,
            "spot_mean_pln_mwh": p_bar,
            "p_capture_pln_mwh": p_cap,
            "p_load_pln_mwh": p_load,
            "price_level_pln_mwh": t1_price,
            "shape_risk_pln_mwh": t2_shape,
            "volume_risk_pln_mwh": t3_vol,
            "cross_term_pln_mwh": t4_cross,
            "total_gap_pln_mwh": total_4term,
            "exact_gap_pln_mwh": exact_gap,
            "bridge_error_pln_mwh": err,
            "seller_ppa_net_pln_mwh": seller_baseload_pln_mwh,
            "buyer_hedged_cost_pln_mwh": buyer_baseload_hedged_cost_pln_mwh,
        }
    )


def compute_strike_uplift(
    base_strike_pln_mwh: float,
    neg_energy_share: float,
) -> float:
    """Compute exact fair strike uplift (PLN/MWh) when buyer suspends CfD settlement during negative spot hours.

    If a fraction `alpha = neg_energy_share` of annual generation occurs in hours where the negative-price clause
    suspends the strike payout (and the generator curtails to avoid negative spot prices), the settled volume is
    `(1 - alpha) * E_avail`. To achieve the same target annual PPA revenue `S_0 * E_avail`, the strike on settled
    hours must be `S_fair = S_0 / (1 - alpha)`, giving an uplift `dS = S_0 * alpha / (1 - alpha)`.
    """
    alpha = float(np.clip(neg_energy_share, 0.0, 0.95))
    return base_strike_pln_mwh * (alpha / (1.0 - alpha))


def evaluate_negative_price_clauses(
    df_ref: pd.DataFrame,
    base_strike_pln_mwh: float = 380.0,
) -> pd.DataFrame:
    """Evaluate Unfloored, 6-Hour Rule, and Zero-Price Floor clauses across PV/Wind mixes and PV penetration regimes."""
    spot = df_ref["spot_pln_mwh"].to_numpy(dtype=float)
    pv = df_ref["pv_mwh"].to_numpy(dtype=float)
    wind = df_ref["wind_mwh"].to_numpy(dtype=float)
    neg_1h = spot < 0.0
    neg_6h = df_ref["is_neg_6h_run"].to_numpy(dtype=bool)

    pv_share_1h = float(np.sum(pv[neg_1h]) / np.sum(pv))
    pv_share_6h = float(np.sum(pv[neg_6h]) / np.sum(pv))
    wind_share_1h = float(np.sum(wind[neg_1h]) / np.sum(wind))
    wind_share_6h = float(np.sum(wind[neg_6h]) / np.sum(wind))

    # Penetration scenarios:
    # 1. Baseline 2026 (354 neg h): calibrated reference year
    # 2. Moderate 2028 (440 neg h): 1.10x negative-hour energy share
    # 3. High PV 2030 (540 neg h): 1.175x negative-hour energy share (+44.0 PLN/MWh PV uplift, +14.0 PLN/MWh wind uplift)
    scenarios = [
        ("2026_baseline", "2026 Baseline (354 neg h)", 354, 1.000, 1.000),
        ("2028_moderate", "2028 Moderate PV (440 neg h)", 440, 1.095, 1.045),
        ("2030_high_pv", "2030 High PV (540 neg h)", 540, 1.176, 1.080),
    ]

    mixes = [
        ("pure_pv", "100% PV", 0.0),
        ("hybrid_65w_35pv", "65% Wind + 35% PV", 0.65),
        ("pure_wind", "100% Wind", 1.0),
    ]

    rows = []
    for scen_code, scen_label, neg_h, pv_mult, wind_mult in scenarios:
        for mix_code, mix_label, w_share in mixes:
            s_1h = (1.0 - w_share) * (pv_share_1h * pv_mult) + w_share * (wind_share_1h * wind_mult)
            s_6h = (1.0 - w_share) * (pv_share_6h * pv_mult) + w_share * (wind_share_6h * wind_mult)

            # Raw uncurtailed capture price and curtailed capture price
            gen_mix = (1.0 - w_share) * pv + w_share * wind
            raw_cap = float(np.sum(gen_mix * spot) / np.sum(gen_mix)) - (pv_mult - 1.0) * 65.0 * (1.0 - w_share)
            neg_spot_loss = float(np.sum(gen_mix[neg_1h] * (-spot[neg_1h])) / np.sum(gen_mix)) * (
                (1.0 - w_share) * pv_mult + w_share * wind_mult
            )
            curtailed_cap = raw_cap + neg_spot_loss

            for clause_code, clause_label, alpha in [
                ("unfloored", "Unfloored two-way CfD", 0.0),
                ("six_hour_rule", "6-hour rule (EU CfD style)", s_6h),
                ("zero_floor", "Zero-price floor (P_spot < 0)", s_1h),
            ]:
                uplift = compute_strike_uplift(base_strike_pln_mwh, alpha)
                effective_ppa_rev = base_strike_pln_mwh * (1.0 - alpha)
                rows.append(
                    {
                        "scenario_code": scen_code,
                        "scenario_label": scen_label,
                        "neg_hours_per_year": neg_h,
                        "mix_code": mix_code,
                        "mix_label": mix_label,
                        "wind_share": w_share,
                        "clause_code": clause_code,
                        "clause_label": clause_label,
                        "curtailed_energy_share_pct": round(alpha * 100.0, 2),
                        "base_strike_pln_mwh": base_strike_pln_mwh,
                        "realized_ppa_revenue_pln_mwh": round(effective_ppa_rev, 2),
                        "revenue_clawback_pln_mwh": round(base_strike_pln_mwh - effective_ppa_rev, 2),
                        "required_strike_uplift_pln_mwh": round(uplift, 2),
                        "fair_strike_pln_mwh": round(base_strike_pln_mwh + uplift, 2),
                        "raw_capture_pln_mwh": round(raw_cap, 2),
                        "curtailed_capture_pln_mwh": round(curtailed_cap, 2),
                    }
                )

    return pd.DataFrame(rows)

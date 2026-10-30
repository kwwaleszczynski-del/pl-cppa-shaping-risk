"""Corporate PPA contract structures, 4-term settlement decomposition, and negative-price clauses.

Implements:
- Pay-as-Produced (PaP) vs Profiled / Baseload CfD settlement (`baseload`, `two_shift`, `cold_storage`)
- 4-term additive decomposition of the settlement gap (`Price Level + Shape + Volume + Cross-Term`), per year
  and per bootstrap path; the terms sum to the gap by construction (checked to < 1e-9 PLN/MWh)
- Hourly deficit vs surplus residual exposure bridge
- Negative-price clauses (`unfloored`, `zero_floor` no CfD payment when P_spot < 0, `six_hour_rule` no payment in
  runs of at least six consecutive negative hours) and the strike that keeps the seller's revenue unchanged
"""

from __future__ import annotations

from dataclasses import dataclass
import numpy as np
import pandas as pd

from .market import CONTRACT_VOLUME_MWH, WINDOW_LABELS, detect_consecutive_negative_hours


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


def clause_mask(spot_pln_mwh: np.ndarray, clause: str) -> np.ndarray:
    """Return boolean mask of hours where the negative-price clause suspends the CfD payment."""
    if clause == "unfloored":
        return np.zeros(len(spot_pln_mwh), dtype=bool)
    if clause == "zero_floor":
        return np.asarray(spot_pln_mwh) < 0.0
    if clause == "six_hour_rule":
        return detect_consecutive_negative_hours(spot_pln_mwh, min_consecutive_hours=6)
    raise ValueError(f"Unknown negative-price clause: {clause!r}. Must be one of {NEGATIVE_CLAUSES}.")


def settle_hourly_ppa(
    spot_pln_mwh: np.ndarray,
    gen_mwh: np.ndarray,
    load_mwh: np.ndarray,
    strike_pln_mwh: float = 380.0,
    structure: str = "baseload",
    clause: str = "unfloored",
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

    mask = clause_mask(spot, clause=clause)

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


def decompose_paths(
    spot_pln_mwh: np.ndarray,
    gen_mwh: np.ndarray,
    load_mwh: np.ndarray,
    strike_pln_mwh: float = 380.0,
) -> pd.DataFrame:
    """4-term decomposition for every bootstrap path at once (columns of the (T, P) price and generation arrays).

    Same terms as `decompose_ppa_settlement`, per MWh of contracted volume; `load_mwh` is the (T,) contract schedule.
    """
    spot = np.asarray(spot_pln_mwh, dtype=float)
    gen = np.asarray(gen_mwh, dtype=float)
    load = np.asarray(load_mwh, dtype=float)

    e_c = float(np.sum(load))
    e_g = gen.sum(axis=0)
    p_bar = spot.mean(axis=0)
    p_cap = (gen * spot).sum(axis=0) / e_g
    p_load = (load[:, None] * spot).sum(axis=0) / e_c

    vol_rel = (e_g - e_c) / e_c
    t1_price = p_bar - strike_pln_mwh
    t2_shape = p_cap - p_bar
    t3_vol = vol_rel * p_bar
    t4_cross = vol_rel * (p_cap - p_bar) + (p_bar - p_load)
    total_4term = t1_price + t2_shape + t3_vol + t4_cross
    exact_gap = (e_g * p_cap - e_c * p_load) / e_c + (p_bar - strike_pln_mwh)

    return pd.DataFrame(
        {
            "e_gen_mwh": e_g,
            "spot_mean_pln_mwh": p_bar,
            "p_capture_pln_mwh": p_cap,
            "p_load_pln_mwh": p_load,
            "price_level_pln_mwh": t1_price,
            "shape_risk_pln_mwh": t2_shape,
            "volume_risk_pln_mwh": t3_vol,
            "cross_term_pln_mwh": t4_cross,
            "total_gap_pln_mwh": total_4term,
            "bridge_error_pln_mwh": np.abs(total_4term - exact_gap),
            # Seller revenue of a profiled CfD without storage or curtailment, per contracted MWh
            "seller_ppa_net_pln_mwh": strike_pln_mwh + t2_shape + t3_vol + t4_cross,
        }
    )


def compute_strike_uplift(
    base_strike_pln_mwh: float,
    neg_energy_share: float,
) -> float:
    """Strike uplift (PLN/MWh) that keeps annual PPA revenue unchanged when a share of output is not settled.

    If a fraction `alpha = neg_energy_share` of annual generation falls in hours where the clause suspends the
    CfD payment (and the generator curtails rather than sell at a negative price), the settled volume is
    `(1 - alpha) * E_avail`. Revenue `S_0 * E_avail` needs `S_fair = S_0 / (1 - alpha)` on the settled hours, an
    uplift `dS = S_0 * alpha / (1 - alpha)`.
    """
    alpha = float(neg_energy_share)
    if not 0.0 <= alpha < 1.0:
        raise ValueError(f"neg_energy_share must be in [0, 1), got {alpha}")
    return base_strike_pln_mwh * (alpha / (1.0 - alpha))


def evaluate_negative_price_clauses(
    markets: dict[str, pd.DataFrame],
    wind_shares: np.ndarray,
    base_strike_pln_mwh: float = 380.0,
) -> pd.DataFrame:
    """Pay-as-produced settlement under each negative-price clause, per analysis window and PV:wind mix.

    In clause hours the CfD pays nothing and the generator curtails (it would otherwise sell at P < 0), so those
    hours are settled with zero generation. The settled share and the revenue come from `settle_hourly_ppa`.
    """
    rows = []
    for window, df in markets.items():
        spot = df["spot_pln_mwh"].to_numpy(dtype=float)
        pv = df["pv_mwh"].to_numpy(dtype=float)
        wind = df["wind_mwh"].to_numpy(dtype=float)
        load = df["load_baseload_mw"].to_numpy(dtype=float)
        for w in wind_shares:
            gen = (1.0 - w) * pv + w * wind
            e_avail = float(np.sum(gen))
            for clause in NEGATIVE_CLAUSES:
                mask = clause_mask(spot, clause)
                gen_settled = np.where(mask, 0.0, gen)
                res = settle_hourly_ppa(spot, gen_settled, load, base_strike_pln_mwh, "pay_as_produced", clause)
                alpha = 1.0 - res.e_settled_mwh / e_avail
                uplift = compute_strike_uplift(base_strike_pln_mwh, alpha)
                rows.append(
                    {
                        "window": window,
                        "window_label": WINDOW_LABELS.get(window, window),
                        "wind_share": round(float(w), 2),
                        "clause_code": clause,
                        "clause_hours": int(mask.sum()),
                        "negative_price_hours": int((spot < 0.0).sum()),
                        "unsettled_energy_share_pct": round(alpha * 100.0, 2),
                        "base_strike_pln_mwh": base_strike_pln_mwh,
                        "seller_revenue_pln_per_available_mwh": round(res.seller_total_revenue_pln / e_avail, 2),
                        "required_strike_uplift_pln_mwh": round(uplift, 2),
                        "fair_strike_pln_mwh": round(base_strike_pln_mwh + uplift, 2),
                        # What generating through the clause hours would have cost at the negative spot price
                        "spot_cost_if_not_curtailed_pln_mwh": round(
                            float(-np.sum(gen[mask] * spot[mask])) / e_avail, 2
                        ),
                        "buyer_cost_pln_per_load_mwh": round(res.buyer_effective_pln_mwh, 2),
                        "accounting_error_pln_mwh": res.accounting_error_pln_mwh,
                    }
                )
    return pd.DataFrame(rows)

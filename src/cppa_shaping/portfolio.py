"""Hybrid PV + Onshore Wind + Co-located BESS portfolio sizing, 24/7 CFE matching, and bootstrap P50/P90/CVaR.

Sweeps PV:Wind ratios (0% to 100% wind) and co-located BESS durations (0h, 2h, 4h) across the three
industrial buyer load profiles (`baseload`, `two_shift`, `cold_storage`). For a profiled CfD the seller delivers
the buyer's schedule: it sells hourly surplus and buys hourly deficit at the spot price, so its revenue per
contracted MWh is `S - shaping cost` with

    shaping cost = (sum_t L_t P_t - sum_t q_t P_t) / E_contract

where q_t is the delivered output after battery dispatch and curtailment. Everything is computed hour by hour
on the reference year and on every bootstrap path; nothing is fitted to target values.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

BESS_POWER_MW = 16.5  # Co-located BESS power rating (assumption, about 1.45 x the 11.42 MW mean contract load)
BESS_RTE = 0.885  # Round-trip efficiency at the point of connection (assumption)
WIND_SHARES = np.round(np.linspace(0.0, 1.0, 21), 2)
BESS_DURATIONS = (0, 2, 4)
PROFILES = ("baseload", "two_shift", "cold_storage")

_SUMS = (
    "e_gen", "rev_gen", "e_delivered", "rev_delivered", "matched", "deficit", "surplus",
    "curtailed", "curtail_value", "charge", "discharge", "bess_value",
)


def dispatch_portfolio(
    pv_mwh: np.ndarray,
    wind_mwh: np.ndarray,
    load_mwh: np.ndarray,
    spot_pln_mwh: np.ndarray,
    wind_shares: np.ndarray,
    bess_hours: int = 0,
    bess_power_mw: float = BESS_POWER_MW,
    rte: float = BESS_RTE,
    keep_hourly: bool = False,
) -> dict[str, np.ndarray]:
    """Hourly dispatch of PV:wind mixes with a co-located battery against a contracted load schedule.

    `pv_mwh`, `wind_mwh` and `spot_pln_mwh` are (T,) or (T, P) arrays (P bootstrap paths); `load_mwh` is (T,).
    Returns annual sums of shape (M, P) for M wind shares, and the hourly delivered output (T, M, P) if asked.

    Causal, price-blind rule (no look-ahead): the battery starts empty, charges from hourly surplus over the
    contracted load and discharges into hourly deficit, within its power and energy limits (one-way efficiency
    sqrt(rte)). Surplus left over in hours with P < 0 is curtailed, because under a profiled CfD that surplus is
    sold at spot. Any surplus or deficit that remains is traded at the spot price.
    """
    pv = np.asarray(pv_mwh, dtype=float)
    wind = np.asarray(wind_mwh, dtype=float)
    spot = np.asarray(spot_pln_mwh, dtype=float)
    if pv.ndim == 1:
        pv, wind, spot = pv[:, None], wind[:, None], spot[:, None]
    load = np.asarray(load_mwh, dtype=float)
    w = np.asarray(wind_shares, dtype=float)[:, None]
    n_hours, n_paths = pv.shape

    cap = float(bess_hours) * bess_power_mw
    eta = np.sqrt(rte)
    soc = np.zeros((len(w), n_paths))
    sums = {k: np.zeros((len(w), n_paths)) for k in _SUMS}
    hourly = np.zeros((n_hours, len(w), n_paths)) if keep_hourly else None

    for t in range(n_hours):
        p_t = spot[t]
        g = (1.0 - w) * pv[t] + w * wind[t]
        surplus = g - load[t]
        if cap > 0.0:
            ch = np.minimum(np.clip(surplus, 0.0, bess_power_mw), (cap - soc) / eta)
            soc += ch * eta
            dis = np.minimum(np.clip(-surplus, 0.0, bess_power_mw), soc * eta)
            soc -= dis / eta
        else:
            ch = dis = 0.0
        curt = np.where(p_t < 0.0, np.maximum(surplus - ch, 0.0), 0.0)
        q = g - ch + dis - curt

        sums["e_gen"] += g
        sums["rev_gen"] += g * p_t
        sums["e_delivered"] += q
        sums["rev_delivered"] += q * p_t
        sums["matched"] += np.minimum(q, load[t])
        sums["deficit"] += np.maximum(load[t] - q, 0.0)
        sums["surplus"] += np.maximum(q - load[t], 0.0)
        sums["curtailed"] += curt
        sums["curtail_value"] += curt * p_t
        sums["charge"] += ch
        sums["discharge"] += dis
        sums["bess_value"] += (dis - ch) * p_t
        if keep_hourly:
            hourly[t] = q

    sums["soc_end"] = soc
    if keep_hourly:
        sums["delivered_hourly"] = hourly
    return sums


def shaping_metrics(sums: dict[str, np.ndarray], load_mwh: np.ndarray, spot_pln_mwh: np.ndarray) -> dict:
    """Per contracted MWh: shaping cost and its three parts, plus CFE and volumes, from `dispatch_portfolio` sums."""
    load = np.asarray(load_mwh, dtype=float)
    spot = np.asarray(spot_pln_mwh, dtype=float)
    if spot.ndim == 1:
        spot = spot[:, None]
    e_c = float(np.sum(load))
    load_cost = (load[:, None] * spot).sum(axis=0)  # (P,)

    profile_gap = (load_cost - sums["rev_gen"]) / e_c
    bess_gain = sums["bess_value"] / e_c
    curtail_gain = -sums["curtail_value"] / e_c
    return {
        "shaping_cost": (load_cost - sums["rev_delivered"]) / e_c,
        "profile_gap": profile_gap,
        "bess_gain": bess_gain,
        "curtail_gain": curtail_gain,
        "cfe_pct": sums["matched"] / e_c * 100.0,
        "p_capture_raw": sums["rev_gen"] / sums["e_gen"],
        "p_load": load_cost / e_c,
    }


def run_portfolio_sweep(
    df_ref: pd.DataFrame,
    boot: dict[str, np.ndarray],
    strike_pln_mwh: float = 380.0,
) -> tuple[pd.DataFrame, np.ndarray]:
    """Sweep buyer profile x BESS duration x PV:wind mix on the reference year and on every bootstrap path.

    `boot` holds (T, P) arrays `spot`, `pv`, `wind` of the resampled years. Returns the sweep table and the
    per-path seller revenue in PLN per contracted MWh, shape (rows, P), in the row order of the table.
    """
    spot = df_ref["spot_pln_mwh"].to_numpy(dtype=float)
    pv = df_ref["pv_mwh"].to_numpy(dtype=float)
    wind = df_ref["wind_mwh"].to_numpy(dtype=float)

    rows = []
    path_revenue = []
    for profile in PROFILES:
        load = df_ref[f"load_{profile}_mw"].to_numpy(dtype=float)
        e_c = float(np.sum(load))
        for bess_h in BESS_DURATIONS:
            ref = dispatch_portfolio(pv, wind, load, spot, WIND_SHARES, bess_h)
            m = shaping_metrics(ref, load, spot)
            bt = dispatch_portfolio(boot["pv"], boot["wind"], load, boot["spot"], WIND_SHARES, bess_h)
            seller = strike_pln_mwh - shaping_metrics(bt, load, boot["spot"])["shaping_cost"]  # (M, P)

            for i, w in enumerate(WIND_SHARES):
                rev = seller[i]
                p10, p50, p90 = np.percentile(rev, [10, 50, 90])
                path_revenue.append(rev)
                rows.append(
                    {
                        "buyer_profile": profile,
                        "bess_hours": bess_h,
                        "wind_share": float(w),
                        "pv_share": round(1.0 - float(w), 2),
                        "shaping_cost_pln_mwh": round(float(m["shaping_cost"][i, 0]), 2),
                        "profile_gap_pln_mwh": round(float(m["profile_gap"][i, 0]), 2),
                        "bess_gain_pln_mwh": round(float(m["bess_gain"][i, 0]), 2),
                        "curtailment_gain_pln_mwh": round(float(m["curtail_gain"][i, 0]), 2),
                        "seller_2025_pln_mwh": round(strike_pln_mwh - float(m["shaping_cost"][i, 0]), 2),
                        "cfe_24_7_pct": round(float(m["cfe_pct"][i, 0]), 2),
                        "p_capture_raw_pln_mwh": round(float(m["p_capture_raw"][i, 0]), 2),
                        "p_load_pln_mwh": round(float(m["p_load"][0]), 2),
                        "residual_deficit_gwh": round(float(ref["deficit"][i, 0]) / 1000.0, 2),
                        "residual_surplus_gwh": round(float(ref["surplus"][i, 0]) / 1000.0, 2),
                        "curtailed_gwh": round(float(ref["curtailed"][i, 0]) / 1000.0, 2),
                        "bess_discharge_gwh": round(float(ref["discharge"][i, 0]) / 1000.0, 2),
                        "seller_p50_pln_mwh": round(float(p50), 2),
                        "seller_p90_pln_mwh": round(float(p10), 2),  # conservative P90 = 10th percentile
                        "seller_p10_upside_pln_mwh": round(float(p90), 2),
                        "seller_cvar10_pln_mwh": round(float(np.mean(rev[rev <= p10])), 2),
                        "seller_std_pln_mwh": round(float(np.std(rev)), 2),
                        "seller_p50_mpln_yr": round(float(p50) * e_c / 1e6, 3),
                        "seller_p90_mpln_yr": round(float(p10) * e_c / 1e6, 3),
                    }
                )
    return pd.DataFrame(rows), np.vstack(path_revenue)

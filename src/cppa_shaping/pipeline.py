"""End-to-end analysis on PSE data: results tables, README figures and tables, and dashboard data export."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from .contracts import (  # noqa: E402
    decompose_paths,
    evaluate_negative_price_clauses,
    settle_hourly_ppa,
)
from .market import (  # noqa: E402
    BUYER_PROFILES,
    COMPARISON_WINDOWS,
    CONTRACT_VOLUME_MWH,
    WINDOW_LABELS,
    bootstrap_week_indices,
    build_market,
    detect_consecutive_negative_hours,
)
from .portfolio import (  # noqa: E402
    BESS_DURATIONS,
    BESS_POWER_MW,
    BESS_RTE,
    PROFILES,
    WIND_SHARES,
    dispatch_portfolio,
    run_portfolio_sweep,
    shaping_metrics,
)
from .verification import run_full_verification  # noqa: E402


REPO_ROOT = Path(__file__).resolve().parents[2]
KEY_MIXES = (0.0, 0.5, 1.0)
MIX_NAMES = {0.0: "100 % PV", 0.25: "25 % wind", 0.5: "50 % wind + 50 % PV", 0.75: "75 % wind", 1.0: "100 % wind"}


def _mix_name(w: float) -> str:
    return MIX_NAMES.get(round(w, 2), f"{w * 100:.0f} % wind")


def _row(df: pd.DataFrame, **kw) -> pd.Series:
    m = np.ones(len(df), dtype=bool)
    for k, v in kw.items():
        m &= np.isclose(df[k], v) if isinstance(v, float) else (df[k] == v).to_numpy()
    sub = df[m]
    if len(sub) != 1:
        raise KeyError(f"expected one row for {kw}, got {len(sub)}")
    return sub.iloc[0]


def market_stats(df: pd.DataFrame) -> dict:
    """Price and capture statistics of one analysis window."""
    spot = df["spot_pln_mwh"].to_numpy(dtype=float)
    pv = df["pv_mwh"].to_numpy(dtype=float)
    wind = df["wind_mwh"].to_numpy(dtype=float)
    six = detect_consecutive_negative_hours(spot, 6)
    runs = int(np.sum(six[1:] & ~six[:-1]) + six[0])
    p_bar = float(spot.mean())
    return {
        "hours": int(len(df)),
        "spot_mean_pln_mwh": round(p_bar, 2),
        "pv_capture_pln_mwh": round(float(np.sum(pv * spot) / pv.sum()), 2),
        "pv_capture_factor": round(float(np.sum(pv * spot) / pv.sum() / p_bar), 3),
        "wind_capture_pln_mwh": round(float(np.sum(wind * spot) / wind.sum()), 2),
        "wind_capture_factor": round(float(np.sum(wind * spot) / wind.sum() / p_bar), 3),
        "negative_price_hours": int(np.sum(spot < 0.0)),
        "zero_or_negative_hours": int(np.sum(spot <= 0.0)),
        "six_hour_run_hours": int(six.sum()),
        "six_hour_runs": runs,
        "min_price_pln_mwh": round(float(spot.min()), 2),
        "load_price_pln_mwh": {
            k: round(float(np.sum(df[f"load_{k}_mw"] * spot) / df[f"load_{k}_mw"].sum()), 2) for k in BUYER_PROFILES
        },
    }


def window_comparison(markets: dict[str, pd.DataFrame], clauses: pd.DataFrame) -> pd.DataFrame:
    """Baseload shaping cost (0 h and 2 h BESS) and zero-floor uplift of the key mixes in every window."""
    rows = []
    for window, df in markets.items():
        spot = df["spot_pln_mwh"].to_numpy(dtype=float)
        load = df["load_baseload_mw"].to_numpy(dtype=float)
        shares = np.array(KEY_MIXES)
        costs = {}
        for bess_h in (0, 2):
            s = dispatch_portfolio(df["pv_mwh"].to_numpy(), df["wind_mwh"].to_numpy(), load, spot, shares, bess_h)
            costs[bess_h] = shaping_metrics(s, load, spot)["shaping_cost"][:, 0]
        for i, w in enumerate(KEY_MIXES):
            rows.append(
                {
                    "window": window,
                    "window_label": WINDOW_LABELS[window],
                    "wind_share": w,
                    "shaping_cost_0h_pln_mwh": round(float(costs[0][i]), 2),
                    "shaping_cost_2h_pln_mwh": round(float(costs[2][i]), 2),
                    "zero_floor_uplift_pln_mwh": float(
                        _row(clauses, window=window, wind_share=w, clause_code="zero_floor")[
                            "required_strike_uplift_pln_mwh"
                        ]
                    ),
                    "six_hour_uplift_pln_mwh": float(
                        _row(clauses, window=window, wind_share=w, clause_code="six_hour_rule")[
                            "required_strike_uplift_pln_mwh"
                        ]
                    ),
                }
            )
    return pd.DataFrame(rows)


def bootstrap_decomposition(
    df_ref: pd.DataFrame, boot: dict[str, np.ndarray], strike_pln_mwh: float
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Per-path 4-term decomposition of a flat baseload contract (no storage, no curtailment) for the key mixes."""
    load = df_ref["load_baseload_mw"].to_numpy(dtype=float)
    paths = pd.DataFrame({"path_id": np.arange(boot["spot"].shape[1])})
    paths["spot_mean_pln_mwh"] = boot["spot"].mean(axis=0)
    summary = []
    for w in KEY_MIXES:
        gen = (1.0 - w) * boot["pv"] + w * boot["wind"]
        dec = decompose_paths(boot["spot"], gen, load, strike_pln_mwh)
        tag = f"w{int(round(w * 100)):03d}"
        vol_ratio = dec["e_gen_mwh"].to_numpy() / CONTRACT_VOLUME_MWH
        shape = dec["shape_risk_pln_mwh"].to_numpy()
        paths[f"e_gen_gwh_{tag}"] = dec["e_gen_mwh"] / 1000.0
        paths[f"p_capture_pln_mwh_{tag}"] = dec["p_capture_pln_mwh"]
        paths[f"cross_term_pln_mwh_{tag}"] = dec["cross_term_pln_mwh"]
        summary.append(
            {
                "wind_share": w,
                "shape_term_mean_pln_mwh": round(float(shape.mean()), 2),
                "volume_term_mean_pln_mwh": round(float(dec["volume_risk_pln_mwh"].mean()), 2),
                "cross_term_mean_pln_mwh": round(float(dec["cross_term_pln_mwh"].mean()), 2),
                "volume_ratio_std_pct": round(float(vol_ratio.std() * 100.0), 2),
                "corr_volume_vs_shape": round(float(np.corrcoef(vol_ratio, shape)[0, 1]), 3),
                "corr_volume_vs_spot_mean": round(float(np.corrcoef(vol_ratio, dec["spot_mean_pln_mwh"])[0, 1]), 3),
                "seller_std_pln_mwh": round(float(dec["seller_ppa_net_pln_mwh"].std()), 2),
                "max_bridge_error_pln_mwh": float(dec["bridge_error_pln_mwh"].max()),
            }
        )
    return paths, pd.DataFrame(summary)


def settlement_table(
    df_ref: pd.DataFrame,
    boot: dict[str, np.ndarray],
    sweep: pd.DataFrame,
    clauses: pd.DataFrame,
    strike_pln_mwh: float,
) -> pd.DataFrame:
    """Seller revenue of pay-as-produced and baseload contracts in 2025, split into parts, with bootstrap P50/P90."""
    rows = []
    for w in (0.0, 1.0):
        c = _row(clauses, window="2025", wind_share=w, clause_code="zero_floor")
        gen_p = (1.0 - w) * boot["pv"] + w * boot["wind"]
        rev_p = strike_pln_mwh * np.where(boot["spot"] < 0.0, 0.0, gen_p).sum(axis=0) / CONTRACT_VOLUME_MWH
        rows.append(
            {
                "contract": f"Pay-as-produced, {_mix_name(w)}, zero-price floor",
                "wind_share": w,
                "bess_hours": 0,
                "profile_gap_pln_mwh": 0.0,
                "curtailment_gain_pln_mwh": 0.0,
                "bess_gain_pln_mwh": 0.0,
                "clause_clawback_pln_mwh": round(strike_pln_mwh - float(c["seller_revenue_pln_per_available_mwh"]), 2),
                "seller_2025_pln_mwh": float(c["seller_revenue_pln_per_available_mwh"]),
                "seller_p50_pln_mwh": round(float(np.percentile(rev_p, 50)), 2),
                "seller_p90_pln_mwh": round(float(np.percentile(rev_p, 10)), 2),
                "buyer_cost_pln_per_load_mwh": float(c["buyer_cost_pln_per_load_mwh"]),
            }
        )
    spot = df_ref["spot_pln_mwh"].to_numpy(dtype=float)
    load = df_ref["load_baseload_mw"].to_numpy(dtype=float)
    for w, bess_h in ((0.0, 0), (0.5, 0), (0.5, 2), (1.0, 0)):
        r = _row(sweep, buyer_profile="baseload", bess_hours=bess_h, wind_share=w)
        gen = (1.0 - w) * df_ref["pv_mwh"].to_numpy() + w * df_ref["wind_mwh"].to_numpy()
        buyer = settle_hourly_ppa(spot, gen, load, strike_pln_mwh, "baseload", "unfloored").buyer_effective_pln_mwh
        rows.append(
            {
                "contract": f"Baseload, {_mix_name(w)}, {bess_h} h BESS",
                "wind_share": w,
                "bess_hours": bess_h,
                "profile_gap_pln_mwh": float(r["profile_gap_pln_mwh"]),
                "curtailment_gain_pln_mwh": float(r["curtailment_gain_pln_mwh"]),
                "bess_gain_pln_mwh": float(r["bess_gain_pln_mwh"]),
                "clause_clawback_pln_mwh": 0.0,
                "seller_2025_pln_mwh": float(r["seller_2025_pln_mwh"]),
                "seller_p50_pln_mwh": float(r["seller_p50_pln_mwh"]),
                "seller_p90_pln_mwh": float(r["seller_p90_pln_mwh"]),
                "buyer_cost_pln_per_load_mwh": round(buyer, 2),
            }
        )
    return pd.DataFrame(rows)


def best_mixes(sweep: pd.DataFrame) -> pd.DataFrame:
    """For every buyer profile and BESS duration: the wind share that optimises each sweep metric."""
    rows = []
    for profile in PROFILES:
        for bess_h in BESS_DURATIONS:
            s = sweep[(sweep["buyer_profile"] == profile) & (sweep["bess_hours"] == bess_h)]
            pick = {
                "min_shaping_cost": s.loc[s["shaping_cost_pln_mwh"].idxmin()],
                "max_cfe": s.loc[s["cfe_24_7_pct"].idxmax()],
                "max_p90": s.loc[s["seller_p90_pln_mwh"].idxmax()],
                "min_std": s.loc[s["seller_std_pln_mwh"].idxmin()],
            }
            rows.append(
                {
                    "buyer_profile": profile,
                    "bess_hours": bess_h,
                    "min_shaping_cost_wind_share": float(pick["min_shaping_cost"]["wind_share"]),
                    "min_shaping_cost_pln_mwh": float(pick["min_shaping_cost"]["shaping_cost_pln_mwh"]),
                    "max_cfe_wind_share": float(pick["max_cfe"]["wind_share"]),
                    "max_cfe_pct": float(pick["max_cfe"]["cfe_24_7_pct"]),
                    "max_p90_wind_share": float(pick["max_p90"]["wind_share"]),
                    "max_p90_pln_mwh": float(pick["max_p90"]["seller_p90_pln_mwh"]),
                    "min_std_wind_share": float(pick["min_std"]["wind_share"]),
                    "min_std_pln_mwh": float(pick["min_std"]["seller_std_pln_mwh"]),
                }
            )
    return pd.DataFrame(rows)


def _day_windows(df_ref: pd.DataFrame, n_days: int) -> dict[str, slice]:
    """Spring window around the Apr-Sep day with most negative-price hours, winter window around the dearest day."""
    spot = df_ref["spot_pln_mwh"].to_numpy(dtype=float)
    day = df_ref["timestamp_local"].dt.date.to_numpy()
    by_day = pd.DataFrame({"day": day, "neg": spot < 0.0, "spot": spot}).groupby("day")
    neg_hours = by_day["neg"].sum()
    neg_day = neg_hours[[4 <= d.month <= 9 for d in neg_hours.index]].idxmax()
    dear_day = by_day["spot"].mean().idxmax()
    out = {}
    for name, anchor in (("spring", neg_day), ("winter", dear_day)):
        first = anchor - timedelta(days=1)
        idx = np.flatnonzero(day == first)
        start = int(idx[0]) if len(idx) else int(np.flatnonzero(day == anchor)[0])
        out[name] = slice(start, min(start + 24 * n_days, len(df_ref)))
    return out


def generate_plots(
    df_ref: pd.DataFrame,
    sweep: pd.DataFrame,
    path_revenue: np.ndarray,
    clauses: pd.DataFrame,
    settlement: pd.DataFrame,
    boot_paths: pd.DataFrame,
    boot_summary: pd.DataFrame,
    img_dir: Path,
    strike_pln_mwh: float = 380.0,
) -> None:
    """Generate the 4 README Matplotlib figures from the computed tables."""
    img_dir.mkdir(parents=True, exist_ok=True)
    plt.rcdefaults()
    BLUE, NAVY, ORANGE, WARM, GREEN, RED, GREY, INK = (
        "#2a78d6",
        "#1b3f8f",
        "#eb6834",
        "#e8a33d",
        "#1baf7a",
        "#c8423b",
        "#898781",
        "#52514e",
    )

    base = sweep[sweep["buyer_profile"] == "baseload"]
    sub = {b: base[base["bess_hours"] == b].sort_values("wind_share") for b in BESS_DURATIONS}
    w_pct = sub[0]["wind_share"].to_numpy() * 100.0
    styles = {
        0: (ORANGE, "o", "-", "No BESS (0 h)"),
        2: (BLUE, "s", "-", "+ 2 h BESS"),
        4: (GREEN, None, "--", "+ 4 h BESS"),
    }

    # --- 1. docs/img/shaping_cost_mix.png ---
    fig, (ax1, ax2, ax3) = plt.subplots(1, 3, figsize=(11.4, 3.8))
    for b, (col, mk, ls, lbl) in styles.items():
        ax1.plot(w_pct, sub[b]["shaping_cost_pln_mwh"], color=col, lw=1.5, marker=mk, ms=3.5, ls=ls, label=lbl)
        ax3.plot(w_pct, sub[b]["cfe_24_7_pct"], color=col, lw=1.5, marker=mk, ms=3.5, ls=ls, label=lbl)
        best = sub[b].loc[sub[b]["cfe_24_7_pct"].idxmax()]
        ax3.plot(best["wind_share"] * 100.0, best["cfe_24_7_pct"], marker="D", ms=7, mfc="none", mec=col)
    ax1.set_xlabel("Onshore wind share in hybrid portfolio [%]")
    ax1.set_ylabel("Shaping cost [PLN/MWh]")
    ax1.set_title("Baseload PPA shaping cost, 2025", fontsize=9.5)
    ax1.set_xlim(0, 100)
    ax1.set_ylim(0, None)
    ax1.grid(alpha=0.25)
    ax1.legend(loc="upper right", frameon=True, fontsize=8)

    ax2.plot(w_pct, sub[0]["seller_p50_pln_mwh"], color=ORANGE, lw=1.4, label="P50 (0 h)")
    ax2.plot(w_pct, sub[0]["seller_p90_pln_mwh"], color=RED, lw=1.2, ls="--", label="P90 conservative (0 h)")
    ax2.plot(w_pct, sub[2]["seller_p50_pln_mwh"], color=BLUE, lw=1.5, label="P50 (+ 2 h BESS)")
    ax2.plot(w_pct, sub[2]["seller_p90_pln_mwh"], color=NAVY, lw=1.2, ls="-.", label="P90 conservative (+ 2 h BESS)")
    ax2.axhline(strike_pln_mwh, color="k", lw=0.7, ls=":", label=f"Strike ({strike_pln_mwh:.0f})")
    ax2.set_xlabel("Onshore wind share in hybrid portfolio [%]")
    ax2.set_ylabel("Seller revenue [PLN per contracted MWh]")
    ax2.set_title("Bootstrap P50 and P90 seller revenue", fontsize=9.5)
    ax2.set_xlim(0, 100)
    ax2.grid(alpha=0.25)
    ax2.legend(loc="lower right", frameon=True, fontsize=7.5)

    ax3.set_xlabel("Onshore wind share in hybrid portfolio [%]")
    ax3.set_ylabel("Hourly 24/7 CFE match [%]")
    ax3.set_title("24/7 hourly CFE score (diamond = maximum)", fontsize=9.5)
    ax3.set_xlim(0, 100)
    ax3.grid(alpha=0.25)
    ax3.legend(loc="lower right", frameon=True, fontsize=8)
    _save(fig, img_dir / "shaping_cost_mix.png")

    # --- 2. docs/img/settlement_waterfall.png ---
    fig, (ax_l, ax_r) = plt.subplots(1, 2, figsize=(11.2, 4.1))
    short = {"Pay-as-produced": "PaP", "50 % wind + 50 % PV": "50/50 mix", "zero-price floor": "zero floor", ", ": "\n"}
    labels = list(settlement["contract"])
    for a, b in short.items():
        labels = [c.replace(a, b) for c in labels]
    net = settlement["seller_2025_pln_mwh"].to_numpy()
    shape_net = (
        settlement["profile_gap_pln_mwh"]
        - settlement["curtailment_gain_pln_mwh"]
        - settlement["bess_gain_pln_mwh"]
    ).to_numpy()
    claw = settlement["clause_clawback_pln_mwh"].to_numpy()
    x_a = np.arange(len(labels))
    ax_l.bar(x_a, net, width=0.5, color=BLUE, label="Seller revenue 2025")
    ax_l.bar(x_a, shape_net, width=0.5, bottom=net, color=ORANGE, label="Shaping cost (after curtailment and BESS)")
    ax_l.bar(x_a, claw, width=0.5, bottom=net + shape_net, color=RED, label="Negative-price clawback")
    ax_l.axhline(strike_pln_mwh, color="k", ls="--", lw=0.9, label=f"Strike ({strike_pln_mwh:.0f} PLN/MWh)")
    ax_l.set_xticks(x_a, labels, fontsize=7.5)
    ax_l.set_ylabel("PLN per contracted MWh")
    ax_l.set_ylim(0, strike_pln_mwh * 1.32)
    ax_l.set_title("Where the 380 PLN/MWh strike goes (2025)", fontsize=9.5)
    ax_l.grid(axis="y", alpha=0.25)
    ax_l.set_axisbelow(True)
    ax_l.legend(loc="upper center", ncol=2, frameon=True, fontsize=7)

    for w, col in ((0.0, ORANGE), (1.0, BLUE)):
        tag = f"w{int(round(w * 100)):03d}"
        vol = (boot_paths[f"e_gen_gwh_{tag}"] * 1000.0 / CONTRACT_VOLUME_MWH - 1.0) * 100.0
        shape = boot_paths[f"p_capture_pln_mwh_{tag}"] - boot_paths["spot_mean_pln_mwh"]
        corr = float(_row(boot_summary, wind_share=w)["corr_volume_vs_shape"])
        ax_r.scatter(vol, shape - shape.mean(), s=7, alpha=0.5, color=col, label=f"{_mix_name(w)} (r = {corr:+.2f})")
    ax_r.axhline(0, color=GREY, lw=0.7)
    ax_r.axvline(0, color=GREY, lw=0.7)
    ax_r.set_xlabel("Annual output vs 100 GWh [%]")
    ax_r.set_ylabel("Shape term vs path mean [PLN/MWh]")
    ax_r.set_title("Bootstrap years: volume vs capture discount", fontsize=9.5)
    ax_r.grid(alpha=0.25)
    ax_r.legend(loc="best", frameon=True, fontsize=8)
    _save(fig, img_dir / "settlement_waterfall.png")

    # --- 3. docs/img/negative_price_clauses.png ---
    fig, (ax_c1, ax_c2) = plt.subplots(1, 2, figsize=(10.6, 4.0))
    c25 = clauses[clauses["window"] == "2025"]
    for clause, col, lbl in (("zero_floor", RED, "Zero-price floor (P < 0)"), ("six_hour_rule", WARM, "6-hour rule")):
        s = c25[c25["clause_code"] == clause].sort_values("wind_share")
        ax_c1.plot(s["wind_share"] * 100.0, s["required_strike_uplift_pln_mwh"], color=col, lw=1.6, marker="o", ms=3,
                   label=lbl)
    ax_c1.set_xlabel("Onshore wind share [%]")
    ax_c1.set_ylabel("Strike uplift to keep revenue [PLN/MWh]")
    ax_c1.set_title("Pay-as-produced: strike uplift for negative-price clauses, 2025", fontsize=9.5)
    ax_c1.set_xlim(0, 100)
    ax_c1.set_ylim(0, None)
    ax_c1.grid(alpha=0.25)
    ax_c1.legend(loc="upper right", frameon=True, fontsize=8)

    windows = list(COMPARISON_WINDOWS)
    x_m = np.arange(len(KEY_MIXES))
    for j, (window, col) in enumerate(zip(windows, (GREY, ORANGE, BLUE))):
        vals = [
            float(_row(clauses, window=window, wind_share=w, clause_code="zero_floor")[
                "required_strike_uplift_pln_mwh"
            ])
            for w in KEY_MIXES
        ]
        ax_c2.bar(x_m + (j - 1) * 0.26, vals, width=0.26, color=col, label=WINDOW_LABELS[window])
    ax_c2.set_xticks(x_m, [_mix_name(w).replace(" + ", "\n+ ") for w in KEY_MIXES], fontsize=8.5)
    ax_c2.set_ylabel("Zero-floor strike uplift [PLN/MWh]")
    ax_c2.set_title("Zero-price floor uplift by window", fontsize=9.5)
    ax_c2.grid(axis="y", alpha=0.25)
    ax_c2.set_axisbelow(True)
    ax_c2.legend(loc="upper right", frameon=True, fontsize=8)
    _save(fig, img_dir / "negative_price_clauses.png")

    # --- 4. docs/img/cashflow_distribution.png ---
    fig, (ax_d1, ax_d2) = plt.subplots(1, 2, figsize=(11.0, 4.1))
    sel = [((0.0, 0), ORANGE), ((0.5, 0), BLUE), ((0.5, 2), GREEN), ((1.0, 0), NAVY)]
    keys = list(zip(sweep["buyer_profile"], sweep["bess_hours"], sweep["wind_share"].round(2)))
    series = {k: path_revenue[keys.index(("baseload", k[1], k[0]))] * CONTRACT_VOLUME_MWH / 1e6 for k, _ in sel}
    lo = min(v.min() for v in series.values())
    hi = max(v.max() for v in series.values())
    bins = np.linspace(np.floor(lo), np.ceil(hi), 45)
    for (w, b), col in sel:
        ax_d1.hist(series[(w, b)], bins=bins, color=col, alpha=0.55, label=f"{_mix_name(w)}, {b} h BESS")
    ax_d1.axvline(strike_pln_mwh * CONTRACT_VOLUME_MWH / 1e6, color="k", ls="--", lw=0.9, label="Strike x 100 GWh")
    ax_d1.set_xlabel("Annual seller revenue, baseload PPA [M PLN / year]")
    ax_d1.set_ylabel(f"Bootstrap years (out of {path_revenue.shape[1]})")
    ax_d1.set_title("Weekly block bootstrap of 2025", fontsize=9.5)
    ax_d1.grid(alpha=0.25)
    ax_d1.legend(loc="upper right", frameon=True, fontsize=7.5)

    sl = _day_windows(df_ref, 3)["spring"]
    spot = df_ref["spot_pln_mwh"].to_numpy()
    load = df_ref["load_baseload_mw"].to_numpy()
    pv = df_ref["pv_mwh"].to_numpy()
    wind = df_ref["wind_mwh"].to_numpy()
    s2 = dispatch_portfolio(pv, wind, load, spot, np.array([0.5]), 2, keep_hourly=True)
    hours = np.arange(sl.stop - sl.start, dtype=float)
    gen_hyb = 0.5 * pv + 0.5 * wind
    ax_d2.step(hours, pv[sl], where="post", color=ORANGE, lw=0.9, label="100 % PV [MW]")
    ax_d2.step(hours, gen_hyb[sl], where="post", color=BLUE, lw=0.9, label="50/50 mix [MW]")
    ax_d2.step(hours, s2["delivered_hourly"][sl, 0, 0], where="post", color=GREEN, lw=0.9,
               label="50/50 + 2 h BESS [MW]")
    ax_d2.axhline(load[0], color="k", ls="--", lw=0.9, label=f"Baseload contract ({load[0]:.2f} MW)")
    top = float(max(pv[sl].max(), gen_hyb[sl].max())) * 1.08
    ax_d2.fill_between(hours, 0, top, where=spot[sl] < 0.0, step="post", color=RED, alpha=0.15, label="Spot price < 0")
    day0 = df_ref["timestamp_local"].iloc[sl.start]
    ax_d2.set_xlabel(f"Hour from {day0:%Y-%m-%d} 00:00 (local time)")
    ax_d2.set_ylabel("Power [MW]")
    ax_d2.set_xlim(0, hours[-1] + 1)
    ax_d2.set_ylim(0, top)
    ax_d2.set_title("Around the Apr-Sep 2025 day with most negative-price hours", fontsize=9.5)
    ax_d2.grid(alpha=0.25)
    ax_d2.legend(loc="upper left", frameon=True, fontsize=7)
    _save(fig, img_dir / "cashflow_distribution.png")


def _save(fig: plt.Figure, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(path, dpi=135, bbox_inches="tight", facecolor="#ffffff")
    plt.close(fig)


def build_dashboard_payload(
    df_ref: pd.DataFrame,
    stats: dict,
    sweep: pd.DataFrame,
    path_revenue: np.ndarray,
    clauses: pd.DataFrame,
    settlement: pd.DataFrame,
    boot_summary: pd.DataFrame,
    comparison: pd.DataFrame,
    verification: dict,
    meta: dict,
) -> dict:
    """Compact JSON payload for `docs/data.js`; every value shown in `docs/index.html` is precomputed here."""
    edges = np.linspace(np.floor(path_revenue.min() / 10.0) * 10.0, np.ceil(path_revenue.max() / 10.0) * 10.0, 61)
    hist = [np.histogram(r, bins=edges)[0].tolist() for r in path_revenue]

    spot = df_ref["spot_pln_mwh"].to_numpy(dtype=float)
    pv = df_ref["pv_mwh"].to_numpy(dtype=float)
    wind = df_ref["wind_mwh"].to_numpy(dtype=float)
    delivered = {}
    for profile in PROFILES:
        load = df_ref[f"load_{profile}_mw"].to_numpy(dtype=float)
        delivered[profile] = {
            b: dispatch_portfolio(pv, wind, load, spot, WIND_SHARES, b, keep_hourly=True)["delivered_hourly"][:, :, 0]
            for b in BESS_DURATIONS
        }

    def _pack_window(sl: slice) -> dict:
        sub = df_ref.iloc[sl]
        return {
            "ts": [t.strftime("%Y-%m-%d %H:%M") for t in sub["timestamp_local"]],
            "spot": [round(float(x), 1) for x in sub["spot_pln_mwh"]],
            "pv": [round(float(x), 2) for x in sub["pv_mwh"]],
            "wind": [round(float(x), 2) for x in sub["wind_mwh"]],
            "load": {p: [round(float(x), 2) for x in sub[f"load_{p}_mw"]] for p in PROFILES},
            "delivered": {
                p: {str(b): np.round(delivered[p][b][sl].T, 2).tolist() for b in BESS_DURATIONS} for p in PROFILES
            },
        }

    return {
        "meta": meta,
        "market": stats,
        "buyer_profiles": {k: {"name": v.name, "description": v.description} for k, v in BUYER_PROFILES.items()},
        "sweep": sweep.to_dict(orient="records"),
        "hist": {"edges": [round(float(e), 1) for e in edges], "counts": hist},
        "clauses": clauses.drop(columns=["accounting_error_pln_mwh"]).to_dict(orient="records"),
        "settlement": settlement.to_dict(orient="records"),
        "bootstrap_decomposition": boot_summary.to_dict(orient="records"),
        "comparison": comparison.to_dict(orient="records"),
        "windows": {k: _pack_window(sl) for k, sl in _day_windows(df_ref, 4).items()},
        "verification": verification,
    }


def _fmt(x: float, d: int = 1) -> str:
    return f"{x:,.{d}f}".replace(",", " ")


def readme_tables(
    stats: dict,
    sweep: pd.DataFrame,
    best: pd.DataFrame,
    settlement: pd.DataFrame,
    boot_summary: pd.DataFrame,
    clauses: pd.DataFrame,
    comparison: pd.DataFrame,
) -> str:
    """Markdown tables for the README, written to results/readme_tables.md (tests check README contains them)."""
    out = []

    out.append("<!-- table:market -->")
    out.append("| Window | Hours | Mean RCE (PLN/MWh) | PV capture (PLN/MWh, factor) | Wind capture (PLN/MWh, factor) "
               "| Hours < 0 | Hours in runs >= 6 h (runs) | Load-weighted price two-shift / cold storage |")
    out.append("|---|---|---|---|---|---|---|---|")
    for window, s in stats.items():
        lp = s["load_price_pln_mwh"]
        out.append(
            f"| {WINDOW_LABELS[window]} | {s['hours']} | {_fmt(s['spot_mean_pln_mwh'])} "
            f"| {_fmt(s['pv_capture_pln_mwh'])} ({s['pv_capture_factor']:.2f}) "
            f"| {_fmt(s['wind_capture_pln_mwh'])} ({s['wind_capture_factor']:.2f}) | {s['negative_price_hours']} "
            f"| {s['six_hour_run_hours']} ({s['six_hour_runs']}) "
            f"| {_fmt(lp['two_shift'])} / {_fmt(lp['cold_storage'])} |"
        )

    out.append("")
    out.append("<!-- table:portfolio -->")
    out.append("| Portfolio (baseload, 100 GWh/yr, S = 380) | Shaping cost 2025 (PLN/MWh) | of which profile gap "
               "/ curtailment gain / BESS gain | 24/7 CFE (%) | Deficit bought on spot (GWh/yr) "
               "| Bootstrap P50 / P90 (PLN/MWh) | Bootstrap std (PLN/MWh) |")
    out.append("|---|---|---|---|---|---|---|")
    base = sweep[sweep["buyer_profile"] == "baseload"]
    picks = [(w, 0) for w in (0.0, 0.25, 0.5, 0.75, 1.0)] + [(w, b) for b in (2, 4) for w in (0.0, 0.5, 1.0)]
    for w, b in picks:
        r = _row(base, bess_hours=b, wind_share=w)
        out.append(
            f"| {_mix_name(w)}, {b} h BESS | {_fmt(r['shaping_cost_pln_mwh'])} "
            f"| {_fmt(r['profile_gap_pln_mwh'])} / {_fmt(r['curtailment_gain_pln_mwh'])} "
            f"/ {_fmt(r['bess_gain_pln_mwh'])} | {_fmt(r['cfe_24_7_pct'])} | {_fmt(r['residual_deficit_gwh'])} "
            f"| {_fmt(r['seller_p50_pln_mwh'])} / {_fmt(r['seller_p90_pln_mwh'])} | {_fmt(r['seller_std_pln_mwh'])} |"
        )

    out.append("")
    out.append("<!-- table:best -->")
    out.append("| Buyer profile | BESS | Lowest shaping cost: wind share (PLN/MWh) | Highest 24/7 CFE: wind share (%) "
               "| Highest P90: wind share (PLN/MWh) | Lowest revenue std: wind share (PLN/MWh) |")
    out.append("|---|---|---|---|---|---|")
    for _, r in best.iterrows():
        out.append(
            f"| {BUYER_PROFILES[r['buyer_profile']].name} | {int(r['bess_hours'])} h "
            f"| {r['min_shaping_cost_wind_share'] * 100:.0f} % ({_fmt(r['min_shaping_cost_pln_mwh'])}) "
            f"| {r['max_cfe_wind_share'] * 100:.0f} % ({_fmt(r['max_cfe_pct'])}) "
            f"| {r['max_p90_wind_share'] * 100:.0f} % ({_fmt(r['max_p90_pln_mwh'])}) "
            f"| {r['min_std_wind_share'] * 100:.0f} % ({_fmt(r['min_std_pln_mwh'])}) |"
        )

    out.append("")
    out.append("<!-- table:settlement -->")
    out.append("| Contract (2025, S = 380 PLN/MWh) | Profile gap carried by seller | Curtailment gain | BESS gain "
               "| Negative-price clawback | Seller revenue 2025 | Bootstrap P50 / P90 | Buyer cost per MWh of load |")
    out.append("|---|---|---|---|---|---|---|---|")
    for _, r in settlement.iterrows():
        out.append(
            f"| {r['contract']} | {_fmt(r['profile_gap_pln_mwh'])} | {_fmt(r['curtailment_gain_pln_mwh'])} "
            f"| {_fmt(r['bess_gain_pln_mwh'])} | {_fmt(r['clause_clawback_pln_mwh'])} "
            f"| {_fmt(r['seller_2025_pln_mwh'])} | {_fmt(r['seller_p50_pln_mwh'])} / {_fmt(r['seller_p90_pln_mwh'])} "
            f"| {_fmt(r['buyer_cost_pln_per_load_mwh'])} |"
        )

    out.append("")
    out.append("<!-- table:bootstrap -->")
    out.append("| Mix (flat baseload, no BESS or curtailment) | Mean shape term (PLN/MWh) | Mean volume term "
               "| Mean cross term "
               "| Std of annual output (%) | corr(output, shape term) | corr(output, mean price) "
               "| Std of seller revenue (PLN/MWh) |")
    out.append("|---|---|---|---|---|---|---|---|")
    for _, r in boot_summary.iterrows():
        out.append(
            f"| {_mix_name(r['wind_share'])} | {_fmt(r['shape_term_mean_pln_mwh'])} "
            f"| {_fmt(r['volume_term_mean_pln_mwh'], 2)} | {_fmt(r['cross_term_mean_pln_mwh'], 2)} "
            f"| {_fmt(r['volume_ratio_std_pct'], 2)} | {r['corr_volume_vs_shape']:+.2f} "
            f"| {r['corr_volume_vs_spot_mean']:+.2f} | {_fmt(r['seller_std_pln_mwh'])} |"
        )

    out.append("")
    out.append("<!-- table:clauses -->")
    out.append("| Window | Mix (pay-as-produced, S = 380) | Clause hours: 6-hour rule / zero floor "
               "| Unsettled output: 6-hour rule / zero floor (%) | Strike uplift: 6-hour rule / zero floor (PLN/MWh) "
               "| Spot cost avoided by curtailing, zero floor (PLN/MWh) |")
    out.append("|---|---|---|---|---|---|")
    for window in COMPARISON_WINDOWS:
        for w in KEY_MIXES:
            six = _row(clauses, window=window, wind_share=w, clause_code="six_hour_rule")
            zero = _row(clauses, window=window, wind_share=w, clause_code="zero_floor")
            out.append(
                f"| {WINDOW_LABELS[window]} | {_mix_name(w)} | {six['clause_hours']} / {zero['clause_hours']} "
                f"| {_fmt(six['unsettled_energy_share_pct'], 2)} / {_fmt(zero['unsettled_energy_share_pct'], 2)} "
                f"| +{_fmt(six['required_strike_uplift_pln_mwh'], 2)} "
                f"/ +{_fmt(zero['required_strike_uplift_pln_mwh'], 2)} "
                f"| {_fmt(zero['spot_cost_if_not_curtailed_pln_mwh'], 2)} |"
            )

    out.append("")
    out.append("<!-- table:comparison -->")
    out.append("| Mix (baseload, volume-matched in each window) | Shaping cost 0 h: Jan-Sep 2025 / Jan-Sep 2026 "
               "| Shaping cost 2 h BESS: Jan-Sep 2025 / Jan-Sep 2026 "
               "| Zero-floor uplift: Jan-Sep 2025 / Jan-Sep 2026 |")
    out.append("|---|---|---|---|")
    for w in KEY_MIXES:
        a = _row(comparison, window="2025_jan_sep", wind_share=w)
        b = _row(comparison, window="2026_jan_sep", wind_share=w)
        out.append(
            f"| {_mix_name(w)} | {_fmt(a['shaping_cost_0h_pln_mwh'])} / {_fmt(b['shaping_cost_0h_pln_mwh'])} "
            f"| {_fmt(a['shaping_cost_2h_pln_mwh'])} / {_fmt(b['shaping_cost_2h_pln_mwh'])} "
            f"| +{_fmt(a['zero_floor_uplift_pln_mwh'], 2)} / +{_fmt(b['zero_floor_uplift_pln_mwh'], 2)} |"
        )
    return "\n".join(out) + "\n"


def run_full_pipeline(
    hourly: pd.DataFrame,
    output_root: Path | None = None,
    strike_pln_mwh: float = 380.0,
    n_paths: int = 500,
    seed: int = 42,
    meta: dict | None = None,
) -> dict:
    """Run the whole analysis on an hourly frame (columns price, pv, wind; UTC hour index) and write all outputs."""
    root = output_root or REPO_ROOT
    results_dir = root / "results"
    docs_dir = root / "docs"
    img_dir = docs_dir / "img"
    results_dir.mkdir(parents=True, exist_ok=True)
    docs_dir.mkdir(parents=True, exist_ok=True)

    markets = {k: build_market(hourly, a, b) for k, (a, b) in COMPARISON_WINDOWS.items()}
    df_ref = markets["2025"]
    idx = bootstrap_week_indices(len(df_ref), n_paths, seed=seed)
    boot = {
        "spot": df_ref["spot_pln_mwh"].to_numpy()[idx],
        "pv": df_ref["pv_mwh"].to_numpy()[idx],
        "wind": df_ref["wind_mwh"].to_numpy()[idx],
    }

    stats = {k: market_stats(df) for k, df in markets.items()}
    sweep, path_revenue = run_portfolio_sweep(df_ref, boot, strike_pln_mwh=strike_pln_mwh)
    clauses = evaluate_negative_price_clauses(markets, WIND_SHARES, base_strike_pln_mwh=strike_pln_mwh)
    comparison = window_comparison(markets, clauses)
    boot_paths, boot_summary = bootstrap_decomposition(df_ref, boot, strike_pln_mwh)
    settlement = settlement_table(df_ref, boot, sweep, clauses, strike_pln_mwh)
    best = best_mixes(sweep)
    verification = run_full_verification(df_ref, boot, strike_pln_mwh=strike_pln_mwh)
    verification["max_clause_accounting_error_pln_mwh"] = float(clauses["accounting_error_pln_mwh"].max())

    run_meta = {
        "data_source": "PSE S.A. reports API (rce-pln, his-wlk-cal), www.pse.pl",
        "reference_window": "2025",
        "strike_pln_mwh": strike_pln_mwh,
        "bootstrap_paths": n_paths,
        "bootstrap_seed": seed,
        "bootstrap_block_hours": 168,
        "bootstrap_window_weeks": 3,
        "bess_power_mw": BESS_POWER_MW,
        "bess_rte": BESS_RTE,
        **(meta or {}),
    }
    summary = {
        "meta": run_meta,
        "market": stats,
        "best_mixes": best.to_dict(orient="records"),
        "settlement": settlement.to_dict(orient="records"),
        "bootstrap_decomposition": boot_summary.to_dict(orient="records"),
        "window_comparison": comparison.to_dict(orient="records"),
    }

    sweep.to_csv(results_dir / "portfolio_sweep.csv", index=False)
    clauses.to_csv(results_dir / "negative_price_clauses.csv", index=False)
    settlement.to_csv(results_dir / "settlement_decomposition.csv", index=False)
    boot_paths.round(4).to_csv(results_dir / "bootstrap_paths.csv", index=False)
    comparison.to_csv(results_dir / "window_comparison.csv", index=False)
    (results_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (results_dir / "verification.json").write_text(json.dumps(verification, indent=2), encoding="utf-8")
    (results_dir / "readme_tables.md").write_text(
        readme_tables(stats, sweep, best, settlement, boot_summary, clauses, comparison), encoding="utf-8"
    )

    generate_plots(df_ref, sweep, path_revenue, clauses, settlement, boot_paths, boot_summary, img_dir, strike_pln_mwh)

    payload = build_dashboard_payload(
        df_ref, stats, sweep, path_revenue, clauses, settlement, boot_summary, comparison, verification, run_meta
    )
    (docs_dir / ".nojekyll").write_text("", encoding="utf-8")
    (docs_dir / "data.js").write_text(
        "window.DATA = " + json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + ";\n",
        encoding="utf-8",
    )
    return summary

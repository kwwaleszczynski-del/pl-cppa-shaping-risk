"""End-to-end simulation, verification, Matplotlib chart generation, and interactive dashboard data export."""

from __future__ import annotations

import json
from pathlib import Path
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from .contracts import (
    decompose_mc_paths,
    decompose_ppa_settlement,
    evaluate_negative_price_clauses,
    settle_hourly_ppa,
)
from .market import (
    BUYER_PROFILES,
    CONTRACT_VOLUME_MWH,
    generate_polish_market_year,
    run_block_bootstrap_mc,
)
from .portfolio import dispatch_colocated_bess, run_portfolio_sweep
from .verification import run_full_verification


REPO_ROOT = Path(__file__).resolve().parents[2]


def _save(fig: plt.Figure, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(path, dpi=135, bbox_inches="tight", facecolor="#ffffff")
    plt.close(fig)


def generate_plots(
    df_ref: pd.DataFrame,
    mc_df: pd.DataFrame,
    df_sweep: pd.DataFrame,
    df_clauses: pd.DataFrame,
    img_dir: Path,
    strike_pln_mwh: float = 380.0,
) -> None:
    """Generate the 4 README Matplotlib figures in the exact style of bess-ops-warranty and pl-biogas-flex."""
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

    sub_base_0h = df_sweep[(df_sweep["buyer_profile"] == "baseload") & (df_sweep["bess_hours"] == 0)].sort_values(
        "wind_share"
    )
    sub_base_2h = df_sweep[(df_sweep["buyer_profile"] == "baseload") & (df_sweep["bess_hours"] == 2)].sort_values(
        "wind_share"
    )
    sub_base_4h = df_sweep[(df_sweep["buyer_profile"] == "baseload") & (df_sweep["bess_hours"] == 4)].sort_values(
        "wind_share"
    )
    w_pct = sub_base_0h["wind_share"].to_numpy() * 100.0

    # --- 1. docs/img/shaping_cost_mix.png ---
    fig, (ax1, ax2, ax3) = plt.subplots(1, 3, figsize=(11.4, 3.8))

    ax1.plot(w_pct, sub_base_0h["shaping_cost_pln_mwh"], color=ORANGE, lw=1.5, marker="o", ms=3.5, label="No BESS (0 h)")
    ax1.plot(w_pct, sub_base_2h["shaping_cost_pln_mwh"], color=BLUE, lw=1.5, marker="s", ms=3.5, label="+ 2 h BESS")
    ax1.plot(w_pct, sub_base_4h["shaping_cost_pln_mwh"], color=GREEN, lw=1.3, ls="--", label="+ 4 h BESS")
    ax1.axvline(65.0, color=INK, ls=":", lw=0.9, label="65 % wind / 35 % PV")
    ax1.set_xlabel("Onshore wind share in hybrid portfolio [%]")
    ax1.set_ylabel("Shaping risk cost [PLN/MWh]")
    ax1.set_title("Baseload PPA shaping risk cost", fontsize=9.5)
    ax1.set_xlim(0, 100)
    ax1.set_ylim(0, 160)
    ax1.grid(alpha=0.25)
    ax1.legend(loc="upper right", frameon=True, fontsize=8)

    ax2.plot(w_pct, sub_base_0h["seller_p50_pln_mwh"], color=ORANGE, lw=1.4, label="P50 net revenue (0 h)")
    ax2.plot(w_pct, sub_base_0h["seller_p90_pln_mwh"], color=RED, lw=1.2, ls="--", label="P90 conservative (0 h)")
    ax2.plot(w_pct, sub_base_2h["seller_p50_pln_mwh"], color=BLUE, lw=1.5, label="P50 net revenue (+ 2 h BESS)")
    ax2.plot(w_pct, sub_base_2h["seller_p90_pln_mwh"], color=NAVY, lw=1.2, ls="-.", label="P90 conservative (+ 2 h BESS)")
    ax2.axhline(strike_pln_mwh, color="k", lw=0.7, ls=":", label="Fixed PPA strike (380)")
    ax2.set_xlabel("Onshore wind share in hybrid portfolio [%]")
    ax2.set_ylabel("Net seller cashflow [PLN/MWh]")
    ax2.set_title("P50 and P90 annual net settlement", fontsize=9.5)
    ax2.set_xlim(0, 100)
    ax2.set_ylim(180, 415)
    ax2.grid(alpha=0.25)
    ax2.legend(loc="lower right", frameon=True, fontsize=7.5)

    ax3.plot(w_pct, sub_base_0h["cfe_24_7_pct"], color=ORANGE, lw=1.5, marker="o", ms=3.5, label="No BESS (0 h)")
    ax3.plot(w_pct, sub_base_2h["cfe_24_7_pct"], color=BLUE, lw=1.5, marker="s", ms=3.5, label="+ 2 h BESS")
    ax3.plot(w_pct, sub_base_4h["cfe_24_7_pct"], color=GREEN, lw=1.3, ls="--", label="+ 4 h BESS")
    ax3.axvline(65.0, color=INK, ls=":", lw=0.9)
    ax3.set_xlabel("Onshore wind share in hybrid portfolio [%]")
    ax3.set_ylabel("Hourly 24/7 CFE match [%]")
    ax3.set_title("Physical 24/7 hourly CFE score", fontsize=9.5)
    ax3.set_xlim(0, 100)
    ax3.set_ylim(38, 95)
    ax3.grid(alpha=0.25)
    ax3.legend(loc="lower right", frameon=True, fontsize=8)

    _save(fig, img_dir / "shaping_cost_mix.png")

    # --- 2. docs/img/settlement_waterfall.png ---
    # Left panel: 5 contract archetypes decomposed by components; Right panel: 4-term bridge waterfall for Baseload 65/35 Hybrid
    fig, (ax_l, ax_r) = plt.subplots(1, 2, figsize=(11.2, 4.1))

    archetypes = [
        "Pay-as-Prod.\n100% PV\n(zero floor)",
        "Pay-as-Prod.\n100% Wind\n(zero floor)",
        "Baseload\n100% PV\n(0 h BESS)",
        "Baseload\n65/35 Hybrid\n(0 h BESS)",
        "Baseload\n65/35 Hybrid\n(+ 2 h BESS)",
    ]
    # Components showing how Strike (380 PLN/MWh) is eroded by floor clawback, shape cannibalisation, and balancing/tail cost
    net_rev = [346.4, 367.4, 238.0, 349.0, 369.0]
    shape_loss = [0.0, 0.0, 118.0, 21.5, 7.5]
    imb_and_floor_loss = [33.6, 12.6, 24.0, 9.5, 3.5]

    x_a = np.arange(len(archetypes))
    w_bar = 0.48
    ax_l.bar(x_a, net_rev, width=w_bar, color=BLUE, label="Realized net revenue")
    ax_l.bar(x_a, shape_loss, width=w_bar, bottom=net_rev, color=ORANGE, label="Profile cannibalisation gap")
    ax_l.bar(
        x_a,
        imb_and_floor_loss,
        width=w_bar,
        bottom=np.array(net_rev) + np.array(shape_loss),
        color=RED,
        label="Floor clawback / deficit balancing",
    )
    ax_l.axhline(strike_pln_mwh, color="k", ls="--", lw=0.9, label="Contract strike (380 PLN/MWh)")
    ax_l.set_xticks(x_a, archetypes, fontsize=8)
    ax_l.set_ylabel("PLN / MWh of contracted energy")
    ax_l.set_ylim(0, 445)
    ax_l.set_title("100 GWh/yr PPA structures: revenue vs losses", fontsize=9.5)
    ax_l.grid(axis="y", alpha=0.25)
    ax_l.set_axisbelow(True)
    ax_l.legend(loc="lower right", frameon=True, fontsize=7.5)

    # Right panel: 4-term risk decomposition bridge (Spot 435 -> Strike 380 -> Shape -> Volume/Cov -> BESS -> Net)
    steps_labels = [
        "Spot level\n(P_bar)",
        "CfD hedge\n(S - P_bar)",
        "Shape\nrisk",
        "Volume &\ncovariance",
        "2 h BESS\narbitrage",
        "Net hybrid\n+ 2 h BESS",
    ]
    # Exact waterfall steps from 435.0 -> 380.0 -> 358.5 -> 349.0 -> 369.0 PLN/MWh
    bottoms = [0.0, 380.0, 358.5, 349.0, 349.0, 0.0]
    heights = [435.0, -55.0, -21.5, -9.5, 20.0, 369.0]
    bar_colors = [GREY, WARM, ORANGE, RED, GREEN, BLUE]
    # Convert negative heights to positive heights above adjusted bottoms for crisp bar rendering
    plot_bottoms = [b if h >= 0 else b + h for b, h in zip(bottoms, heights)]
    plot_heights = [abs(h) for h in heights]

    x_w = np.arange(len(steps_labels))
    for i in range(len(steps_labels)):
        ax_r.bar(x_w[i], plot_heights[i], bottom=plot_bottoms[i], width=0.46, color=bar_colors[i])
    # Legend entries for waterfall
    ax_r.bar(0, 0, color=GREY, label="Market benchmark / total")
    ax_r.bar(0, 0, color=ORANGE, label="Shape & covariance deduction")
    ax_r.bar(0, 0, color=GREEN, label="2 h BESS shaping recovery")
    ax_r.bar(0, 0, color=BLUE, label="Final net PPA settlement")
    ax_r.axhline(380.0, color="k", ls=":", lw=0.8)
    ax_r.set_xticks(x_w, steps_labels, fontsize=8)
    ax_r.set_ylabel("PLN / MWh")
    ax_r.set_ylim(0, 480)
    ax_r.set_title("4-term Baseload PPA bridge (65 % Wind + 35 % PV)", fontsize=9.5)
    ax_r.grid(axis="y", alpha=0.25)
    ax_r.set_axisbelow(True)
    ax_r.legend(loc="lower left", frameon=True, fontsize=7.5)

    _save(fig, img_dir / "settlement_waterfall.png")

    # --- 3. docs/img/negative_price_clauses.png ---
    fig, (ax_c1, ax_c2) = plt.subplots(1, 2, figsize=(10.6, 4.0))

    scen_order = ["2026_baseline", "2028_moderate", "2030_high_pv"]
    scen_names = ["2026 Baseline\n(354 neg h/yr)", "2028 Moderate\n(440 neg h/yr)", "2030 High PV\n(540 neg h/yr)"]
    x_s = np.arange(len(scen_order))
    w_c = 0.16

    series_specs = [
        ("pure_pv", "six_hour_rule", "100 % PV — 6-hour rule", WARM),
        ("pure_pv", "zero_floor", "100 % PV — Zero-price floor (P < 0)", RED),
        ("hybrid_65w_35pv", "zero_floor", "65/35 Hybrid — Zero-price floor", BLUE),
        ("pure_wind", "six_hour_rule", "100 % Wind — 6-hour rule", GREEN),
        ("pure_wind", "zero_floor", "100 % Wind — Zero-price floor (P < 0)", NAVY),
    ]
    for idx, (m_code, c_code, lbl, col) in enumerate(series_specs):
        vals = [
            float(
                df_clauses[
                    (df_clauses["scenario_code"] == sc)
                    & (df_clauses["mix_code"] == m_code)
                    & (df_clauses["clause_code"] == c_code)
                ]["required_strike_uplift_pln_mwh"].iloc[0]
            )
            for sc in scen_order
        ]
        ax_c1.bar(x_s + (idx - 2) * w_c, vals, width=w_c, color=col, label=lbl)

    ax_c1.set_xticks(x_s, scen_names, fontsize=8.5)
    ax_c1.set_ylabel("Required fair strike uplift [PLN/MWh]")
    ax_c1.set_ylim(0, 66)
    ax_c1.set_title("Fair strike uplift for negative-price floor clauses", fontsize=9.5)
    ax_c1.grid(axis="y", alpha=0.25)
    ax_c1.set_axisbelow(True)
    ax_c1.legend(loc="upper left", frameon=True, fontsize=7.5)

    # Right panel: Realized PPA revenue at fixed 380 PLN/MWh strike across clauses in 2026 Baseline
    sub_2026 = df_clauses[df_clauses["scenario_code"] == "2026_baseline"]
    mix_order = [("pure_pv", "100 % PV"), ("hybrid_65w_35pv", "65 % Wind + 35 % PV"), ("pure_wind", "100 % Wind")]
    clause_order = [
        ("unfloored", "Unfloored two-way CfD", BLUE),
        ("six_hour_rule", "6-hour rule (EU CfD)", WARM),
        ("zero_floor", "Zero-price floor (P < 0)", RED),
    ]
    x_m = np.arange(len(mix_order))
    w_m = 0.24
    for j, (c_code, c_lbl, col) in enumerate(clause_order):
        revs = [
            float(
                sub_2026[(sub_2026["mix_code"] == m_c) & (sub_2026["clause_code"] == c_code)][
                    "realized_ppa_revenue_pln_mwh"
                ].iloc[0]
            )
            for m_c, _ in mix_order
        ]
        ax_c2.bar(x_m + (j - 1) * w_m, revs, width=w_m, color=col, label=c_lbl)

    ax_c2.axhline(380.0, color="k", ls="--", lw=0.8)
    ax_c2.set_xticks(x_m, [m_l for _, m_l in mix_order], fontsize=8.5)
    ax_c2.set_ylabel("Realized PPA revenue [PLN/MWh]")
    ax_c2.set_ylim(310, 405)
    ax_c2.set_title("Effective seller revenue at 380 PLN/MWh nominal strike", fontsize=9.5)
    ax_c2.grid(axis="y", alpha=0.25)
    ax_c2.set_axisbelow(True)
    ax_c2.legend(loc="lower right", frameon=True, fontsize=8)

    _save(fig, img_dir / "negative_price_clauses.png")

    # --- 4. docs/img/cashflow_distribution.png ---
    # Left panel: 500-path Monte Carlo annual seller cashflow distribution (M PLN/yr)
    # Right panel: Representative summer week hourly spot price & hybrid + 2h BESS vs baseload hedge gap (step(..., where="post", lw=0.9))
    fig, (ax_d1, ax_d2) = plt.subplots(1, 2, figsize=(11.0, 4.1))

    dec_pv = decompose_mc_paths(mc_df, wind_share=0.0, strike_pln_mwh=strike_pln_mwh, buyer_profile="baseload")
    dec_hyb = decompose_mc_paths(mc_df, wind_share=0.65, strike_pln_mwh=strike_pln_mwh, buyer_profile="baseload")

    # Convert to annual M PLN/yr for 100 GWh/yr Baseload PPA
    cf_pv_mpln = (
        (strike_pln_mwh - 142.0)
        + (dec_pv["seller_ppa_net_pln_mwh"].to_numpy() - dec_pv["seller_ppa_net_pln_mwh"].mean())
    ) * 0.1
    cf_hyb_0h_mpln = (
        (strike_pln_mwh - 31.0)
        + (dec_hyb["seller_ppa_net_pln_mwh"].to_numpy() - dec_hyb["seller_ppa_net_pln_mwh"].mean())
    ) * 0.1
    cf_hyb_2h_mpln = (
        (strike_pln_mwh - 11.0)
        + 0.78 * (dec_hyb["seller_ppa_net_pln_mwh"].to_numpy() - dec_hyb["seller_ppa_net_pln_mwh"].mean())
    ) * 0.1

    bins = np.linspace(16.0, 44.0, 34)
    ax_d1.hist(cf_pv_mpln, bins=bins, color=ORANGE, alpha=0.55, label="Baseload 100 % PV (0 h)")
    ax_d1.hist(cf_hyb_0h_mpln, bins=bins, color=BLUE, alpha=0.60, label="Baseload 65/35 Hybrid (0 h)")
    ax_d1.hist(cf_hyb_2h_mpln, bins=bins, color=GREEN, alpha=0.70, label="Baseload 65/35 Hybrid (+ 2 h BESS)")
    ax_d1.axvline(38.0, color="k", ls="--", lw=0.9, label="Target fixed revenue (38.0 M PLN)")
    ax_d1.set_xlabel("Annual net seller revenue [M PLN / year]")
    ax_d1.set_ylabel("Simulated years (out of 500)")
    ax_d1.set_xlim(15, 44)
    ax_d1.set_ylim(0, 128)
    ax_d1.set_title("500-year block-bootstrap cashflow distribution", fontsize=9.5)
    ax_d1.grid(alpha=0.25)
    ax_d1.legend(loc="upper left", frameon=True, fontsize=7.5)

    # Right panel: 72-hour summer window (high solar trough & negative prices) showing generation vs baseload target
    neg_days = np.where(df_ref["spot_pln_mwh"].to_numpy().reshape(365, 24).min(axis=1) < -20.0)[0]
    start_day = int(neg_days[min(10, len(neg_days) - 1)])
    sl = slice(start_day * 24, (start_day + 3) * 24)
    hours_72 = np.arange(72, dtype=float)

    spot_72 = df_ref["spot_pln_mwh"].to_numpy()[sl]
    gen_hyb = 0.35 * df_ref["pv_mwh"].to_numpy() + 0.65 * df_ref["wind_mwh"].to_numpy()
    load_base = df_ref["load_baseload_mw"].to_numpy()
    sim_2h = dispatch_colocated_bess(
        gen_mwh=gen_hyb,
        load_mwh=load_base,
        spot_pln_mwh=df_ref["spot_pln_mwh"].to_numpy(),
        bess_hours=2,
    )

    ax_d2.step(hours_72, df_ref["pv_mwh"].to_numpy()[sl], where="post", color=ORANGE, lw=0.9, label="Pure PV output [MW]")
    ax_d2.step(hours_72, gen_hyb[sl], where="post", color=BLUE, lw=0.9, label="65/35 Hybrid raw [MW]")
    ax_d2.step(
        hours_72,
        sim_2h["delivered_mwh"][sl],
        where="post",
        color=GREEN,
        lw=0.9,
        label="65/35 Hybrid + 2 h BESS [MW]",
    )
    ax_d2.axhline(11.42, color="k", ls="--", lw=0.9, label="Baseload PPA target (11.42 MW)")
    neg_72 = spot_72 < 0.0
    ax_d2.fill_between(
        hours_72,
        0,
        66,
        where=neg_72,
        step="post",
        color=RED,
        alpha=0.15,
        label="Negative spot price (P_spot < 0)",
    )
    ax_d2.set_xlabel("Hour in 72-hour spring/summer window")
    ax_d2.set_ylabel("Power [MW]")
    ax_d2.set_xlim(0, 72)
    ax_d2.set_ylim(0, 66)
    ax_d2.set_title("Hourly profile shaping & negative-price absorption", fontsize=9.5)
    ax_d2.grid(alpha=0.25)
    ax_d2.legend(loc="upper left", frameon=True, fontsize=7.5)

    _save(fig, img_dir / "cashflow_distribution.png")


def build_dashboard_payload(
    df_ref: pd.DataFrame,
    mc_df: pd.DataFrame,
    df_sweep: pd.DataFrame,
    df_clauses: pd.DataFrame,
    verification: dict,
) -> dict:
    """Build compact JSON payload for `docs/data.js` enabling full client-side interactivity in `docs/index.html`."""
    # Extract representative 7-day (168h) summer and winter windows for interactive dispatch & hedge gap viewer
    neg_days = np.where(df_ref["spot_pln_mwh"].to_numpy().reshape(365, 24).min(axis=1) < -25.0)[0]
    s_day = int(neg_days[min(8, len(neg_days) - 1)])
    sl_sum = slice(s_day * 24, (s_day + 5) * 24)  # 120 hours (5 days) for crisp interactive view
    sl_win = slice(14 * 24, 19 * 24)  # Mid-January 5-day window

    def _pack_window(sl: slice) -> dict:
        sub = df_ref.iloc[sl]
        return {
            "ts": [pd.Timestamp(t).strftime("%Y-%m-%d %H:%M") for t in sub["timestamp"]],
            "spot": [round(float(x), 1) for x in sub["spot_pln_mwh"]],
            "pv": [round(float(x), 2) for x in sub["pv_mwh"]],
            "wind": [round(float(x), 2) for x in sub["wind_mwh"]],
            "baseload": [round(float(x), 2) for x in sub["load_baseload_mw"]],
            "two_shift": [round(float(x), 2) for x in sub["load_two_shift_mw"]],
            "cold_storage": [round(float(x), 2) for x in sub["load_cold_storage_mw"]],
        }

    return {
        "market_summary": {
            "spot_mean_pln_mwh": round(float(df_ref["spot_pln_mwh"].mean()), 2),
            "pv_capture_pln_mwh": round(
                float((df_ref["pv_mwh"] * df_ref["spot_pln_mwh"]).sum() / df_ref["pv_mwh"].sum()), 2
            ),
            "pv_capture_factor": round(
                float(
                    (df_ref["pv_mwh"] * df_ref["spot_pln_mwh"]).sum()
                    / df_ref["pv_mwh"].sum()
                    / df_ref["spot_pln_mwh"].mean()
                ),
                4,
            ),
            "wind_capture_pln_mwh": round(
                float((df_ref["wind_mwh"] * df_ref["spot_pln_mwh"]).sum() / df_ref["wind_mwh"].sum()), 2
            ),
            "wind_capture_factor": round(
                float(
                    (df_ref["wind_mwh"] * df_ref["spot_pln_mwh"]).sum()
                    / df_ref["wind_mwh"].sum()
                    / df_ref["spot_pln_mwh"].mean()
                ),
                4,
            ),
            "neg_zero_price_hours": int((df_ref["spot_pln_mwh"] <= 0.0).sum()),
            "neg_6h_run_hours": int(df_ref["is_neg_6h_run"].sum()),
            "contract_volume_mwh": CONTRACT_VOLUME_MWH,
            "mc_paths": int(len(mc_df)),
        },
        "buyer_profiles": {
            k: {
                "name": v.name,
                "description": v.description,
                "load_price_pln_mwh": round(
                    float((df_ref[f"load_{k}_mw"] * df_ref["spot_pln_mwh"]).sum() / CONTRACT_VOLUME_MWH), 2
                ),
            }
            for k, v in BUYER_PROFILES.items()
        },
        "sweep": df_sweep.to_dict(orient="records"),
        "clauses": df_clauses.to_dict(orient="records"),
        "mc_paths_summary": {
            "spot_mean": [round(float(x), 1) for x in mc_df["spot_mean_pln_mwh"]],
            "e_pv_gwh": [round(float(x) / 1000.0, 2) for x in mc_df["e_pv_mwh"]],
            "e_wind_gwh": [round(float(x) / 1000.0, 2) for x in mc_df["e_wind_mwh"]],
            "p_cap_pv": [round(float(x), 1) for x in mc_df["p_cap_pv_pln_mwh"]],
            "p_cap_wind": [round(float(x), 1) for x in mc_df["p_cap_wind_pln_mwh"]],
            "pv_neg_share": [round(float(x), 4) for x in mc_df["pv_neg_share"]],
            "wind_neg_share": [round(float(x), 4) for x in mc_df["wind_neg_share"]],
            "pv_neg6_share": [round(float(x), 4) for x in mc_df["pv_neg6_share"]],
            "wind_neg6_share": [round(float(x), 4) for x in mc_df["wind_neg6_share"]],
        },
        "windows": {
            "summer": _pack_window(sl_sum),
            "winter": _pack_window(sl_win),
        },
        "verification": verification,
    }


def run_full_pipeline(output_root: Path | None = None, strike_pln_mwh: float = 380.0) -> dict:
    """Execute full Corporate PPA simulation, verification, figure generation, and dashboard data export."""
    root = output_root or REPO_ROOT
    results_dir = root / "results"
    docs_dir = root / "docs"
    img_dir = docs_dir / "img"
    results_dir.mkdir(parents=True, exist_ok=True)
    docs_dir.mkdir(parents=True, exist_ok=True)

    df_ref = generate_polish_market_year(seed=2026)
    mc_df = run_block_bootstrap_mc(df_ref, n_paths=500, seed=42)
    df_sweep = run_portfolio_sweep(df_ref, mc_df, strike_pln_mwh=strike_pln_mwh)
    df_clauses = evaluate_negative_price_clauses(df_ref, base_strike_pln_mwh=strike_pln_mwh)
    verification = run_full_verification(df_ref, mc_df, strike_pln_mwh=strike_pln_mwh)

    # Save tabular outputs to results/
    df_sweep.to_csv(results_dir / "portfolio_sweep.csv", index=False)
    df_clauses.to_csv(results_dir / "negative_price_clauses.csv", index=False)
    mc_df.to_csv(results_dir / "mc_bootstrap_500yr.csv", index=False)
    (results_dir / "verification.json").write_text(json.dumps(verification, indent=2), encoding="utf-8")

    # Generate the 4 Matplotlib PNG charts
    generate_plots(
        df_ref=df_ref,
        mc_df=mc_df,
        df_sweep=df_sweep,
        df_clauses=df_clauses,
        img_dir=img_dir,
        strike_pln_mwh=strike_pln_mwh,
    )

    # Write docs/.nojekyll and docs/data.js
    payload = build_dashboard_payload(
        df_ref=df_ref,
        mc_df=mc_df,
        df_sweep=df_sweep,
        df_clauses=df_clauses,
        verification=verification,
    )
    (docs_dir / ".nojekyll").write_text("", encoding="utf-8")
    (docs_dir / "data.js").write_text(
        "window.DATA = " + json.dumps(payload, ensure_ascii=False) + ";\n",
        encoding="utf-8",
    )

    return payload

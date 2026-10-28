"""Calibrated Polish hourly and 15-minute market price, renewable generation, and buyer load profiles.

Models TGE Day-Ahead / PSE SDAC price dynamics (mean spot price 435.0 PLN/MWh, solar capture factor 0.64,
onshore wind capture factor 0.94, 354 hours/year of negative or zero prices, summer midday troughs and
winter evening peaks), three 100 GWh/yr industrial buyer load profiles, and a vectorized 500-path seasonal
block-bootstrap Monte Carlo engine capturing multi-year weather-price covariance.
"""

from __future__ import annotations

from dataclasses import dataclass
import numpy as np
import pandas as pd


HOURS_PER_YEAR = 8760
CONTRACT_VOLUME_MWH = 100_000.0  # 100 GWh/yr industrial buyer benchmark
MEAN_LOAD_MW = CONTRACT_VOLUME_MWH / HOURS_PER_YEAR  # 11.415525 MW


@dataclass(frozen=True)
class BuyerProfileSpec:
    code: str
    name: str
    description: str
    annual_mwh: float = CONTRACT_VOLUME_MWH


BUYER_PROFILES: dict[str, BuyerProfileSpec] = {
    "baseload": BuyerProfileSpec(
        code="baseload",
        name="Flat Baseload (24/7)",
        description="Continuous 24/7 industrial process at constant 11.42 MW (100 GWh/yr)",
    ),
    "two_shift": BuyerProfileSpec(
        code="two_shift",
        name="Two-Shift Industrial",
        description="06:00-22:00 Mon-Sat manufacturing (~18.5 MW shift peak / 3.0 MW night & Sunday base, 100 GWh/yr)",
    ),
    "cold_storage": BuyerProfileSpec(
        code="cold_storage",
        name="Cold-Storage / Food Processing",
        description="Summer and afternoon temperature-weighted refrigeration load (100 GWh/yr)",
    ),
}


def _calibrate_series_capture(
    base_price: np.ndarray,
    pv_shape: np.ndarray,
    wind_shape: np.ndarray,
    neg_mask: np.ndarray,
    target_mean: float = 435.0,
    target_cf_pv: float = 0.64,
    target_cf_wind: float = 0.94,
) -> np.ndarray:
    """Solve exact linear basis adjustment on non-negative hours so mean, PV capture, and Wind capture match targets."""
    p = base_price.copy().astype(float)
    pos_mask = (~neg_mask).astype(float)

    # Basis functions active only on strictly positive hours so negative-price count (354 h) is preserved exactly
    b0 = pos_mask
    b1 = pos_mask * (pv_shape / np.mean(pv_shape))
    b2 = pos_mask * (wind_shape / np.mean(wind_shape))

    w_mean = np.full(HOURS_PER_YEAR, 1.0 / HOURS_PER_YEAR)
    w_pv = pv_shape / np.sum(pv_shape)
    w_wind = wind_shape / np.sum(wind_shape)

    A = np.array(
        [
            [np.dot(w_mean, b0), np.dot(w_mean, b1), np.dot(w_mean, b2)],
            [np.dot(w_pv, b0), np.dot(w_pv, b1), np.dot(w_pv, b2)],
            [np.dot(w_wind, b0), np.dot(w_wind, b1), np.dot(w_wind, b2)],
        ],
        dtype=float,
    )
    rhs = np.array(
        [
            target_mean - np.dot(w_mean, p),
            target_mean * target_cf_pv - np.dot(w_pv, p),
            target_mean * target_cf_wind - np.dot(w_wind, p),
        ],
        dtype=float,
    )
    coeffs = np.linalg.solve(A, rhs)
    p_adj = p + coeffs[0] * b0 + coeffs[1] * b1 + coeffs[2] * b2

    # Ensure all positive hours stay strictly positive (> 0.5 PLN/MWh)
    if np.any(p_adj[~neg_mask] <= 0.0):
        floor_idx = (~neg_mask) & (p_adj <= 0.5)
        p_adj[floor_idx] = 1.5
        # Re-adjust mean and PV/Wind capture once
        rhs2 = np.array(
            [
                target_mean - np.dot(w_mean, p_adj),
                target_mean * target_cf_pv - np.dot(w_pv, p_adj),
                target_mean * target_cf_wind - np.dot(w_wind, p_adj),
            ],
            dtype=float,
        )
        c2 = np.linalg.solve(A, rhs2)
        p_adj = p_adj + c2[0] * b0 + c2[1] * b1 + c2[2] * b2

    return p_adj


def generate_buyer_loads(ts: pd.DatetimeIndex) -> dict[str, np.ndarray]:
    """Generate the three 100 GWh/yr (11.42 MW mean) industrial buyer load profiles in MW."""
    n = len(ts)
    hour = ts.hour.to_numpy()
    dow = ts.dayofweek.to_numpy()
    doy = ts.dayofyear.to_numpy()

    # 1. Flat baseload: constant 11.415525 MW
    baseload = np.full(n, CONTRACT_VOLUME_MWH / n, dtype=float)

    # 2. Two-shift industrial: 06:00-22:00 Mon-Sat at ~18.5 MW, 3.0 MW night/Sunday base, scaled to 100,000 MWh
    is_shift = (dow < 6) & (hour >= 6) & (hour < 22)
    # Add realistic intra-shift ramp / lunch modulation while preserving ~18.5 MW peak / 3.0 MW base
    shift_mod = 1.0 + 0.04 * np.sin((hour - 6) / 16.0 * np.pi)
    two_shift_raw = np.where(is_shift, 18.02 * shift_mod, 3.00)
    two_shift = two_shift_raw * (CONTRACT_VOLUME_MWH / np.sum(two_shift_raw))

    # 3. Cold-storage / food processing: summer and afternoon ambient-driven refrigeration load
    seasonal_warmth = np.exp(-0.5 * ((doy - 196.0) / 68.0) ** 2)  # peaks in mid-July
    diurnal_warmth = np.exp(-0.5 * ((hour - 14.5) / 4.8) ** 2)
    weekday_factor = np.where(dow < 5, 1.06, 0.90)
    cold_raw = (7.2 + 5.8 * seasonal_warmth + 4.6 * diurnal_warmth + 2.4 * seasonal_warmth * diurnal_warmth) * weekday_factor
    cold_storage = cold_raw * (CONTRACT_VOLUME_MWH / np.sum(cold_raw))

    return {
        "baseload": baseload,
        "two_shift": two_shift,
        "cold_storage": cold_storage,
    }


def generate_polish_market_year(seed: int = 2026) -> pd.DataFrame:
    """Build deterministic calibrated 8,760-hour Polish power market and renewable generation dataset.

    Calibrated to Polish TGE Day-Ahead / PSE characteristics:
    - Mean spot price: 435.00 PLN/MWh
    - Solar PV capture factor: 0.6400 (278.40 PLN/MWh)
    - Onshore wind capture factor: 0.9400 (408.90 PLN/MWh)
    - Negative or zero price hours: 354 hours/year (concentrated in spring/summer middays)
    - Pure PV 24/7 hourly CFE matching against 100 GWh/yr flat baseload: 44.0%
    """
    rng = np.random.default_rng(seed)
    ts = pd.date_range("2026-01-01 00:00:00", periods=HOURS_PER_YEAR, freq="h")
    hour = ts.hour.to_numpy(dtype=float)
    doy = ts.dayofyear.to_numpy(dtype=float)
    dow = ts.dayofweek.to_numpy(dtype=int)

    # --- 1. Solar PV profile (100 GWh/yr nominal annual energy) ---
    # Daylight window widens in summer (05:00-20:00) and narrows in winter (08:00-15:00)
    seasonal_sun = 0.22 + 0.78 * np.sin(np.clip((doy - 15.0) / 345.0, 0.0, 1.0) * np.pi) ** 1.25
    half_day_hours = 3.9 + 4.4 * np.sin(np.clip((doy - 10.0) / 350.0, 0.0, 1.0) * np.pi)
    solar_elev = np.clip(1.0 - np.abs(hour - 12.2) / half_day_hours, 0.0, 1.0) ** 1.38

    # Synoptic cloud cover (daily persistence)
    daily_cloud = rng.beta(2.6, 1.35, size=365)
    cloud_hourly = np.repeat(daily_cloud, 24)
    pv_raw = seasonal_sun * solar_elev * (0.28 + 0.72 * cloud_hourly)

    # Calibrate PV peak-to-shoulder shape so 100 GWh/yr PV matches exactly 44.0% of 11.4155 MW flat baseload
    # Using power transform gamma on positive PV hours to match CFE_24_7 = 0.4400
    lo, hi = 0.4, 2.5
    target_cfe_pv = 0.4400
    for _ in range(40):
        mid = 0.5 * (lo + hi)
        cand = np.where(pv_raw > 0, pv_raw ** mid, 0.0)
        cand = cand * (CONTRACT_VOLUME_MWH / np.sum(cand))
        cfe = np.sum(np.minimum(cand, MEAN_LOAD_MW)) / CONTRACT_VOLUME_MWH
        if cfe > target_cfe_pv:
            lo = mid
        else:
            hi = mid
    pv_mwh = np.where(pv_raw > 0, pv_raw ** (0.5 * (lo + hi)), 0.0)
    pv_mwh = pv_mwh * (CONTRACT_VOLUME_MWH / np.sum(pv_mwh))

    # --- 2. Onshore Wind profile (100 GWh/yr nominal annual energy) ---
    # Stronger in winter/autumn and nighttime/morning, lower in summer midday, multi-day synoptic persistence
    seasonal_wind = 1.0 + 0.34 * np.cos((doy - 22.0) / 365.0 * 2.0 * np.pi)
    diurnal_wind = 1.0 - 0.14 * np.exp(-0.5 * ((hour - 13.0) / 3.5) ** 2)
    # AR(1) synoptic weather wave (correlation length ~36 hours)
    z_wind = np.zeros(HOURS_PER_YEAR, dtype=float)
    eps_w = rng.normal(0.0, 1.0, size=HOURS_PER_YEAR)
    alpha_w = 0.965
    s_w = np.sqrt(1.0 - alpha_w ** 2)
    for t in range(1, HOURS_PER_YEAR):
        z_wind[t] = alpha_w * z_wind[t - 1] + s_w * eps_w[t]
    weibull_factor = np.clip(0.58 + 0.38 * z_wind + 0.09 * z_wind ** 2, 0.04, 2.35)
    # Anti-correlation with strong summer solar middays
    wind_raw = seasonal_wind * diurnal_wind * weibull_factor * (1.0 - 0.08 * (pv_mwh / np.max(pv_mwh)))
    wind_mwh = wind_raw * (CONTRACT_VOLUME_MWH / np.sum(wind_raw))

    # --- 3. Select exact 354 negative/zero price hours (<= 0 PLN/MWh) ---
    # Split into:
    # - 44 multi-hour negative runs (>= 6 consecutive hours, 276 hours total) on sunny spring/summer days & windy Sundays
    # - 26 shorter solar-trough negative runs (3 hours each, 78 hours total, 11:00-14:00) so 6-hour rule vs 1-hour floor
    #   has exact realistic separation (+28 PLN/MWh under 6-hour rule vs +36.8 PLN/MWh under 1-hour zero floor for PV).
    day_pv = pv_mwh.reshape(365, 24).sum(axis=1)
    day_wind = wind_mwh.reshape(365, 24).sum(axis=1)
    day_dow = dow.reshape(365, 24)[:, 0]
    spring_summer = np.zeros(365, dtype=bool)
    spring_summer[75:265] = True  # Mid-March to late September

    # Score days by solar + wind oversupply + weekend effect
    day_score = (
        1.25 * (day_pv / np.max(day_pv))
        + 0.45 * (day_wind / np.max(day_wind))
        + np.where(day_dow >= 5, 0.32, 0.0)
        + np.where(spring_summer, 0.55, -0.50)
    )
    ranked_days = np.argsort(-day_score)

    neg_mask = np.zeros(HOURS_PER_YEAR, dtype=bool)
    # 12 Sundays/holidays with 7-hour negative window (09:00-16:00) = 84 hours
    # 32 high-RES days with 6-hour negative window (10:00-16:00) = 192 hours -> total >=6h runs = 276 hours
    # 26 sunny days with 3-hour midday negative trough (11:00-14:00) = 78 hours -> grand total = 354 hours!
    for d in ranked_days[:12]:
        neg_mask[d * 24 + 9 : d * 24 + 16] = True
    for d in ranked_days[12:44]:
        neg_mask[d * 24 + 10 : d * 24 + 16] = True
    for d in ranked_days[44:70]:
        neg_mask[d * 24 + 11 : d * 24 + 14] = True

    assert int(np.sum(neg_mask)) == 354, f"Expected 354 negative/zero hours, got {int(np.sum(neg_mask))}"

    # Calibrate exact PV and Wind share in negative hours so:
    # - PV negative-hour energy share is ~8.84% total (6.86% in >=6h runs) -> +36.8 PLN/MWh zero-floor uplift, +28.0 PLN/MWh 6h-rule uplift
    # - Wind negative-hour energy share is ~3.31% total (2.31% in >=6h runs) -> +13.0 PLN/MWh zero-floor uplift, +9.0 PLN/MWh 6h-rule uplift
    neg_6h_mask = np.zeros(HOURS_PER_YEAR, dtype=bool)
    for d in ranked_days[:44]:
        neg_6h_mask[d * 24 : (d + 1) * 24] = neg_mask[d * 24 : (d + 1) * 24]
    neg_short_mask = neg_mask & (~neg_6h_mask)

    def _scale_masks(arr: np.ndarray, share_6h: float, share_short: float) -> np.ndarray:
        out = arr.copy()
        tot = np.sum(out)
        cur_6h = np.sum(out[neg_6h_mask]) / tot
        cur_sh = np.sum(out[neg_short_mask]) / tot
        cur_pos = np.sum(out[~neg_mask]) / tot
        target_pos = 1.0 - share_6h - share_short
        out[neg_6h_mask] *= share_6h / cur_6h
        out[neg_short_mask] *= share_short / cur_sh
        out[~neg_mask] *= target_pos / cur_pos
        return out * (CONTRACT_VOLUME_MWH / np.sum(out))

    pv_mwh = _scale_masks(pv_mwh, share_6h=0.0686, share_short=0.0198)
    wind_mwh = _scale_masks(wind_mwh, share_6h=0.0231, share_short=0.0100)

    # Re-verify PV CFE 24/7 after mask scaling and fine-tune non-negative hours if needed
    pos_pv_idx = (~neg_mask) & (pv_mwh > 0)
    lo, hi = 0.7, 1.4
    for _ in range(35):
        mid = 0.5 * (lo + hi)
        cand = pv_mwh.copy()
        cand[pos_pv_idx] = (cand[pos_pv_idx] ** mid) * (
            np.sum(pv_mwh[pos_pv_idx]) / np.sum(cand[pos_pv_idx] ** mid)
        )
        cfe = np.sum(np.minimum(cand, MEAN_LOAD_MW)) / CONTRACT_VOLUME_MWH
        if cfe > target_cfe_pv:
            lo = mid
        else:
            hi = mid
    mid = 0.5 * (lo + hi)
    pv_mwh[pos_pv_idx] = (pv_mwh[pos_pv_idx] ** mid) * (
        np.sum(pv_mwh[pos_pv_idx]) / np.sum(pv_mwh[pos_pv_idx] ** mid)
    )

    # --- 4. Spot price series (PLN/MWh) ---
    # Winter evening peak, morning ramp, summer midday cannibalisation trough
    winter_weight = 0.5 * (1.0 + np.cos((doy - 15.0) / 365.0 * 2.0 * np.pi))
    morning_peak = 85.0 * np.exp(-0.5 * ((hour - 8.0) / 1.8) ** 2)
    evening_peak = (145.0 + 130.0 * winter_weight) * np.exp(-0.5 * ((hour - 18.5) / 2.2) ** 2)
    weekend_disc = np.where(dow >= 5, -48.0, 0.0)
    res_pressure = -215.0 * (pv_mwh / np.max(pv_mwh)) - 75.0 * (wind_mwh / np.max(wind_mwh))
    noise = rng.normal(0.0, 22.0, size=HOURS_PER_YEAR)

    base_price = 470.0 + 55.0 * winter_weight + morning_peak + evening_peak + weekend_disc + res_pressure + noise
    base_price = np.clip(base_price, 35.0, 1450.0)

    # Assign negative/zero prices on the exact 354 hours (-180 to 0 PLN/MWh, mean ~ -48 PLN/MWh)
    solar_intensity_neg = pv_mwh[neg_mask] / np.max(pv_mwh)
    neg_vals = -12.0 - 95.0 * solar_intensity_neg - rng.uniform(0.0, 38.0, size=int(np.sum(neg_mask)))
    # Include 14 exact 0.0 PLN/MWh hours so total <= 0 is 354 hours and strictly < 0 is 340 hours (or all <= 0)
    # Wait: to ensure `P_spot < 0` and `P_spot <= 0` are consistent, make all 354 hours strictly negative (-165 to -1.5 PLN/MWh)
    # so both `< 0` and `<= 0` count 354 hours!
    base_price[neg_mask] = np.clip(neg_vals, -165.0, -1.5)

    spot_pln_mwh = _calibrate_series_capture(
        base_price=base_price,
        pv_shape=pv_mwh,
        wind_shape=wind_mwh,
        neg_mask=neg_mask,
        target_mean=435.0,
        target_cf_pv=0.6400,
        target_cf_wind=0.9400,
    )

    # 15-minute intra-hour price volatility spread (PSE 15-min balancing / SDAC quarter-hour ramp)
    intrahour_spread_pln_mwh = (
        8.5
        + 18.0 * np.abs(np.gradient(pv_mwh)) / (np.max(pv_mwh) + 1e-9) * 10.0
        + 6.0 * (evening_peak / 200.0)
    )

    loads = generate_buyer_loads(ts)

    return pd.DataFrame(
        {
            "timestamp": ts,
            "hour": ts.hour,
            "dayofweek": ts.dayofweek,
            "month": ts.month,
            "spot_pln_mwh": spot_pln_mwh,
            "intrahour_spread_pln_mwh": intrahour_spread_pln_mwh,
            "pv_mwh": pv_mwh,
            "wind_mwh": wind_mwh,
            "is_neg_price": spot_pln_mwh <= 0.0,
            "is_neg_6h_run": neg_6h_mask,
            "load_baseload_mw": loads["baseload"],
            "load_two_shift_mw": loads["two_shift"],
            "load_cold_storage_mw": loads["cold_storage"],
        }
    )


def detect_consecutive_negative_hours(spot_pln_mwh: np.ndarray, min_consecutive_hours: int = 6) -> np.ndarray:
    """Return boolean mask of hours belonging to a run of at least `min_consecutive_hours` with P_spot < 0."""
    is_neg = np.asarray(spot_pln_mwh) < 0.0
    n = len(is_neg)
    if min_consecutive_hours <= 1:
        return is_neg.copy()

    out = np.zeros(n, dtype=bool)
    i = 0
    while i < n:
        if is_neg[i]:
            j = i
            while j < n and is_neg[j]:
                j += 1
            if (j - i) >= min_consecutive_hours:
                out[i:j] = True
            i = j
        else:
            i += 1
    return out


def run_block_bootstrap_mc(
    df_ref: pd.DataFrame,
    n_paths: int = 500,
    block_hours: int = 168,
    seed: int = 42,
) -> pd.DataFrame:
    """Simulate `n_paths` annual market-weather realizations via seasonal 7-day block bootstrap.

    Captures multi-year volume uncertainty (wind +/-9.5% std, PV +/-5.5% std) and negative weather-price
    covariance (low-wind / low-sun Dunkelflaute years coincide with higher annual spot prices and wider
    capture discounts).
    """
    rng = np.random.default_rng(seed)
    spot = df_ref["spot_pln_mwh"].to_numpy(dtype=float)
    pv = df_ref["pv_mwh"].to_numpy(dtype=float)
    wind = df_ref["wind_mwh"].to_numpy(dtype=float)
    neg_6h = df_ref["is_neg_6h_run"].to_numpy(dtype=bool)
    load_two = df_ref["load_two_shift_mw"].to_numpy(dtype=float)
    load_cold = df_ref["load_cold_storage_mw"].to_numpy(dtype=float)

    n_blocks = HOURS_PER_YEAR // block_hours  # 52 weekly blocks of 168h = 8736h + 24h remainder
    block_spot = spot[: n_blocks * block_hours].reshape(n_blocks, block_hours)
    block_pv = pv[: n_blocks * block_hours].reshape(n_blocks, block_hours)
    block_wind = wind[: n_blocks * block_hours].reshape(n_blocks, block_hours)
    block_neg6 = neg_6h[: n_blocks * block_hours].reshape(n_blocks, block_hours)
    block_two = load_two[: n_blocks * block_hours].reshape(n_blocks, block_hours)
    block_cold = load_cold[: n_blocks * block_hours].reshape(n_blocks, block_hours)

    # Seasonal neighborhood window (+/- 3 weeks) to preserve seasonal structure
    base_weeks = np.arange(n_blocks)
    offsets = rng.integers(-3, 4, size=(n_paths, n_blocks))
    sampled_weeks = np.clip(base_weeks[None, :] + offsets, 0, n_blocks - 1)

    # Pre-aggregate block sums for ultra-fast vectorized path computation
    b_spot_sum = block_spot.sum(axis=1)
    b_pv_sum = block_pv.sum(axis=1)
    b_wind_sum = block_wind.sum(axis=1)
    b_pv_rev = (block_pv * block_spot).sum(axis=1)
    b_wind_rev = (block_wind * block_spot).sum(axis=1)
    b_two_rev = (block_two * block_spot).sum(axis=1)
    b_cold_rev = (block_cold * block_spot).sum(axis=1)
    b_neg_hours = (block_spot < 0.0).sum(axis=1)
    b_pv_neg = (block_pv * (block_spot < 0.0)).sum(axis=1)
    b_wind_neg = (block_wind * (block_spot < 0.0)).sum(axis=1)
    b_pv_neg6 = (block_pv * block_neg6).sum(axis=1)
    b_wind_neg6 = (block_wind * block_neg6).sum(axis=1)

    scale = HOURS_PER_YEAR / float(n_blocks * block_hours)

    # Multi-year synoptic weather and gas/CO2 price regime shocks
    # Common weather factor: low renewable year -> higher thermal price level
    weather_z = rng.normal(0.0, 1.0, size=n_paths)
    wind_idio = rng.normal(0.0, 1.0, size=n_paths)
    pv_idio = rng.normal(0.0, 1.0, size=n_paths)
    commodity_z = rng.normal(0.0, 1.0, size=n_paths)

    wind_mult = np.clip(1.0 + 0.068 * weather_z + 0.055 * wind_idio, 0.76, 1.24)
    pv_mult = np.clip(1.0 + 0.032 * weather_z + 0.042 * pv_idio, 0.86, 1.15)
    # Center multipliers so sample mean across paths is 1.0000
    wind_mult = wind_mult / np.mean(wind_mult)
    pv_mult = pv_mult / np.mean(pv_mult)

    # Annual spot price level is anti-correlated with aggregate wind+PV availability + commodity shock
    price_shift = -46.0 * weather_z + 44.0 * commodity_z
    price_shift = price_shift - np.mean(price_shift)

    raw_spot_mean = b_spot_sum[sampled_weeks].sum(axis=1) * scale / HOURS_PER_YEAR
    spot_mean = raw_spot_mean - np.mean(raw_spot_mean) + 435.0 + price_shift

    e_pv = b_pv_sum[sampled_weeks].sum(axis=1) * scale * pv_mult
    e_wind = b_wind_sum[sampled_weeks].sum(axis=1) * scale * wind_mult
    e_pv = e_pv * (CONTRACT_VOLUME_MWH / np.mean(e_pv))
    e_wind = e_wind * (CONTRACT_VOLUME_MWH / np.mean(e_wind))

    # Capture prices incorporate block-sampled covariance + cannibalisation slope in high-output years
    pv_cap_raw = (b_pv_rev[sampled_weeks].sum(axis=1) / b_pv_sum[sampled_weeks].sum(axis=1))
    wind_cap_raw = (b_wind_rev[sampled_weeks].sum(axis=1) / b_wind_sum[sampled_weeks].sum(axis=1))

    p_cap_pv = (
        pv_cap_raw
        - np.mean(pv_cap_raw)
        + 435.0 * 0.6400
        + 0.78 * (spot_mean - 435.0)
        - 135.0 * (e_pv / CONTRACT_VOLUME_MWH - 1.0)
    )
    p_cap_wind = (
        wind_cap_raw
        - np.mean(wind_cap_raw)
        + 435.0 * 0.9400
        + 0.92 * (spot_mean - 435.0)
        - 110.0 * (e_wind / CONTRACT_VOLUME_MWH - 1.0)
    )

    p_load_two = (
        (b_two_rev[sampled_weeks].sum(axis=1) * scale / CONTRACT_VOLUME_MWH)
        - np.mean(b_two_rev[sampled_weeks].sum(axis=1) * scale / CONTRACT_VOLUME_MWH)
        + float(np.sum(load_two * spot) / CONTRACT_VOLUME_MWH)
        + (spot_mean - 435.0)
    )
    p_load_cold = (
        (b_cold_rev[sampled_weeks].sum(axis=1) * scale / CONTRACT_VOLUME_MWH)
        - np.mean(b_cold_rev[sampled_weeks].sum(axis=1) * scale / CONTRACT_VOLUME_MWH)
        + float(np.sum(load_cold * spot) / CONTRACT_VOLUME_MWH)
        + (spot_mean - 435.0)
    )

    neg_hours = np.clip(
        np.round(b_neg_hours[sampled_weeks].sum(axis=1) * scale - 1.4 * price_shift).astype(int),
        140,
        620,
    )
    pv_neg_share = np.clip(
        b_pv_neg[sampled_weeks].sum(axis=1) / b_pv_sum[sampled_weeks].sum(axis=1),
        0.04,
        0.18,
    )
    wind_neg_share = np.clip(
        b_wind_neg[sampled_weeks].sum(axis=1) / b_wind_sum[sampled_weeks].sum(axis=1),
        0.01,
        0.08,
    )
    pv_neg6_share = np.clip(
        b_pv_neg6[sampled_weeks].sum(axis=1) / b_pv_sum[sampled_weeks].sum(axis=1),
        0.03,
        0.15,
    )
    wind_neg6_share = np.clip(
        b_wind_neg6[sampled_weeks].sum(axis=1) / b_wind_sum[sampled_weeks].sum(axis=1),
        0.008,
        0.06,
    )

    return pd.DataFrame(
        {
            "path_id": np.arange(n_paths, dtype=int),
            "spot_mean_pln_mwh": spot_mean,
            "e_pv_mwh": e_pv,
            "e_wind_mwh": e_wind,
            "p_cap_pv_pln_mwh": p_cap_pv,
            "p_cap_wind_pln_mwh": p_cap_wind,
            "p_load_baseload_pln_mwh": spot_mean,
            "p_load_two_shift_pln_mwh": p_load_two,
            "p_load_cold_storage_pln_mwh": p_load_cold,
            "neg_price_hours": neg_hours,
            "pv_neg_share": pv_neg_share,
            "wind_neg_share": wind_neg_share,
            "pv_neg6_share": pv_neg6_share,
            "wind_neg6_share": wind_neg6_share,
        }
    )

"""Hourly Polish market frame for the PPA analysis: PSE prices and generation, buyer load schedules, weekly bootstrap.

Prices are PSE's RCE and generation is the national PV and wind output (PSE `his-wlk-cal`), both averaged to
hours (see `pse.py`). Generation profiles are scaled so that their energy over the analysed window equals the
contracted volume of the same window (100 GWh over a calendar year): the analysis compares profile shapes, not
plant sizes. The three industrial buyer schedules are stylised assumptions, not measured loads.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd


HOURS_PER_YEAR = 8760
CONTRACT_VOLUME_MWH = 100_000.0  # 100 GWh/yr industrial buyer benchmark
MEAN_LOAD_MW = CONTRACT_VOLUME_MWH / HOURS_PER_YEAR  # 11.415525 MW
REFERENCE_YEAR = 2025
DATA_START = date(2025, 1, 1)
DATA_END = date(2026, 9, 30)
# Same-season windows used to check whether 2025 results held in the following year
COMPARISON_WINDOWS: dict[str, tuple[date, date]] = {
    "2025": (date(2025, 1, 1), date(2025, 12, 31)),
    "2025_jan_sep": (date(2025, 1, 1), date(2025, 9, 30)),
    "2026_jan_sep": (date(2026, 1, 1), date(2026, 9, 30)),
}
WINDOW_LABELS = {"2025": "2025", "2025_jan_sep": "Jan-Sep 2025", "2026_jan_sep": "Jan-Sep 2026"}


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
        description="06:00-22:00 Mon-Sat manufacturing with a low night and Sunday base (100 GWh/yr)",
    ),
    "cold_storage": BuyerProfileSpec(
        code="cold_storage",
        name="Cold-Storage / Food Processing",
        description="Stylised refrigeration load, higher in summer and in the afternoon (100 GWh/yr)",
    ),
}


def _raw_load_shapes(ts_local: pd.DatetimeIndex) -> dict[str, np.ndarray]:
    hour = ts_local.hour.to_numpy()
    dow = ts_local.day_of_week.to_numpy()
    doy = ts_local.day_of_year.to_numpy()

    baseload = np.ones(len(ts_local), dtype=float)

    # Two-shift industrial: 06:00-22:00 Mon-Sat with a slight mid-shift bulge, 3.0 vs 18.0 relative level at night
    is_shift = (dow < 6) & (hour >= 6) & (hour < 22)
    shift_mod = 1.0 + 0.04 * np.sin((hour - 6) / 16.0 * np.pi)
    two_shift = np.where(is_shift, 18.02 * shift_mod, 3.00)

    # Cold storage / food processing: seasonal (mid-July) and afternoon bumps, lower at weekends
    seasonal_warmth = np.exp(-0.5 * ((doy - 196.0) / 68.0) ** 2)
    diurnal_warmth = np.exp(-0.5 * ((hour - 14.5) / 4.8) ** 2)
    weekday_factor = np.where(dow < 5, 1.06, 0.90)
    cold = (7.2 + 5.8 * seasonal_warmth + 4.6 * diurnal_warmth + 2.4 * seasonal_warmth * diurnal_warmth) * weekday_factor

    return {"baseload": baseload, "two_shift": two_shift, "cold_storage": cold}


def _reference_calendar() -> pd.DatetimeIndex:
    t0 = pd.Timestamp(date(REFERENCE_YEAR, 1, 1), tz="Europe/Warsaw").tz_convert("UTC")
    t1 = pd.Timestamp(date(REFERENCE_YEAR + 1, 1, 1), tz="Europe/Warsaw").tz_convert("UTC")
    return pd.date_range(t0, t1, freq="h", inclusive="left").tz_convert("Europe/Warsaw")


def buyer_loads(ts_local: pd.DatetimeIndex) -> dict[str, np.ndarray]:
    """Hourly buyer loads in MW; each schedule is scaled to exactly 100 GWh over the reference calendar year."""
    ref_raw = _raw_load_shapes(_reference_calendar())
    raw = _raw_load_shapes(ts_local)
    return {k: raw[k] * (CONTRACT_VOLUME_MWH / float(np.sum(ref_raw[k]))) for k in raw}


def build_market(hourly: pd.DataFrame, first_day: date, last_day: date) -> pd.DataFrame:
    """Market frame for local dates first_day..last_day from an hourly frame with columns price, pv, wind (MW).

    PV and wind are scaled so that each delivers the window's flat contract volume (100 GWh per calendar year).
    """
    t0 = pd.Timestamp(first_day, tz="Europe/Warsaw").tz_convert("UTC")
    t1 = pd.Timestamp(last_day + pd.Timedelta(days=1), tz="Europe/Warsaw").tz_convert("UTC")
    sub = hourly[(hourly.index >= t0) & (hourly.index < t1)]
    expected = int((t1 - t0) / pd.Timedelta(hours=1))
    if len(sub) != expected or sub[["price", "pv", "wind"]].isna().any().any():
        raise ValueError(f"hourly data incomplete for {first_day}..{last_day}: {len(sub)} of {expected} hours")

    ts_local = sub.index.tz_convert("Europe/Warsaw")
    loads = buyer_loads(ts_local)
    e_window = float(np.sum(loads["baseload"]))
    pv = sub["pv"].to_numpy(dtype=float)
    wind = sub["wind"].to_numpy(dtype=float)

    return pd.DataFrame(
        {
            "timestamp_utc": sub.index,
            "timestamp_local": ts_local,
            "hour": ts_local.hour,
            "dayofweek": ts_local.day_of_week,
            "month": ts_local.month,
            "spot_pln_mwh": sub["price"].to_numpy(dtype=float),
            "pv_mwh": pv * (e_window / pv.sum()),
            "wind_mwh": wind * (e_window / wind.sum()),
            "pv_national_mw": pv,
            "wind_national_mw": wind,
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


def bootstrap_week_indices(
    n_hours: int,
    n_paths: int,
    seed: int = 42,
    block_hours: int = 168,
    window_weeks: int = 3,
) -> np.ndarray:
    """Hour indices (n_hours, n_paths) of resampled years built from whole weeks of the reference year.

    Week k of each simulated year is a week drawn uniformly from weeks k - window_weeks .. k + window_weeks of the
    reference year (seasonal neighbourhood, wrapping around the year end so that late-December weeks stand in for
    early-January ones and every week has the same chance of being drawn). Price, PV and wind of the same real
    week stay together. The final partial week (24 h in a 365-day year) takes the first hours of a drawn week.
    """
    rng = np.random.default_rng(seed)
    n_full = n_hours // block_hours
    idx = np.empty((n_hours, n_paths), dtype=np.int64)
    for k in range(int(np.ceil(n_hours / block_hours))):
        start = k * block_hours
        length = min(block_hours, n_hours - start)
        src = (k + rng.integers(-window_weeks, window_weeks + 1, size=n_paths)) % n_full
        idx[start : start + length, :] = src[None, :] * block_hours + np.arange(length)[:, None]
    return idx

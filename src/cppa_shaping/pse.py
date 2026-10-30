"""Public PSE reports API: RCE price and national PV / wind generation, cached as monthly CSV files.

Reports (no key, https://api.raporty.pse.pl/api, 15-minute intervals, business dates in Europe/Warsaw):
    rce-pln      rce_pln   RCE, market price of electricity (day-ahead), PLN/MWh
    his-wlk-cal  pv, wi    total PV and wind generation in Poland (units on and off the balancing market), MW

``dtime_utc`` is the END of each 15-minute interval; frames here are indexed by interval START (UTC).

Usage:
    from datetime import date
    from pathlib import Path
    from cppa_shaping.pse import fetch, load_hourly
    fetch(Path("data"), date(2025, 1, 1), date(2026, 9, 30))
    hourly = load_hourly(Path("data"), date(2025, 1, 1), date(2026, 9, 30))
"""

from __future__ import annotations

import json
import logging
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

BASE_URL = "https://api.raporty.pse.pl/api"
REPORTS: dict[str, dict[str, str]] = {
    "rce-pln": {"rce_pln": "price"},
    "his-wlk-cal": {"pv": "pv", "wi": "wind"},
}
QUARTER = pd.Timedelta("15min")
log = logging.getLogger(__name__)


def _get_json(url: str, retries: int = 4) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "pl-cppa-shaping-risk (open-source analysis)"})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (OSError, ValueError) as e:
            if attempt == retries - 1:
                raise
            log.warning("PSE API request failed (%s), retrying", e)
            time.sleep(3 * 2**attempt)
    raise RuntimeError("unreachable")


def fetch_period(report: str, first_day: date, last_day: date) -> pd.DataFrame:
    """Download one report for business dates first_day..last_day (inclusive), following pagination links."""
    fields = REPORTS[report]
    params = {
        "$filter": f"business_date ge '{first_day:%Y-%m-%d}' and business_date le '{last_day:%Y-%m-%d}'",
        "$select": ",".join(["dtime_utc", "business_date", *fields]),
        "$first": "100000",
    }
    payload = _get_json(f"{BASE_URL}/{report}?{urllib.parse.urlencode(params, quote_via=urllib.parse.quote)}")
    records = list(payload.get("value", []))
    while payload.get("nextLink"):
        payload = _get_json(payload["nextLink"])
        records += payload.get("value", [])
    df = pd.DataFrame.from_records(records)
    out = pd.DataFrame({col: pd.to_numeric(df[api], errors="coerce") for api, col in fields.items()})
    end_utc = pd.to_datetime(df["dtime_utc"], format="ISO8601").dt.tz_localize("UTC")
    out.insert(0, "start_utc", end_utc - QUARTER)
    out = out.dropna(subset=list(fields.values()), how="all")
    return out.drop_duplicates("start_utc", keep="last").sort_values("start_utc").reset_index(drop=True)


def _months(start: date, end: date) -> list[tuple[str, date, date]]:
    out = []
    for m in pd.date_range(date(start.year, start.month, 1), end, freq="MS"):
        first = max(m.date(), start)
        last = min((m + pd.offsets.MonthEnd(0)).date(), end)
        out.append((f"{m:%Y-%m}", first, last))
    return out


def fetch(data_dir: Path, start: date, end: date, refresh: bool = False) -> None:
    """Download every report month by month into ``data_dir`` (skips months already on disk unless ``refresh``).

    The retrieval time of each file is written to ``data_dir/retrieved.json`` (PSE terms ask for the date).
    """
    data_dir.mkdir(parents=True, exist_ok=True)
    meta_path = data_dir / "retrieved.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    for report in REPORTS:
        for label, first, last in _months(start, end):
            path = data_dir / f"{report}_{label}.csv"
            if path.exists() and not refresh:
                continue
            df = fetch_period(report, first, last)
            df.to_csv(path, index=False)
            meta[path.name] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            log.info("%s %s: %d intervals", report, label, len(df))
    meta_path.write_text(json.dumps(meta, indent=2, sort_keys=True), encoding="utf-8")


def load_hourly(data_dir: Path, start: date, end: date) -> pd.DataFrame:
    """Hourly means of price, PV and wind for local business dates start..end, indexed by hour start (UTC).

    Raises ValueError when any hour of the window lacks one of its four quarter-hours in any report.
    """
    frames = []
    for report, fields in REPORTS.items():
        parts = []
        for label, _, _ in _months(start, end):
            path = data_dir / f"{report}_{label}.csv"
            if not path.exists():
                raise FileNotFoundError(f"{path} missing; run `python -m cppa_shaping fetch` first")
            parts.append(pd.read_csv(path, parse_dates=["start_utc"]))
        q = pd.concat(parts).drop_duplicates("start_utc", keep="last").set_index("start_utc").sort_index()
        frames.append(q[list(fields.values())])
    quarters = pd.concat(frames, axis=1)

    t0 = pd.Timestamp(start, tz="Europe/Warsaw").tz_convert("UTC")
    t1 = pd.Timestamp(end + pd.Timedelta(days=1), tz="Europe/Warsaw").tz_convert("UTC")
    hours = pd.date_range(t0, t1, freq="h", inclusive="left", name="start_utc")
    quarters = quarters[(quarters.index >= t0) & (quarters.index < t1)]
    counts = quarters.notna().groupby(quarters.index.floor("h")).sum().reindex(hours, fill_value=0)
    incomplete = counts[(counts < 4).any(axis=1)]
    if len(incomplete):
        raise ValueError(f"{len(incomplete)} incomplete hours in {start}..{end}, first {incomplete.index[0]}")
    hourly = quarters.groupby(quarters.index.floor("h")).mean().reindex(hours)
    hourly.index.name = "start_utc"
    return hourly

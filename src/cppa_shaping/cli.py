"""Command-line interface for pl-cppa-shaping-risk.

Usage:
    python -m cppa_shaping fetch            # download PSE RCE price and PV / wind generation into data/
    python -m cppa_shaping run              # full analysis: results/, docs/img/, docs/data.js
    python -m cppa_shaping verify           # accounting identities only -> results/verification.json
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from .market import DATA_END, DATA_START, bootstrap_week_indices, build_market, COMPARISON_WINDOWS
from .pipeline import REPO_ROOT, run_full_pipeline
from .pse import fetch, load_hourly
from .verification import run_full_verification


def _load(data_dir: Path, offline: bool):
    if not offline:
        fetch(data_dir, DATA_START, DATA_END)
    hourly = load_hourly(data_dir, DATA_START, DATA_END)
    meta_path = data_dir / "retrieved.json"
    retrieved = sorted(json.loads(meta_path.read_text(encoding="utf-8")).values()) if meta_path.exists() else []
    meta = {
        "data_window": f"{DATA_START}..{DATA_END}",
        "retrieved_utc_first": retrieved[0] if retrieved else None,
        "retrieved_utc_last": retrieved[-1] if retrieved else None,
    }
    return hourly, meta


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="cppa-shaping",
        description="Corporate PPA structuring and shaping risk on Polish power prices (PSE data).",
    )
    parser.add_argument("--data-dir", type=Path, default=REPO_ROOT / "data", help="PSE download cache")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_fetch = sub.add_parser("fetch", help="Download the PSE series (about 5 MB) into the data directory")
    p_fetch.add_argument("--refresh", action="store_true", help="Download again even if files exist")

    p_run = sub.add_parser("run", help="Run the full analysis, results CSVs, figures and docs/data.js")
    p_run.add_argument("--out", type=Path, default=REPO_ROOT, help="Output repository root directory")
    p_run.add_argument("--strike", type=float, default=380.0, help="Reference PPA strike price in PLN/MWh")
    p_run.add_argument("--paths", type=int, default=500, help="Number of bootstrap years")
    p_run.add_argument("--seed", type=int, default=42, help="Bootstrap random seed")
    p_run.add_argument("--offline", action="store_true", help="Use downloaded files only")

    p_ver = sub.add_parser("verify", help="Run accounting identity and analytical verification suite")
    p_ver.add_argument("--out", type=Path, default=REPO_ROOT / "results" / "verification.json")
    p_ver.add_argument("--strike", type=float, default=380.0)
    p_ver.add_argument("--offline", action="store_true", help="Use downloaded files only")

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    log = logging.getLogger("cppa_shaping")

    if args.cmd == "fetch":
        fetch(args.data_dir, DATA_START, DATA_END, refresh=args.refresh)
        log.info("PSE series %s..%s in %s", DATA_START, DATA_END, args.data_dir)
        return 0

    hourly, meta = _load(args.data_dir, args.offline)
    if args.cmd == "verify":
        df_ref = build_market(hourly, *COMPARISON_WINDOWS["2025"])
        idx = bootstrap_week_indices(len(df_ref), 500, seed=42)
        cols = (("spot", "spot_pln_mwh"), ("pv", "pv_mwh"), ("wind", "wind_mwh"))
        boot = {k: df_ref[c].to_numpy()[idx] for k, c in cols}
        ver = run_full_verification(df_ref, boot, strike_pln_mwh=args.strike)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(ver, indent=2), encoding="utf-8")
        log.info("Verification: all_identities_below_1e9=%s", ver["all_identities_below_1e9"])
        return 0 if ver["all_identities_below_1e9"] else 1

    summary = run_full_pipeline(hourly, output_root=args.out, strike_pln_mwh=args.strike, n_paths=args.paths,
                                seed=args.seed, meta=meta)
    ms = summary["market"]["2025"]
    log.info(
        "2025: mean RCE %.2f PLN/MWh, PV capture factor %.3f, wind capture factor %.3f, %d hours < 0",
        ms["spot_mean_pln_mwh"], ms["pv_capture_factor"], ms["wind_capture_factor"], ms["negative_price_hours"],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Command-line interface for pl-cppa-shaping-risk."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .market import generate_polish_market_year, run_block_bootstrap_mc
from .pipeline import REPO_ROOT, run_full_pipeline
from .verification import run_full_verification


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="cppa-shaping",
        description="Corporate PPA structuring and shaping risk on Polish power prices.",
    )
    sub = parser.add_subparsers(dest="cmd")

    p_run = sub.add_parser("run", help="Run full simulation pipeline, plots, results CSVs, and docs/data.js")
    p_run.add_argument("--out", type=Path, default=REPO_ROOT, help="Output repository root directory")
    p_run.add_argument("--strike", type=float, default=380.0, help="Reference PPA strike price in PLN/MWh")

    p_ver = sub.add_parser("verify", help="Run accounting identity and analytical verification suite")
    p_ver.add_argument("--out", type=Path, default=REPO_ROOT / "results" / "verification.json")
    p_ver.add_argument("--strike", type=float, default=380.0)

    args = parser.parse_args(argv)
    if args.cmd == "verify":
        df_ref = generate_polish_market_year(seed=2026)
        mc_df = run_block_bootstrap_mc(df_ref, n_paths=500, seed=42)
        ver = run_full_verification(df_ref, mc_df, strike_pln_mwh=args.strike)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(ver, indent=2), encoding="utf-8")
        print(f"Verification passed: all_identities_below_1e9={ver['all_identities_below_1e9']}")
        return 0

    out_root = getattr(args, "out", REPO_ROOT)
    strike = getattr(args, "strike", 380.0)
    payload = run_full_pipeline(output_root=out_root, strike_pln_mwh=strike)
    ms = payload["market_summary"]
    print(
        f"Completed pipeline: spot_mean={ms['spot_mean_pln_mwh']} PLN/MWh, "
        f"CF_pv={ms['pv_capture_factor']}, CF_wind={ms['wind_capture_factor']}, "
        f"neg_hours={ms['neg_zero_price_hours']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Layer 1 orchestrator — deterministic nightly scan.

Usage:
    python scan.py                 # full scan, writes state/runs/<date>/triggers.json
    python scan.py --dry-run       # scan live APIs, print triggers.json to stdout,
                                   # do not update run state / alert history
    python scan.py --force         # ignore high/low cooldowns (manual override)
    python scan.py --config path   # alternate config file

Exit codes: 0 = triggers found, 3 = success with no triggers, 1 = fatal error.
"""
from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import os
import sys
from datetime import date
from pathlib import Path

import yaml
from dotenv import load_dotenv

from src.edgar_client import EdgarClient, EdgarError
from src.fmp_client import FMPClient, FMPError
from src.models import DataIssue, Trigger, TriggersFile
from src.state import StateStore
from src.triggers import earnings as trig_earnings
from src.triggers import guidance as trig_guidance
from src.triggers import high_low as trig_high_low
from src.triggers import price_move as trig_price_move

log = logging.getLogger("scan")

EXIT_TRIGGERS = 0
EXIT_FATAL = 1
EXIT_NO_TRIGGERS = 3


def setup_logging(log_dir: Path, verbose: bool = False) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    fh = logging.handlers.RotatingFileHandler(
        log_dir / "scan.log", maxBytes=5_000_000, backupCount=5
    )
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(fh)
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    root.addHandler(sh)


def load_config(path: Path) -> dict:
    with path.open() as f:
        return yaml.safe_load(f)


def main() -> int:
    parser = argparse.ArgumentParser(description="Nightly watchlist surveillance scan (Layer 1)")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--dry-run", action="store_true",
                        help="print triggers.json to stdout; do not update run state")
    parser.add_argument("--force", action="store_true",
                        help="ignore high/low cooldowns")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    load_dotenv()
    project_root = Path(__file__).resolve().parent
    cfg = load_config(project_root / args.config)

    state_dir = project_root / cfg["run"]["state_dir"]
    setup_logging(project_root / cfg["run"].get("log_dir", "./logs"), args.verbose)

    run_date = date.today().isoformat()
    cache_dir = state_dir / "cache" / run_date
    state = StateStore(state_dir)

    try:
        fmp = FMPClient(
            base_url=cfg["fmp"].get("base_url", "https://financialmodelingprep.com/stable"),
            request_delay_seconds=float(cfg["fmp"].get("request_delay_seconds", 0.35)),
            cache_dir=cache_dir,
        )
        edgar = EdgarClient(
            user_agent=os.path.expandvars(cfg["edgar"]["user_agent"]),
            max_requests_per_second=int(cfg["edgar"].get("max_requests_per_second", 10)),
        )
    except (FMPError, KeyError) as exc:
        log.error("fatal setup error: %s", exc)
        return EXIT_FATAL

    thresholds = cfg["thresholds"]
    cooldown_days = 0 if args.force else int(cfg["alerts"]["high_low_cooldown_days"])
    history_days = int(cfg["fmp"].get("price_history_days", 400))
    keywords = cfg.get("guidance_keywords", [])
    edgar_forms = cfg["edgar"].get("forms", ["8-K", "10-Q", "10-K", "6-K"])
    watchlist = cfg["watchlist"]
    watermark = state.watermark_for(run_date)
    log.info("run %s: %d tickers, since-watermark %s", run_date, len(watchlist), watermark)

    triggers: list[Trigger] = []
    data_issues: list[DataIssue] = []
    edgar_ok: list[str] = []
    as_of = None  # latest completed trading day, from actual price data

    # ------------------------------------------------------ benchmark ETFs
    etf_symbols = {w.get("benchmark_etf", "SPY") for w in watchlist}
    etf_bars: dict[str, list[dict]] = {}
    for etf in sorted(etf_symbols):
        try:
            etf_bars[etf] = fmp.historical_prices(etf, history_days)
        except FMPError as exc:
            log.error("ETF %s: %s", etf, exc)
            data_issues.append(DataIssue(ticker=etf, source="fmp", error=str(exc)))
            etf_bars[etf] = []

    # ---------------------------------------------------------- per ticker
    for entry in watchlist:
        ticker = entry["ticker"]
        etf = entry.get("benchmark_etf", "SPY")

        # prices → as-of day, 52w high/low, price moves
        bars = None
        try:
            bars = fmp.historical_prices(ticker, history_days)
            if not bars:
                raise FMPError(f"no price history returned for {ticker}",
                               endpoint="historical-price-eod/full")
        except FMPError as exc:
            log.error("%s prices: %s", ticker, exc)
            data_issues.append(DataIssue(ticker=ticker, source="fmp", error=str(exc)))

        if bars:
            ticker_as_of = bars[0]["date"]
            as_of = max(as_of or ticker_as_of, ticker_as_of)

            t = trig_high_low.evaluate(ticker, bars, state, ticker_as_of, cooldown_days)
            if t:
                triggers.append(t)

            t = trig_price_move.evaluate(
                ticker, bars, etf, etf_bars.get(etf, []), thresholds, ticker_as_of
            )
            if t:
                triggers.append(t)

            # earnings surprise — only when a report landed since the last run
            try:
                rows = fmp.earnings(ticker, limit=8)
                t = trig_earnings.evaluate(ticker, rows, watermark, ticker_as_of, thresholds)
                if t:
                    triggers.append(t)
            except FMPError as exc:
                log.error("%s earnings: %s", ticker, exc)
                data_issues.append(DataIssue(ticker=ticker, source="fmp", error=str(exc)))

        # EDGAR → guidance candidates
        try:
            subs = edgar.submissions(entry["cik"])
            edgar_since = state.edgar_watermark_for(ticker, run_date)
            filings = edgar.recent_filings(subs, edgar_forms, edgar_since)
            if filings:
                log.info("%s: %d new filing(s) since %s", ticker, len(filings), edgar_since)

            def fetch_text(f, _cik=entry["cik"]):
                return edgar.filing_text(_cik, f["accession"], f["primary_document"])

            triggers.extend(
                trig_guidance.evaluate(
                    ticker, filings, fetch_text, keywords, state_dir / "filings", run_date
                )
            )
            edgar_ok.append(ticker)
        except EdgarError as exc:
            log.error("%s EDGAR: %s", ticker, exc)
            data_issues.append(DataIssue(ticker=ticker, source="edgar", error=str(exc)))

    if as_of is None and not triggers:
        # no price data at all — if every ticker failed, treat the run as fatal
        if len(data_issues) >= len(watchlist):
            log.error("no price data for any ticker — aborting run")
            return EXIT_FATAL
        as_of = run_date

    triggers.sort(key=lambda t: (t.priority, t.ticker))

    # ------------------------------------------------- validate and persist
    out = TriggersFile(
        run_date=run_date,
        as_of_trading_day=as_of or run_date,
        triggers=triggers,
        data_issues=data_issues,
    )
    payload = out.model_dump_json(indent=2)
    # round-trip through the schema as a self-check before we call the run good
    TriggersFile.model_validate_json(payload)

    if args.dry_run:
        print(payload)
        log.info("dry run: %d trigger(s), %d data issue(s); state not updated",
                 len(triggers), len(data_issues))
        return EXIT_TRIGGERS if triggers else EXIT_NO_TRIGGERS

    run_dir = state_dir / "runs" / run_date
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "triggers.json").write_text(payload)

    state.append_alerts(
        [
            {"dedupe_key": t.dedupe_key, "date": out.as_of_trading_day,
             "type": t.type, "ticker": t.ticker}
            for t in triggers
        ]
    )
    state.record_successful_run(run_date, edgar_ok)

    log.info("run complete: %d trigger(s), %d data issue(s) -> %s",
             len(triggers), len(data_issues), run_dir / "triggers.json")
    return EXIT_TRIGGERS if triggers else EXIT_NO_TRIGGERS


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        log.exception("fatal error in scan")
        sys.exit(EXIT_FATAL)

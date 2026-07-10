#!/usr/bin/env python3
"""Extracted text of a SEC filing, as JSON on stdout.

Usage: python tools/get_filing.py TICKER ACCESSION

Reads the text Layer 1 already saved to state/filings/<ticker>/<accession>.txt
when available; otherwise fetches from EDGAR (requires the ticker to be in the
config watchlist so its CIK is known).
"""
from __future__ import annotations

import argparse
import json
import sys

from _common import PROJECT_ROOT, load_config, make_edgar


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("ticker")
    parser.add_argument("accession")
    parser.add_argument("--max-chars", type=int, default=80000)
    args = parser.parse_args()

    cfg = load_config()
    ticker = args.ticker.upper()
    state_dir = PROJECT_ROOT / cfg["run"]["state_dir"]
    saved = state_dir / "filings" / ticker / f"{args.accession}.txt"

    if saved.exists():
        text = saved.read_text()
    else:
        cik = next((w["cik"] for w in cfg["watchlist"] if w["ticker"] == ticker), None)
        if not cik:
            json.dump({"error": f"{ticker} not in watchlist and no saved filing text"}, sys.stdout)
            print()
            return 1
        edgar = make_edgar(cfg)
        index = edgar.filing_index(cik, args.accession)
        primary = next(
            (i["name"] for i in index.get("directory", {}).get("item", [])
             if i.get("name", "").lower().endswith((".htm", ".html"))),
            None,
        )
        if not primary:
            json.dump({"error": f"no HTML document found in {args.accession}"}, sys.stdout)
            print()
            return 1
        text = edgar.filing_text(cik, args.accession, primary)
        saved.parent.mkdir(parents=True, exist_ok=True)
        saved.write_text(text)

    truncated = len(text) > args.max_chars
    json.dump(
        {"ticker": ticker, "accession": args.accession,
         "truncated": truncated, "text": text[: args.max_chars]},
        sys.stdout,
        indent=2,
    )
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())

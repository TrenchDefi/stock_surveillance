#!/usr/bin/env python3
"""Recent news for a ticker, as JSON on stdout.

Usage: python tools/get_news.py TICKER [--days 7] [--limit 15]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta

from _common import load_config, make_fmp


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("ticker")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--limit", type=int, default=15)
    args = parser.parse_args()

    cfg = load_config()
    fmp = make_fmp(cfg)
    cutoff = (date.today() - timedelta(days=args.days)).isoformat()

    items = fmp.stock_news(args.ticker.upper(), limit=max(args.limit, 15))
    out = [
        {
            "published": it.get("publishedDate"),
            "title": it.get("title"),
            "publisher": it.get("publisher") or it.get("site"),
            "summary": (it.get("text") or "")[:500],
            "url": it.get("url"),
        }
        for it in items
        if (it.get("publishedDate") or "")[:10] >= cutoff
    ][: args.limit]

    json.dump({"ticker": args.ticker.upper(), "since": cutoff, "news": out}, sys.stdout, indent=2)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())

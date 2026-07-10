#!/usr/bin/env python3
"""Most recent earnings-call transcript for a ticker, as JSON on stdout.

Usage: python tools/get_transcript.py TICKER [--max-chars 60000]
"""
from __future__ import annotations

import argparse
import json
import sys

from _common import load_config, make_fmp


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("ticker")
    parser.add_argument("--max-chars", type=int, default=60000,
                        help="truncate transcript content (budget guard)")
    args = parser.parse_args()

    cfg = load_config()
    fmp = make_fmp(cfg)
    t = fmp.latest_transcript(args.ticker.upper())
    if not t:
        json.dump({"ticker": args.ticker.upper(), "transcript": None,
                   "note": "no transcript available"}, sys.stdout, indent=2)
        print()
        return 0

    content = t.get("content", "")
    truncated = len(content) > args.max_chars
    json.dump(
        {
            "ticker": args.ticker.upper(),
            "period": t.get("period"),
            "year": t.get("year"),
            "date": t.get("date"),
            "truncated": truncated,
            "content": content[: args.max_chars],
        },
        sys.stdout,
        indent=2,
    )
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())

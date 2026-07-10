"""§4.3 — Guidance-revision candidates from new SEC filings.

Layer 1 only *detects*: any new 8-K / 10-Q / 10-K / 6-K since the last run
becomes a guidance_candidate, flagged higher-priority when the filing text
contains guidance keywords. Classification happens in Layer 2.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Callable

from ..models import Trigger
from . import PRIORITY_GUIDANCE

log = logging.getLogger(__name__)


def keyword_hits(text: str, keywords: list[str]) -> list[str]:
    """Case-insensitive whole-phrase matches; returns the keywords found."""
    hits = []
    low = text.lower()
    for kw in keywords:
        if re.search(r"(?<!\w)" + re.escape(kw.lower()) + r"(?!\w)", low):
            hits.append(kw)
    return hits


def evaluate(
    ticker: str,
    filings: list[dict],
    fetch_text: Callable[[dict], str],
    keywords: list[str],
    filings_dir: Path,
    as_of: str,
) -> list[Trigger]:
    """One trigger per new filing. `filings` come from EdgarClient.recent_filings.

    `fetch_text(filing)` returns the extracted document text; it is only called
    when the text is not already cached on disk (idempotent re-runs).
    """
    triggers = []
    for filing in filings:
        accession = filing["accession"]
        text_path = filings_dir / ticker / f"{accession}.txt"
        if text_path.exists():
            text = text_path.read_text()
        else:
            try:
                text = fetch_text(filing)
            except Exception as exc:
                log.warning("%s: could not fetch filing %s: %s", ticker, accession, exc)
                text = ""
            text_path.parent.mkdir(parents=True, exist_ok=True)
            text_path.write_text(text)

        hits = keyword_hits(text, keywords) if text else []
        triggers.append(
            Trigger(
                ticker=ticker,
                type="guidance_candidate",
                priority=PRIORITY_GUIDANCE,
                detail={
                    "form": filing["form"],
                    "accession": accession,
                    "filing_date": filing["filing_date"],
                    "keyword_hits": hits,
                    "keyword_flagged": bool(hits),
                    "text_path": str(text_path),
                    "text_chars": len(text),
                },
                dedupe_key=f"{ticker}:guidance_candidate:{accession}",
            )
        )
    return triggers

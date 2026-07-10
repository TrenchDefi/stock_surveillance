"""§4.1 — New 52-week high / low, with cooldown and monthly streak counting."""
from __future__ import annotations

import logging

from ..models import Trigger
from ..state import StateStore
from . import PRIORITY_HIGH_LOW

log = logging.getLogger(__name__)

TRADING_DAYS_52W = 252


def evaluate(
    ticker: str,
    bars: list[dict],
    state: StateStore,
    as_of: str,
    cooldown_days: int,
) -> Trigger | None:
    """`bars` is daily OHLCV newest-first; bars[0] must be the as-of trading day.

    Always records the streak (even while suppressed by cooldown) so the digest
    can report "Nth new high this month"; returns a Trigger only outside cooldown.
    """
    window = bars[:TRADING_DAYS_52W]
    if len(window) < 2:
        return None
    closes = [b["close"] for b in window]
    latest = closes[0]
    rest = closes[1:]

    if latest > max(rest):
        kind, ttype = "high", "high_52w"
    elif latest < min(rest):
        kind, ttype = "low", "low_52w"
    else:
        return None

    streak_count = state.record_extreme(ticker, kind, as_of)

    if state.in_cooldown(ticker, ttype, as_of, cooldown_days):
        log.info("%s: new 52w %s suppressed by cooldown (streak %d this month)",
                 ticker, kind, streak_count)
        return None

    prior_extreme = max(rest) if kind == "high" else min(rest)
    return Trigger(
        ticker=ticker,
        type=ttype,
        priority=PRIORITY_HIGH_LOW,
        detail={
            "close": latest,
            "prior_52w_extreme": prior_extreme,
            "streak_count_month": streak_count,
            "streak_month": as_of[:7],
        },
        dedupe_key=f"{ticker}:{ttype}:{as_of}",
    )

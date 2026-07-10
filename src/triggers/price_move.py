"""§4.4 — Significant price move with benchmark (relative) decomposition.

Windows that fire for the same ticker collapse into a single trigger with one
entry per window. Priority is 1 if any window is IDIOSYNCRATIC, else 4.
"""
from __future__ import annotations

import logging

from ..models import MoveWindow, Trigger
from . import PRIORITY_IDIOSYNCRATIC_MOVE, PRIORITY_MARKET_MOVE

log = logging.getLogger(__name__)

# window label -> (trading days back, threshold config key)
WINDOWS = [
    ("1d", 1, "move_1d_pct"),
    ("1w", 5, "move_1w_pct"),
    ("1m", 21, "move_1m_pct"),
]


def total_return_pct(closes: list[float], days_back: int) -> float | None:
    """Return over `days_back` trading days; closes are newest-first."""
    if len(closes) <= days_back or closes[days_back] == 0:
        return None
    return (closes[0] / closes[days_back] - 1.0) * 100.0


def pct_explained(stock_ret: float, etf_ret: float) -> float:
    """Share of the stock move explained by the benchmark, per §4.4.

    clamp(etf/stock, 0..1) * 100 when signs match; 0 when signs differ,
    the ETF is flat, or the stock return is zero.
    """
    if stock_ret == 0 or etf_ret == 0 or (stock_ret > 0) != (etf_ret > 0):
        return 0.0
    return max(0.0, min(1.0, etf_ret / stock_ret)) * 100.0


def evaluate(
    ticker: str,
    stock_bars: list[dict],
    etf_symbol: str,
    etf_bars: list[dict],
    thresholds: dict,
    as_of: str,
) -> Trigger | None:
    stock_closes = [b["close"] for b in stock_bars]
    etf_by_date = {b["date"]: b["close"] for b in etf_bars}
    # align the ETF series to the stock's trading days so windows match exactly
    etf_closes = [etf_by_date.get(b["date"]) for b in stock_bars]

    flag_pct = float(thresholds["relative_move_flag_pct"])
    windows: list[MoveWindow] = []

    for label, days_back, threshold_key in WINDOWS:
        stock_ret = total_return_pct(stock_closes, days_back)
        if stock_ret is None or abs(stock_ret) < float(thresholds[threshold_key]):
            continue

        etf_ret = None
        if None not in (etf_closes[0:1] or [None]) and len(etf_closes) > days_back:
            start, end = etf_closes[days_back], etf_closes[0]
            if start and end:
                etf_ret = (end / start - 1.0) * 100.0
        if etf_ret is None:
            log.warning("%s: no aligned %s data for window %s; treating ETF move as 0",
                        ticker, etf_symbol, label)
            etf_ret = 0.0

        explained = pct_explained(stock_ret, etf_ret)
        windows.append(
            MoveWindow(
                window=label,
                stock_return_pct=round(stock_ret, 2),
                etf=etf_symbol,
                etf_return_pct=round(etf_ret, 2),
                relative_move_pct=round(stock_ret - etf_ret, 2),
                pct_explained=round(explained, 1),
                classification=(
                    "MARKET/SECTOR-DRIVEN" if explained >= flag_pct else "IDIOSYNCRATIC"
                ),
            )
        )

    if not windows:
        return None

    idiosyncratic = any(w.classification == "IDIOSYNCRATIC" for w in windows)
    return Trigger(
        ticker=ticker,
        type="price_move",
        windows=windows,
        priority=PRIORITY_IDIOSYNCRATIC_MOVE if idiosyncratic else PRIORITY_MARKET_MOVE,
        detail={"windows_fired": [w.window for w in windows]},
        dedupe_key=f"{ticker}:price_move:{as_of}",
    )

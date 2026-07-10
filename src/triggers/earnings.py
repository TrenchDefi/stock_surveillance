"""§4.2 — Earnings surprise vs. consensus, with near-zero-estimate guard rail."""
from __future__ import annotations

import logging

from ..models import Trigger
from . import PRIORITY_EARNINGS

log = logging.getLogger(__name__)

# tolerance so a surprise computed as 14.999999...% still meets a 15% threshold
_EPS = 1e-9


def _surprise_pct(actual: float, estimate: float) -> float:
    return abs(actual - estimate) / abs(estimate) * 100.0


def evaluate(
    ticker: str,
    earnings_rows: list[dict],
    last_run_date: str,
    as_of: str,
    thresholds: dict,
) -> Trigger | None:
    """Evaluate the most recent report published since the last successful run.

    `earnings_rows` are FMP /stable/earnings rows (newest first):
    {date, epsActual, epsEstimated, revenueActual, revenueEstimated}.
    Only rows with last_run_date < date <= as_of and a reported actual count.
    """
    report = None
    for row in earnings_rows:
        d = row.get("date", "")
        if last_run_date < d <= as_of and (
            row.get("epsActual") is not None or row.get("revenueActual") is not None
        ):
            report = row
            break
    if report is None:
        return None

    eps_a, eps_e = report.get("epsActual"), report.get("epsEstimated")
    rev_a, rev_e = report.get("revenueActual"), report.get("revenueEstimated")

    eps_threshold = float(thresholds["eps_surprise_pct"])
    rev_threshold = float(thresholds["revenue_surprise_pct"])
    eps_floor = float(thresholds.get("eps_estimate_floor_usd", 0.05))
    eps_abs_threshold = float(thresholds.get("eps_surprise_abs_usd", 0.05))

    detail: dict = {"report_date": report["date"]}
    fired = []

    if eps_a is not None and eps_e is not None:
        eps_delta = eps_a - eps_e
        detail["eps"] = {
            "actual": eps_a,
            "estimate": eps_e,
            "delta_usd": round(eps_delta, 4),
            "direction": "beat" if eps_delta >= 0 else "miss",
        }
        if abs(eps_e) < eps_floor:
            # near-zero consensus: percentage is meaningless, use absolute delta
            detail["eps"]["near_zero_estimate"] = True
            if abs(eps_delta) >= eps_abs_threshold - _EPS:
                fired.append("eps_abs")
        else:
            pct = _surprise_pct(eps_a, eps_e)
            detail["eps"]["surprise_pct"] = round(pct, 2)
            if pct >= eps_threshold - _EPS:
                fired.append("eps")

    if rev_a is not None and rev_e is not None and rev_e != 0:
        rev_pct = _surprise_pct(rev_a, rev_e)
        detail["revenue"] = {
            "actual": rev_a,
            "estimate": rev_e,
            "surprise_pct": round(rev_pct, 2),
            "direction": "beat" if rev_a >= rev_e else "miss",
        }
        if rev_pct >= rev_threshold - _EPS:
            fired.append("revenue")

    if not fired:
        return None

    detail["fired_on"] = fired
    return Trigger(
        ticker=ticker,
        type="earnings_surprise",
        priority=PRIORITY_EARNINGS,
        detail=detail,
        dedupe_key=f"{ticker}:earnings_surprise:{report['date']}",
    )

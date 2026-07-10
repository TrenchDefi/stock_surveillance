import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from src.state import StateStore

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def state(tmp_path):
    return StateStore(tmp_path / "state")


def make_bars(closes: list[float], start_date: str = "2026-07-09") -> list[dict]:
    """Synthetic daily bars, newest-first, on consecutive weekdays counting back."""
    from datetime import date, timedelta

    d = date.fromisoformat(start_date)
    bars = []
    for close in closes:
        while d.weekday() >= 5:
            d -= timedelta(days=1)
        bars.append({"date": d.isoformat(), "close": close, "open": close,
                     "high": close, "low": close, "volume": 1000})
        d -= timedelta(days=1)
    return bars

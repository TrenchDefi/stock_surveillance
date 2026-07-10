from src.triggers import price_move
from tests.conftest import make_bars

THRESHOLDS = {
    "move_1d_pct": 5,
    "move_1w_pct": 10,
    "move_1m_pct": 15,
    "relative_move_flag_pct": 60,
}


def test_pct_explained_same_sign():
    assert price_move.pct_explained(-6.2, -1.1) == (1.1 / 6.2) * 100


def test_pct_explained_opposite_signs_is_zero():
    assert price_move.pct_explained(-6.2, 1.5) == 0.0


def test_pct_explained_zero_etf_move():
    assert price_move.pct_explained(-6.2, 0.0) == 0.0


def test_pct_explained_etf_bigger_clamps_to_100():
    assert price_move.pct_explained(5.0, 8.0) == 100.0


def test_1d_idiosyncratic_move():
    stock = make_bars([93.8, 100.0] + [100.0] * 30)  # -6.2%
    etf = make_bars([98.9, 100.0] + [100.0] * 30)    # -1.1%
    t = price_move.evaluate("AAPL", stock, "XLK", etf, THRESHOLDS, stock[0]["date"])
    assert t is not None
    w = t.windows[0]
    assert w.window == "1d"
    assert w.classification == "IDIOSYNCRATIC"
    assert w.stock_return_pct == -6.2
    assert abs(w.pct_explained - 17.7) < 0.1
    assert t.priority == 1


def test_market_driven_move():
    stock = make_bars([94.0, 100.0] + [100.0] * 30)  # -6.0%
    etf = make_bars([95.0, 100.0] + [100.0] * 30)    # -5.0% -> 83% explained
    t = price_move.evaluate("AAPL", stock, "XLK", etf, THRESHOLDS, stock[0]["date"])
    assert t is not None
    assert t.windows[0].classification == "MARKET/SECTOR-DRIVEN"
    assert t.priority == 4


def test_below_threshold_no_trigger():
    stock = make_bars([104.0, 100.0] + [100.0] * 30)  # +4% < 5%
    etf = make_bars([100.0] * 32)
    assert price_move.evaluate("AAPL", stock, "XLK", etf, THRESHOLDS, stock[0]["date"]) is None


def test_multiple_windows_collapse_to_one_trigger():
    # -6% in 1d and -12% over 5d, flat ETF
    closes = [88.0, 93.6] + [100.0] * 30
    stock = make_bars(closes)
    etf = make_bars([100.0] * 32)
    t = price_move.evaluate("AAPL", stock, "XLK", etf, THRESHOLDS, stock[0]["date"])
    assert t is not None
    labels = [w.window for w in t.windows]
    assert "1d" in labels and "1w" in labels
    assert len([t]) == 1


def test_missing_etf_data_treated_as_zero():
    stock = make_bars([93.0, 100.0] + [100.0] * 30)
    t = price_move.evaluate("AAPL", stock, "XLK", [], THRESHOLDS, stock[0]["date"])
    assert t is not None
    assert t.windows[0].etf_return_pct == 0.0
    assert t.windows[0].classification == "IDIOSYNCRATIC"

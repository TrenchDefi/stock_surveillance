from src.triggers import earnings

THRESHOLDS = {
    "eps_surprise_pct": 15,
    "revenue_surprise_pct": 5,
    "eps_estimate_floor_usd": 0.05,
    "eps_surprise_abs_usd": 0.05,
}


def row(date="2026-07-08", eps_a=None, eps_e=None, rev_a=None, rev_e=None):
    return {"date": date, "epsActual": eps_a, "epsEstimated": eps_e,
            "revenueActual": rev_a, "revenueEstimated": rev_e}


def test_eps_beat_fires():
    rows = [row(eps_a=2.30, eps_e=2.00)]  # +15%
    t = earnings.evaluate("AAPL", rows, "2026-07-01", "2026-07-09", THRESHOLDS)
    assert t is not None
    assert "eps" in t.detail["fired_on"]
    assert t.detail["eps"]["direction"] == "beat"
    assert t.detail["eps"]["surprise_pct"] == 15.0


def test_eps_miss_fires_both_directions():
    rows = [row(eps_a=1.70, eps_e=2.00)]  # -15%
    t = earnings.evaluate("AAPL", rows, "2026-07-01", "2026-07-09", THRESHOLDS)
    assert t is not None
    assert t.detail["eps"]["direction"] == "miss"


def test_below_threshold_no_fire():
    rows = [row(eps_a=2.10, eps_e=2.00, rev_a=102.0e9, rev_e=100.0e9)]  # +5% eps, +2% rev
    assert earnings.evaluate("AAPL", rows, "2026-07-01", "2026-07-09", THRESHOLDS) is None


def test_revenue_fires_and_both_metrics_recorded():
    rows = [row(eps_a=2.01, eps_e=2.00, rev_a=106.0e9, rev_e=100.0e9)]  # +6% rev
    t = earnings.evaluate("AAPL", rows, "2026-07-01", "2026-07-09", THRESHOLDS)
    assert t is not None
    assert t.detail["fired_on"] == ["revenue"]
    assert "eps" in t.detail  # both metrics recorded regardless of which fired


def test_near_zero_estimate_guard():
    # |estimate| < $0.05: percentage would be meaningless (1500%)
    rows = [row(eps_a=0.16, eps_e=0.01)]
    t = earnings.evaluate("AAPL", rows, "2026-07-01", "2026-07-09", THRESHOLDS)
    assert t is not None
    assert t.detail["eps"]["near_zero_estimate"] is True
    assert "surprise_pct" not in t.detail["eps"]
    assert t.detail["fired_on"] == ["eps_abs"]


def test_near_zero_estimate_small_delta_no_fire():
    rows = [row(eps_a=0.03, eps_e=0.01)]  # delta $0.02 < $0.05 abs threshold
    assert earnings.evaluate("AAPL", rows, "2026-07-01", "2026-07-09", THRESHOLDS) is None


def test_only_reports_since_last_run():
    rows = [row(date="2026-06-15", eps_a=3.00, eps_e=2.00)]  # big beat, but old
    assert earnings.evaluate("AAPL", rows, "2026-07-01", "2026-07-09", THRESHOLDS) is None


def test_future_report_with_null_actuals_skipped():
    rows = [row(date="2026-07-30"), row(date="2026-07-05", eps_a=2.40, eps_e=2.00)]
    t = earnings.evaluate("AAPL", rows, "2026-07-01", "2026-07-09", THRESHOLDS)
    assert t is not None
    assert t.detail["report_date"] == "2026-07-05"

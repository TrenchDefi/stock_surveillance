from src.triggers import high_low
from tests.conftest import make_bars


def test_new_high_fires(state):
    bars = make_bars([110.0] + [100.0] * 260)
    t = high_low.evaluate("AAPL", bars, state, bars[0]["date"], cooldown_days=10)
    assert t is not None
    assert t.type == "high_52w"
    assert t.detail["prior_52w_extreme"] == 100.0
    assert t.detail["streak_count_month"] == 1


def test_new_low_fires(state):
    bars = make_bars([90.0] + [100.0] * 260)
    t = high_low.evaluate("AAPL", bars, state, bars[0]["date"], cooldown_days=10)
    assert t is not None
    assert t.type == "low_52w"


def test_no_trigger_mid_range(state):
    bars = make_bars([100.0, 95.0, 105.0] + [100.0] * 258)
    assert high_low.evaluate("AAPL", bars, state, bars[0]["date"], cooldown_days=10) is None


def test_only_looks_back_252_days(state):
    # higher close exists but outside the 252-day window
    bars = make_bars([110.0] + [100.0] * 251 + [120.0] * 10)
    t = high_low.evaluate("AAPL", bars, state, bars[0]["date"], cooldown_days=10)
    assert t is not None and t.type == "high_52w"


def test_cooldown_suppresses_but_streak_counts(state):
    bars1 = make_bars([110.0] + [100.0] * 260, start_date="2026-07-06")
    t1 = high_low.evaluate("AAPL", bars1, state, "2026-07-06", cooldown_days=10)
    assert t1 is not None
    state.append_alerts([{"dedupe_key": t1.dedupe_key, "date": "2026-07-06",
                          "type": "high_52w", "ticker": "AAPL"}])

    # two days later: another new high, inside cooldown -> suppressed
    bars2 = make_bars([112.0, 110.0] + [100.0] * 260, start_date="2026-07-08")
    t2 = high_low.evaluate("AAPL", bars2, state, "2026-07-08", cooldown_days=10)
    assert t2 is None

    # streak kept counting through the suppression
    assert state.record_extreme("AAPL", "high", "2026-07-08") == 2  # idempotent same-day

    # after cooldown expires the trigger fires again and reports the streak
    bars3 = make_bars([115.0, 112.0, 110.0] + [100.0] * 260, start_date="2026-07-20")
    t3 = high_low.evaluate("AAPL", bars3, state, "2026-07-20", cooldown_days=10)
    assert t3 is not None
    assert t3.detail["streak_count_month"] == 3


def test_same_day_rerun_is_idempotent(state):
    bars = make_bars([110.0] + [100.0] * 260)
    as_of = bars[0]["date"]
    t1 = high_low.evaluate("AAPL", bars, state, as_of, cooldown_days=10)
    state.append_alerts([{"dedupe_key": t1.dedupe_key, "date": as_of,
                          "type": "high_52w", "ticker": "AAPL"}])
    # same night rerun: fires again with the same dedupe_key, same streak count
    t2 = high_low.evaluate("AAPL", bars, state, as_of, cooldown_days=10)
    assert t2 is not None
    assert t2.dedupe_key == t1.dedupe_key
    assert t2.detail["streak_count_month"] == t1.detail["streak_count_month"]
    # and history does not grow
    state.append_alerts([{"dedupe_key": t2.dedupe_key, "date": as_of,
                          "type": "high_52w", "ticker": "AAPL"}])
    assert len(state.load_history()) == 1


def test_streak_resets_new_month(state):
    assert state.record_extreme("AAPL", "high", "2026-06-29") == 1
    assert state.record_extreme("AAPL", "high", "2026-07-01") == 1

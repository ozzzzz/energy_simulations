import pandas as pd
import pytest

from app.simulations.sim1.resample import aggregate_series, aggregate_state, plan_buckets, thin
from app.simulations.sim1.telemetry import SeriesSpec


def _frame(n: int, dt: float = 10.0) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "t": [i * dt for i in range(n)],
            "dt": [dt] * n,
            "p_kw": [float(i) for i in range(n)],
            "state": ["online" if i < n // 2 else "offline" for i in range(n)],
        }
    )


def test_a_short_frame_is_left_alone() -> None:
    df = _frame(50)
    buckets = plan_buckets(df["t"], df["dt"], target_points=100)
    assert len(buckets) == 50
    assert all(len(group) == 1 for group in buckets.groups)


def test_bucketing_respects_the_point_budget() -> None:
    df = _frame(4000)
    buckets = plan_buckets(df["t"], df["dt"], target_points=200)
    assert 150 <= len(buckets) <= 260
    assert sum(len(group) for group in buckets.groups) == 4000


def test_event_windows_keep_their_resolution_while_the_rest_collapses() -> None:
    df = _frame(4000, dt=1.0)
    buckets = plan_buckets(df["t"], df["dt"], target_points=200, windows=[(1000.0, 1100.0)], window_step_s=2.0)

    inside = [group for group in buckets.groups if 1000.0 <= df["t"].iloc[group[0]] < 1100.0]
    outside = [group for group in buckets.groups if df["t"].iloc[group[0]] >= 2000.0]

    assert max(len(group) for group in inside) <= 2
    assert min(len(group) for group in outside) > 5


def test_a_bucket_never_mixes_protected_and_unprotected_rows() -> None:
    df = _frame(2000, dt=1.0)
    buckets = plan_buckets(df["t"], df["dt"], target_points=100, windows=[(500.0, 600.0)], window_step_s=2.0)
    for group in buckets.groups:
        flags = {500.0 <= df["t"].iloc[i] < 600.0 for i in group}
        assert len(flags) == 1


def test_rate_aggregation_is_weighted_by_dt() -> None:
    df = pd.DataFrame({"t": [0.0, 1.0, 2.0], "dt": [1.0, 99.0, 1.0], "p_kw": [100.0, 0.0, 100.0]})
    buckets = plan_buckets(df["t"], df["dt"], target_points=1)
    spec = SeriesSpec("p_kw", 0.1)
    # A plain mean would say 66.7 kW. Weighting by dt gives 200/101 kW, the only
    # answer whose product with the bucket's 101 s span is the true energy.
    assert aggregate_series(df, spec, buckets)[0] == pytest.approx(200.0 / 101.0)


def test_gaps_stay_gaps_rather_than_becoming_zero() -> None:
    df = pd.DataFrame({"t": [0.0, 1.0], "dt": [1.0, 1.0], "p_kw": [float("nan"), float("nan")]})
    buckets = plan_buckets(df["t"], df["dt"], target_points=1)
    assert aggregate_series(df, SeriesSpec("p_kw", 0.1), buckets)[0] is None


def test_state_aggregation_takes_the_last_value_not_an_average() -> None:
    df = _frame(100)
    buckets = plan_buckets(df["t"], df["dt"], target_points=4)
    labels = aggregate_state(df, "state", buckets)
    assert set(labels) <= {"online", "offline"}
    assert labels[0] == "online"
    assert labels[-1] == "offline"


def test_thinning_returns_a_frame_of_the_requested_size() -> None:
    df = _frame(3000)
    thinned = thin(df, 300)
    assert 200 <= len(thinned) <= 400
    assert list(thinned.columns) == list(df.columns)
    assert thin(df, 5000) is df


def test_windows_cannot_eat_the_whole_point_budget() -> None:
    """A small budget against long event windows used to leave one bucket for
    everything outside them, which draws a plausible and useless chart."""
    df = _frame(4000, dt=1.0)
    buckets = plan_buckets(df["t"], df["dt"], target_points=100, windows=[(0.0, 3000.0)], window_step_s=2.0)

    outside = [group for group in buckets.groups if df["t"].iloc[group[0]] >= 3000.0]
    assert len(outside) > 5, "the unprotected tail collapsed into too few buckets"
    assert len(buckets) <= 160

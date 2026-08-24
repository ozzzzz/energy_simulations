"""The visualization payload: correct after quantization, and small enough to open."""

import json

import pytest

from app.simulations.sim1.engine import run_scenario
from app.simulations.sim1.resample import plan_buckets
from app.simulations.sim1.scenarios import get_scenario
from app.simulations.sim1.telemetry import FACILITY_SERIES
from app.simulations.sim1.viz.payload import build_payload, dumps


@pytest.fixture(scope="module")
def run():
    name = "grid_outage_gen_ok"
    result = run_scenario(name, duration_s=3600.0)
    return result, get_scenario(name), build_payload(result, get_scenario(name), target_points=400)


def test_every_series_is_the_same_length_as_the_time_axis(run) -> None:
    _, _, payload = run
    points = len(payload["t"])
    for name, entry in payload["series"].items():
        assert len(entry["v"]) == points, name
    for name, entry in payload["states"].items():
        assert len(entry["v"]) == points, name
    for rack, entry in payload["racks"].items():
        for key, sub in entry.items():
            values = sub["v"]
            assert len(values) == points, f"{rack}.{key}"


def test_dequantized_values_match_the_frame_within_their_own_step(run) -> None:
    result, scenario, payload = run
    df = result.facility
    windows = tuple((event.t - 60.0, event.t + 600.0) for event in scenario.events)
    buckets = plan_buckets(df["t"].tolist(), df["dt"].tolist(), 400, windows, window_step_s=2.0)
    specs = {spec.column: spec for spec in FACILITY_SERIES}

    # A 'last' aggregation is exactly reproducible, so it is the honest check on
    # the quantization itself.
    for name in ("a_batt_soc", "cool_loop_supply_c", "gen_fuel_l"):
        spec = specs[name]
        entry = payload["series"][name]
        for index, group in enumerate(buckets.groups):
            expected = float(df[name].iloc[list(group)].dropna().iloc[-1])
            got = entry["v"][index] * entry["scale"]
            assert abs(got - expected) <= spec.scale, (name, index, got, expected)


def test_rate_series_preserve_energy_through_downsampling(run) -> None:
    result, _, payload = run
    df = result.facility
    exact_kwh = float((df["it_drawn_kw"] * df["dt"]).sum() / 3600.0)

    entry = payload["series"]["it_drawn_kw"]
    times = payload["t"]
    total = 0.0
    for index, value in enumerate(entry["v"]):
        span = (times[index + 1] - times[index]) if index + 1 < len(times) else df["dt"].iloc[-1]
        total += (value or 0) * entry["scale"] * span / 3600.0

    assert total == pytest.approx(exact_kwh, rel=0.01)


def test_peak_matters_columns_ship_a_companion_series(run) -> None:
    _, _, payload = run
    for name in ("it_unserved_kw__max", "a_ups_load_pct__max", "a_batt_soc__min", "rack_temp_max_c__max"):
        assert name in payload["series"], name

    # And the companion really is an extreme, not a second average.
    mean = payload["series"]["a_ups_load_pct"]
    peak = payload["series"]["a_ups_load_pct__max"]
    assert all(p >= m for p, m in zip(peak["v"], mean["v"], strict=True))


def test_the_json_carries_no_token_that_json_parse_would_reject(run) -> None:
    _, _, payload = run
    text = dumps(payload)
    assert "Infinity" not in text
    assert "NaN" not in text
    # And it round-trips through a strict parser.
    json.loads(text.replace("\\u003c", "<"))


def test_the_payload_cannot_close_its_own_script_element(run) -> None:
    _, _, payload = run
    payload["description"] = "oops </script><script>alert(1)</script>"
    text = dumps(payload)
    assert "</script>" not in text
    assert "\\u003c/script" in text


def test_events_survive_at_full_resolution(run) -> None:
    result, _, payload = run
    assert len(payload["events"]) == len(result.events)
    scheduled = [event for event in payload["events"] if event["kind"] == "scheduled"]
    assert {event["t"] for event in scheduled} == {1800.0}


def test_a_week_long_run_stays_under_a_megabyte() -> None:
    """The size budget the whole payload design exists to meet."""
    name = "normal"
    result = run_scenario(name, duration_s=7 * 86400.0)
    payload = build_payload(result, get_scenario(name), target_points=2500)
    size_kb = len(dumps(payload)) / 1024

    assert result.kpis["ticks"] > 10_000
    assert payload["points"] <= 2600
    assert size_kb < 1024, f"payload is {size_kb:.0f} KB"

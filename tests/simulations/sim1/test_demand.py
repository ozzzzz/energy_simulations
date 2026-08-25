"""User traffic: arrivals, the queue, and the loop back from physics to users."""

import math

import pytest

from app.simulations.sim1.demand import RequestMix, Surge, UserArrivals, UserLoad
from app.simulations.sim1.engine import run_scenario
from app.simulations.sim1.protocols import TickContext
from app.simulations.sim1.rack import Rack, RackState
from app.simulations.sim1.workload import WorkloadProfile


def _ctx(t: float, dt: float = 60.0) -> TickContext:
    return TickContext(t=t, dt=dt, tick=int(t / dt))


def _racks(profiles: tuple[str, ...] = ("inference", "inference")) -> list[Rack]:
    return [
        Rack(name=f"rack-{i + 1}", workload=WorkloadProfile(profile, noise_ratio=0.0, seed=i))
        for i, profile in enumerate(profiles)
    ]


def _load(**kwargs: float) -> UserLoad:
    arrivals = UserArrivals(peak_rps=kwargs.pop("peak_rps", 152.0), burstiness=0.0, **kwargs)  # type: ignore[arg-type]
    return UserLoad(arrivals=arrivals, mix=RequestMix(tokens_per_request=420.0, slo_latency_s=8.0))


def _serve(load: UserLoad, racks: list[Rack], ctx: TickContext, capacity_scale: float = 1.0):
    asks = load.plan(ctx, racks)
    for rack, ask in zip(racks, asks, strict=True):
        rack.apply(ctx, granted_kw=rack.request(ctx, ask) * capacity_scale, sink_c=20.0)
    return load.settle(ctx, racks)


# --- arrivals ---------------------------------------------------------------


def test_traffic_follows_a_daily_cycle() -> None:
    arrivals = UserArrivals(peak_rps=100.0, low_fraction=0.35, peak_hour=14.0, burstiness=0.0)
    at_peak = arrivals.rps(14 * 3600.0)
    at_trough = arrivals.rps(2 * 3600.0)

    assert at_peak == pytest.approx(100.0)
    assert at_trough == pytest.approx(35.0)


def test_a_surge_ramps_in_and_out_rather_than_stepping() -> None:
    surge = Surge(start_s=100.0, end_s=1000.0, multiplier=3.0, ramp_s=100.0)

    assert surge.factor(99.0) == 1.0
    assert surge.factor(150.0) == pytest.approx(2.0)
    assert surge.factor(500.0) == pytest.approx(3.0)
    assert surge.factor(950.0) == pytest.approx(2.0)
    assert surge.factor(1000.0) == 1.0


def test_arrivals_are_reproducible_for_a_seed_and_differ_across_seeds() -> None:
    a = UserArrivals(peak_rps=100.0, burstiness=0.2, seed=5)
    b = UserArrivals(peak_rps=100.0, burstiness=0.2, seed=5)
    c = UserArrivals(peak_rps=100.0, burstiness=0.2, seed=6)
    times = [float(i) * 60.0 for i in range(20)]

    assert [a.rps(t) for t in times] == [b.rps(t) for t in times]
    assert [a.rps(t) for t in times] != [c.rps(t) for t in times]


# --- the loop from traffic to power and back --------------------------------


def test_power_follows_traffic() -> None:
    """The whole point: a rack's draw is set by the work handed to it."""
    load = _load()
    racks = _racks()

    _serve(load, racks, _ctx(2 * 3600.0))
    quiet_kw = sum(rack.drawn_kw for rack in racks)
    _serve(load, racks, _ctx(14 * 3600.0))
    busy_kw = sum(rack.drawn_kw for rack in racks)

    assert busy_kw > quiet_kw * 1.5
    assert all(rack.drawn_kw >= rack.idle_kw for rack in racks)


def test_a_cluster_inside_its_capacity_queues_nothing() -> None:
    load = _load(peak_rps=60.0)
    racks = _racks()
    for i in range(30):
        result = _serve(load, racks, _ctx(14 * 3600.0 + i * 60.0))

    assert result.served_rps == pytest.approx(result.offered_rps, rel=1e-6)
    assert result.dropped_rps == 0.0
    assert result.queued_requests == pytest.approx(0.0)
    assert load.requests_dropped == 0.0


def test_traffic_beyond_capacity_queues_then_drops() -> None:
    load = _load(peak_rps=400.0)
    racks = _racks()

    queued: list[float] = []
    dropped: list[float] = []
    for i in range(40):
        result = _serve(load, racks, _ctx(14 * 3600.0 + i * 30.0, dt=30.0))
        queued.append(result.queued_requests)
        dropped.append(result.dropped_rps)

    assert max(queued) > 0.0, "the queue should build before anything is dropped"
    assert max(dropped) > 0.0
    assert queued.index(max(queued)) <= dropped.index(max(dropped))
    assert load.drop_pct > 0.0
    assert load.slo_compliance_pct < 100.0


def test_losing_serving_capacity_drops_requests_even_with_power_to_spare() -> None:
    """The link the whole demand layer exists for: a thermal event becomes a
    user-visible one, with the electrical chain never involved."""
    load = _load()
    racks = _racks()
    for i in range(10):
        _serve(load, racks, _ctx(14 * 3600.0 + i * 60.0))
    assert load.requests_dropped == 0.0

    for rack in racks:
        rack.state = RackState.EMERGENCY_SHUTDOWN
    result = _serve(load, racks, _ctx(15 * 3600.0))

    assert result.capacity_rps == 0.0
    assert result.served_rps == 0.0
    assert result.dropped_rps > 0.0
    # Undefined, not zero: reporting an idle cluster here would be the opposite
    # of what happened.
    assert math.isinf(load.utilisation)
    # Latency reports the budget, not infinity: a saturated queue's wait *is* the
    # budget, and everything past it has already been abandoned.
    assert result.queue_latency_s == pytest.approx(load.mix.slo_latency_s)


def test_a_throttled_cluster_serves_less_than_a_healthy_one() -> None:
    load = _load()
    racks = _racks()
    _serve(load, racks, _ctx(14 * 3600.0))
    healthy = load.capacity_rps

    for rack in racks:
        rack.state = RackState.THROTTLING
    _serve(load, racks, _ctx(14 * 3600.0 + 60.0))

    assert 0.0 < load.capacity_rps < healthy


def test_no_interactive_racks_means_no_user_traffic_at_all() -> None:
    load = _load()
    racks = _racks(("training", "training"))
    result = _serve(load, racks, _ctx(14 * 3600.0))

    assert result.offered_rps == 0.0
    assert result.capacity_rps == 0.0
    assert result.dropped_rps == 0.0
    # Batch racks still run: they follow their own profile.
    assert sum(rack.drawn_kw for rack in racks) > 0.0


def test_batch_shortfall_is_owed_rather_than_lost() -> None:
    load = _load()
    racks = _racks(("training", "training"))
    for i in range(20):
        _serve(load, racks, _ctx(i * 60.0), capacity_scale=0.25)

    assert load.batch_backlog_kwh > 0.0


def test_the_queue_never_serves_more_than_arrived_plus_backlog() -> None:
    """Guards against the queue manufacturing throughput out of spare capacity."""
    load = _load(peak_rps=20.0)
    racks = _racks()
    for i in range(30):
        result = _serve(load, racks, _ctx(14 * 3600.0 + i * 60.0))
        assert result.served_rps <= result.offered_rps + 1e-6
    assert load.requests_served == pytest.approx(load.requests_offered, rel=1e-9)


# --- end to end -------------------------------------------------------------


def test_the_surge_scenario_drops_requests_without_any_power_problem() -> None:
    result = run_scenario("user_surge")
    k = result.kpis

    assert float(k["request_drop_pct"]) > 1.0
    assert float(k["peak_utilisation_pct"]) > 100.0
    assert float(k["p95_queue_latency_s"]) > 0.0
    # The constraint is compute, not kilowatts: nothing tripped, nothing throttled.
    assert k["uptime_pct"] == 100.0
    assert k["throttle_events"] == 0.0
    assert k["overload_seconds"] == 0.0
    assert float(k["max_ups_load_pct_b"]) < 80.0


def test_a_cooling_failure_shows_up_as_dropped_user_requests() -> None:
    result = run_scenario("cooling_failure")
    k = result.kpis
    df = result.facility

    assert float(k["request_drop_pct"]) > 5.0
    # Capacity collapses before the requests do, because throttling comes first.
    dark = df[df["user_capacity_rps"] <= 0.0]
    assert not dark.empty
    assert float(dark["user_dropped_rps"].max()) > 0.0


def test_a_clean_outage_costs_users_nothing() -> None:
    k = run_scenario("grid_outage_gen_ok").kpis
    assert k["request_drop_pct"] == 0.0
    assert k["slo_compliance_pct"] == 100.0
    assert float(k["time_on_battery_s"]) > 0.0


def test_the_user_columns_are_present_and_finite_where_defined() -> None:
    df = run_scenario("normal", duration_s=3600.0).facility
    for column in (
        "user_offered_rps",
        "user_served_rps",
        "user_dropped_rps",
        "user_capacity_rps",
        "user_queued_requests",
        "user_queue_latency_s",
        "user_utilisation_pct",
        "user_batch_backlog_kwh",
    ):
        assert column in df.columns, column
    assert (df["user_offered_rps"] > 0.0).all()
    assert df["user_served_rps"].notna().all()


# --- the wall clock ---------------------------------------------------------


def test_the_clock_offset_moves_the_traffic_peak_to_the_start_of_the_run() -> None:
    """The whole point of `start_hour`: a failure has to land on busy traffic, and
    without this the only way to get there is to run twelve idle hours first."""
    midnight = UserArrivals(peak_rps=100.0, burstiness=0.0, clock_offset_s=0.0)
    afternoon = UserArrivals(peak_rps=100.0, burstiness=0.0, clock_offset_s=13 * 3600.0)

    assert midnight.rps(0.0) < 45.0
    assert afternoon.rps(0.0) > 95.0
    assert afternoon.rps(3600.0) == pytest.approx(100.0, rel=1e-6)


def test_the_offset_shifts_the_curve_rather_than_reshaping_it() -> None:
    plain = UserArrivals(peak_rps=100.0, burstiness=0.0, clock_offset_s=0.0)
    shifted = UserArrivals(peak_rps=100.0, burstiness=0.0, clock_offset_s=13 * 3600.0)
    for t in (0.0, 900.0, 3600.0, 7200.0):
        assert shifted.rps(t) == pytest.approx(plain.rps(t + 13 * 3600.0))

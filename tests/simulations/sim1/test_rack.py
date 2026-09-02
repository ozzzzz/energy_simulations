import pytest

from app.simulations.sim1.models import Profile, TickContext
from app.simulations.sim1.rack import Rack, RackState
from app.simulations.sim1.workload import WorkloadProfile


def _rack(**kwargs: object) -> Rack:
    return Rack(name="rack-1", workload=WorkloadProfile(Profile.TRAINING, noise_ratio=0.0), **kwargs)  # type: ignore[arg-type]


def _ctx(t: float, dt: float) -> TickContext:
    return TickContext(t=t, dt=dt, tick=int(t / dt))


def _ask(rack: Rack, ctx: TickContext) -> float:
    """Stand in for the scheduler: hand the rack its own profile's ask."""
    return rack.request(ctx, rack.profile_ask_kw(ctx))


def test_a_rack_draws_exactly_what_it_was_granted() -> None:
    """Granted <= requested <= cap holds by construction, so there is never a
    third reconciliation pass between the grant and the draw."""
    rack = _rack()
    ctx = _ctx(0.0, 60.0)
    requested = _ask(rack, ctx)
    result = rack.apply(ctx, granted_kw=requested * 0.4, sink_c=20.0)

    assert result.drawn_kw == pytest.approx(requested * 0.4)
    assert result.heat_kw == result.drawn_kw


@pytest.mark.parametrize("dt", [1.0, 60.0, 600.0])
def test_temperature_converges_monotonically_at_any_tick_size(dt: float) -> None:
    """Guards the analytic solve. Explicit Euler with the proportional cooling
    term needs dt < 2*mass/UA, which is ~350 s here — it would oscillate at 600."""
    rack = _rack()
    draw_kw = 150.0
    sink_c = 20.0
    steady_c = sink_c + draw_kw / rack.ua_kw_per_c

    # Run for ~20 time constants whatever the tick size, so "did it converge"
    # means the same thing at dt=1 and dt=600.
    steps = max(1, int(20.0 * rack.thermal_mass_kws_per_c / rack.ua_kw_per_c / dt))
    temps = [rack.temp_c]
    for i in range(steps):
        rack.state = RackState.RUNNING
        rack.apply(_ctx(i * dt, dt), granted_kw=draw_kw, sink_c=sink_c)
        temps.append(rack.temp_c)

    assert temps == sorted(temps), "temperature oscillated instead of converging"
    assert all(t <= steady_c + 1e-9 for t in temps), "temperature overshot its steady state"
    assert rack.temp_c == pytest.approx(steady_c, rel=1e-6)


def test_removed_heat_matches_the_energy_balance_exactly() -> None:
    rack = _rack()
    dt = 45.0
    draw_kw = 120.0
    start_c = rack.temp_c

    result = rack.apply(_ctx(0.0, dt), granted_kw=draw_kw, sink_c=18.0)
    expected_kws = draw_kw * dt - rack.thermal_mass_kws_per_c * (rack.temp_c - start_c)

    assert result.removed_kw * dt == pytest.approx(expected_kws, abs=1e-10)


def test_losing_coolant_flow_makes_the_rack_adiabatic() -> None:
    rack = _rack()
    result = rack.apply(_ctx(0.0, 60.0), granted_kw=150.0, sink_c=18.0, ua_scale=0.0)
    assert result.removed_kw == pytest.approx(0.0)
    assert rack.temp_c == pytest.approx(25.0 + 150.0 * 60.0 / rack.thermal_mass_kws_per_c)


def test_a_hot_sink_walks_the_rack_through_throttle_then_shutdown() -> None:
    rack = _rack()
    states: list[str] = []
    for i in range(300):
        ctx = _ctx(i * 10.0, 10.0)
        want = _ask(rack, ctx)
        rack.apply(ctx, granted_kw=want, sink_c=90.0, ua_scale=1.0)
        states.append(rack.state.value)

    assert "throttling" in states
    assert "emergency_shutdown" in states
    assert states.index("throttling") < states.index("emergency_shutdown")


def test_a_shutdown_rack_recovers_once_the_sink_cools() -> None:
    rack = _rack()
    for i in range(300):
        ctx = _ctx(i * 10.0, 10.0)
        rack.apply(ctx, granted_kw=_ask(rack, ctx), sink_c=90.0)
    assert rack.state is RackState.EMERGENCY_SHUTDOWN

    for i in range(300, 900):
        ctx = _ctx(i * 10.0, 10.0)
        rack.apply(ctx, granted_kw=_ask(rack, ctx), sink_c=18.0)

    assert rack.state in (RackState.RUNNING, RackState.IDLE)


def test_hysteresis_keeps_a_rack_at_the_threshold_from_flapping() -> None:
    """Without it a rack parked exactly at the throttle point changes state on
    every tick, which floods the event log and the visualization."""
    rack = _rack()
    sink_c = rack.throttle_temp_c - rack.peak_kw * rack.throttle_ratio / rack.ua_kw_per_c

    transitions = 0
    previous = rack.state
    for i in range(600):
        ctx = _ctx(i * 5.0, 5.0)
        rack.apply(ctx, granted_kw=_ask(rack, ctx), sink_c=sink_c)
        transitions += rack.state is not previous
        previous = rack.state

    assert transitions <= 4, f"rack flapped {transitions} times at the threshold"


def test_a_shutdown_rack_asks_for_nothing() -> None:
    rack = _rack()
    rack.state = RackState.EMERGENCY_SHUTDOWN
    ctx = _ctx(0.0, 60.0)
    assert rack.request(ctx, rack.profile_ask_kw(ctx)) == 0.0
    # ...but the ask is still recorded, so unserved compute is measured against
    # what was wanted, not what was allowed.
    assert rack.demand_kw > 0.0


def test_a_throttling_rack_asks_for_the_throttled_cap() -> None:
    rack = _rack()
    rack.state = RackState.THROTTLING
    assert rack.request(_ctx(0.0, 60.0), rack.peak_kw) == pytest.approx(rack.peak_kw * rack.throttle_ratio)


def test_power_and_throughput_are_inverses_of_each_other() -> None:
    rack = _rack()
    for tokens in (0.0, 5_000.0, 20_000.0, rack.peak_tokens_per_s):
        assert rack.tokens_per_s_for(rack.kw_for(tokens)) == pytest.approx(tokens, abs=1e-6)


def test_an_idling_rack_still_draws_power_but_serves_nothing() -> None:
    rack = _rack()
    assert rack.kw_for(0.0) == pytest.approx(rack.idle_kw)
    assert rack.tokens_per_s_for(rack.idle_kw) == 0.0
    assert rack.tokens_per_s_for(0.0) == 0.0


def test_serving_capacity_collapses_with_the_rack_state() -> None:
    """The link that turns a thermal event into a user-visible one."""
    rack = _rack()
    healthy = rack.capacity_tokens_per_s
    rack.state = RackState.THROTTLING
    throttled = rack.capacity_tokens_per_s
    rack.state = RackState.EMERGENCY_SHUTDOWN

    assert healthy == pytest.approx(rack.peak_tokens_per_s)
    assert 0.0 < throttled < healthy
    assert rack.capacity_tokens_per_s == 0.0

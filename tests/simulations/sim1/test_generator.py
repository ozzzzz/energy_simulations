import pytest

from app.simulations.sim1.electrical.generator import DieselGenerator, FuelTank, GeneratorState
from app.simulations.sim1.models import TickContext


def _run(gen: DieselGenerator, dt: float, until: float, ask_kw: float = 600.0) -> list[tuple[float, float]]:
    """Drive the generator over a wall of ticks; returns (t, output_kw) pairs."""
    trace: list[tuple[float, float]] = []
    t = 0.0
    index = 0
    while t < until:
        ctx = TickContext(t=t, dt=dt, tick=index)
        gen.command(start=True)
        gen.reset_asks()
        gen.register_ask(ask_kw)
        bus = gen.deliver(ctx)
        trace.append((t, bus.output_kw))
        t += dt
        index += 1
    return trace


@pytest.mark.parametrize("dt", [1.0, 5.0, 10.0])
def test_output_stays_at_zero_until_the_start_time_has_actually_elapsed(dt: float) -> None:
    gen = DieselGenerator(start_time_s=30.0, start_success_p=1.0, ramp_s=0.0)
    trace = _run(gen, dt=dt, until=120.0)

    live = [t for t, kw in trace if kw > 0.0]
    assert live, "generator never produced anything"
    first = live[0]
    # The first tick boundary at or after 30 s — never earlier, and never more
    # than one tick later.
    assert 30.0 <= first < 30.0 + dt
    assert all(kw == 0.0 for t, kw in trace if t < first)


def test_output_ramps_rather_than_stepping_to_full_load() -> None:
    gen = DieselGenerator(start_time_s=30.0, start_success_p=1.0, ramp_s=10.0)
    outputs = [kw for _, kw in _run(gen, dt=1.0, until=60.0) if kw > 0.0]
    assert outputs[0] < gen.rating_kw
    assert outputs == sorted(outputs), "output should never fall while the ask is constant"
    assert max(outputs) == pytest.approx(600.0)


def test_three_failed_attempts_latch_the_generator_as_failed() -> None:
    gen = DieselGenerator(start_time_s=30.0, start_success_p=0.0, max_start_attempts=3, retry_delay_s=10.0)
    _run(gen, dt=1.0, until=400.0)

    assert gen.state is GeneratorState.FAILED
    assert gen.start_failures == 3
    assert gen.starts == 0
    assert gen.tank.consumed_l == 0.0


def test_fuel_burn_is_affine_in_load() -> None:
    gen = DieselGenerator(start_time_s=0.0, start_success_p=1.0, ramp_s=0.0, idle_l_per_h=8.0, l_per_h_per_kw=0.24)
    ctx = TickContext(t=0.0, dt=3600.0, tick=0)
    gen.command(start=True)
    gen.reset_asks()
    gen.register_ask(400.0)
    gen.deliver(ctx)

    assert gen.fuel_rate_l_per_h == pytest.approx(8.0 + 0.24 * 400.0)
    assert gen.tank.consumed_l == pytest.approx(104.0)


def test_running_the_tank_dry_fails_the_generator() -> None:
    gen = DieselGenerator(start_time_s=0.0, start_success_p=1.0, ramp_s=0.0, tank=FuelTank(level_l=5.0))
    _run(gen, dt=60.0, until=1800.0)

    assert gen.state is GeneratorState.FAILED
    assert gen.tank.empty


def test_dropping_the_start_signal_cools_down_before_stopping() -> None:
    gen = DieselGenerator(start_time_s=0.0, start_success_p=1.0, ramp_s=0.0, cooldown_s=100.0)
    _run(gen, dt=10.0, until=60.0)
    assert gen.state is GeneratorState.RUNNING

    t = 60.0
    for index in range(30):
        gen.command(start=False)
        gen.reset_asks()
        gen.deliver(TickContext(t=t, dt=10.0, tick=index))
        t += 10.0

    assert gen.state is GeneratorState.STOPPED


def test_the_bus_splits_output_proportionally_and_independent_of_call_order() -> None:
    gen = DieselGenerator(start_time_s=0.0, start_success_p=1.0, ramp_s=0.0, rating_kw=600.0)
    ctx = TickContext(t=0.0, dt=1.0, tick=0)
    gen.command(start=True)
    gen.reset_asks()
    gen.register_ask(600.0)
    gen.register_ask(200.0)
    bus = gen.deliver(ctx)

    a = bus.allocate(600.0)
    b = bus.allocate(200.0)
    assert a + b == pytest.approx(600.0)
    assert a == pytest.approx(450.0)
    assert b == pytest.approx(150.0)
    # Output is known before either side allocates, which is what lets fuel burn
    # be computed inside `deliver`.
    assert bus.output_kw == pytest.approx(600.0)

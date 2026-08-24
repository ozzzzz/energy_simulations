"""A dual-corded GPU rack: the load, and the only body whose temperature throttles.

Two departures from sim0's rack, both needed by the closed cooling loop:

* Heat leaves through a *conductance* to a sink temperature, not through an
  externally supplied removal capacity. That is what lets a warming coolant
  loop throttle the rack, with no clip and no ``min()`` in the causal chain.
* The temperature is solved analytically (see :mod:`.thermal`) rather than
  Euler-stepped, so ``--dt 600`` is as stable as ``--dt 1``.
"""

from dataclasses import dataclass, field
from enum import StrEnum

from app.simulations.sim1.protocols import LoadResult, TickContext
from app.simulations.sim1.thermal import step_lumped
from app.simulations.sim1.units import kwh
from app.simulations.sim1.workload import WorkloadProfile


class RackState(StrEnum):
    IDLE = "idle"
    RUNNING = "running"
    THROTTLING = "throttling"
    EMERGENCY_SHUTDOWN = "emergency_shutdown"
    RECOVERING = "recovering"


@dataclass
class Rack:
    """Reference unit is a GB300 NVL72 class rack: ~135 kW nominal, 155 kW peak."""

    name: str
    workload: WorkloadProfile
    nominal_kw: float = 135.0
    peak_kw: float = 155.0
    ambient_c: float = 25.0
    throttle_temp_c: float = 85.0
    shutdown_temp_c: float = 95.0
    recovery_temp_c: float = 70.0
    hysteresis_c: float = 5.0
    thermal_mass_kws_per_c: float = 900.0
    ua_kw_per_c: float = 5.17
    """Rack-to-coolant conductance. Sized so a rack at peak draw sits ~30 °C
    above its coolant, i.e. ~50 °C on a healthy 20 °C loop."""

    throttle_ratio: float = 0.6
    liquid_capture_rate: float = 0.9
    """Fraction of rack heat taken by cold plates. The remainder goes to room
    air, which is why the sink temperature is a blend of the two."""

    psu_efficiency: float = 0.97

    kind: str = field(default="rack", init=False)
    temp_c: float = field(init=False)
    state: RackState = field(default=RackState.IDLE, init=False)
    energy_kwh: float = field(default=0.0, init=False)
    unserved_kwh: float = field(default=0.0, init=False)
    drawn_kw: float = field(default=0.0, init=False)
    demand_kw: float = field(default=0.0, init=False)
    removed_kw: float = field(default=0.0, init=False)

    def __post_init__(self) -> None:
        self.temp_c = self.ambient_c

    @property
    def down(self) -> bool:
        return self.state is RackState.EMERGENCY_SHUTDOWN

    @property
    def draw_cap_kw(self) -> float:
        if self.state is RackState.EMERGENCY_SHUTDOWN:
            return 0.0
        if self.state in (RackState.THROTTLING, RackState.RECOVERING):
            return self.peak_kw * self.throttle_ratio
        return self.peak_kw

    def request(self, ctx: TickContext) -> float:
        """Phase 2. Wanted power, already capped by the state at tick start.

        Capping here rather than during ``apply`` is what makes the grant the
        rack's actual draw: ``granted <= requested <= cap`` holds by
        construction, so no third reconciliation pass is needed.
        """
        self.demand_kw = self.workload.demand_kw(ctx.t)
        return min(self.demand_kw, self.draw_cap_kw)

    def apply(self, ctx: TickContext, granted_kw: float, sink_c: float, ua_scale: float = 1.0) -> LoadResult:
        drawn = max(0.0, granted_kw)
        self.drawn_kw = drawn
        self.energy_kwh += kwh(drawn, ctx.dt)
        self.unserved_kwh += kwh(max(0.0, self.demand_kw - drawn), ctx.dt)

        # Every watt a rack draws leaves it as heat; the PSU/VRM split below is
        # reporting only, not a second energy path.
        step = step_lumped(
            temp_c=self.temp_c,
            heat_in_kw=drawn,
            sink_c=sink_c,
            ua_kw_per_c=self.ua_kw_per_c * max(0.0, ua_scale),
            mass_kws_per_c=self.thermal_mass_kws_per_c,
            dt=ctx.dt,
        )
        self.temp_c = step.temp_c
        self.removed_kw = step.removed_kw_over(ctx.dt)
        self._transition(drawn)

        return LoadResult(
            drawn_kw=drawn,
            heat_kw=drawn,
            removed_kw=self.removed_kw,
            temp_c=self.temp_c,
            state=self.state.value,
        )

    @property
    def it_kw(self) -> float:
        """Draw that reaches the GPUs as compute rather than PSU/VRM loss."""
        return self.drawn_kw * self.psu_efficiency

    @property
    def loss_kw(self) -> float:
        return self.drawn_kw * (1.0 - self.psu_efficiency)

    @property
    def liquid_kw(self) -> float:
        return self.removed_kw * self.liquid_capture_rate

    @property
    def air_kw(self) -> float:
        return self.removed_kw * (1.0 - self.liquid_capture_rate)

    def _transition(self, drawn_kw: float) -> None:
        """Latch the new state from the end-of-tick temperature.

        Transitions take effect on the *next* tick's ``request``, so a shutdown
        overshoots its threshold by exactly one tick. That is a bounded
        discretisation artefact, and the engine's dt refinement shrinks it to a
        second wherever it matters.
        """
        running = RackState.RUNNING if drawn_kw > 0.0 else RackState.IDLE

        if self.temp_c >= self.shutdown_temp_c:
            self.state = RackState.EMERGENCY_SHUTDOWN
            return

        if self.state is RackState.EMERGENCY_SHUTDOWN:
            if self.temp_c <= self.recovery_temp_c:
                self.state = RackState.RECOVERING
            return

        if self.state is RackState.RECOVERING:
            if self.temp_c <= self.throttle_temp_c - self.hysteresis_c:
                self.state = running
            return

        if self.temp_c >= self.throttle_temp_c:
            self.state = RackState.THROTTLING
            return

        if self.state is RackState.THROTTLING:
            # Hysteresis, not a bare threshold: without it a rack sitting at the
            # throttle point flaps between states every tick.
            if self.temp_c <= self.throttle_temp_c - self.hysteresis_c:
                self.state = running
            return

        self.state = running

    def telemetry(self) -> dict[str, float | str]:
        return {
            "state": self.state.value,
            "demand_kw": self.demand_kw,
            "drawn_kw": self.drawn_kw,
            "it_kw": self.it_kw,
            "temp_c": self.temp_c,
            "removed_kw": self.removed_kw,
        }

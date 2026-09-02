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

from app.simulations.sim1.models import LoadResult, Segment, TickContext
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
    idle_fraction: float = 0.15
    """Draw with nothing to do. A rack serving zero requests is not a rack at
    0 kW — fans, NICs, idling SMs and the host all keep running, which is why
    an interactive rack's power floor is well above zero."""

    peak_tokens_per_s: float = 40_000.0
    """Throughput at ``peak_kw``. A stand-in for "how fast this rack serves the
    model it is hosting" rather than a benchmark claim — only its ratio to the
    arrival rate affects the simulation."""

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

    segment: Segment = field(init=False)
    temp_c: float = field(init=False)
    state: RackState = field(default=RackState.IDLE, init=False)
    energy_kwh: float = field(default=0.0, init=False)
    unserved_kwh: float = field(default=0.0, init=False)
    drawn_kw: float = field(default=0.0, init=False)
    demand_kw: float = field(default=0.0, init=False)
    removed_kw: float = field(default=0.0, init=False)

    def __post_init__(self) -> None:
        self.temp_c = self.ambient_c
        self.segment = self.workload.segment

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

    @property
    def idle_kw(self) -> float:
        return self.idle_fraction * self.nominal_kw

    def tokens_per_s_for(self, kw: float) -> float:
        """Throughput at a given draw.

        Linear above the idle floor: GPU power tracks utilisation closely enough
        that a straight line between (idle, 0) and (peak, peak_tokens) is honest,
        and being invertible is what lets the scheduler ask for power in terms of
        the work it needs done.
        """
        span_kw = self.peak_kw - self.idle_kw
        if span_kw <= 0.0:
            return 0.0
        return self.peak_tokens_per_s * max(0.0, kw - self.idle_kw) / span_kw

    def kw_for(self, tokens_per_s: float) -> float:
        """Draw needed to serve ``tokens_per_s``, bounded by idle and peak."""
        if self.peak_tokens_per_s <= 0.0:
            return self.idle_kw
        span_kw = self.peak_kw - self.idle_kw
        wanted = self.idle_kw + span_kw * max(0.0, tokens_per_s) / self.peak_tokens_per_s
        return min(wanted, self.peak_kw)

    @property
    def capacity_tokens_per_s(self) -> float:
        """Throughput this rack could serve right now, given its own state.

        Falls to a throttled fraction under heat and to zero when shut down,
        which is how a thermal event becomes a user-visible one.
        """
        return self.tokens_per_s_for(self.draw_cap_kw)

    def request(self, ctx: TickContext, wanted_kw: float) -> float:
        """Phase 2. Wanted power, already capped by the state at tick start.

        The ask comes from the scheduler rather than from the rack itself: an
        interactive rack's demand is set by arriving user traffic, a batch rack's
        by its own profile. See :mod:`app.simulations.sim1.demand`.

        Capping here rather than during ``apply`` is what makes the grant the
        rack's actual draw: ``granted <= requested <= cap`` holds by
        construction, so no third reconciliation pass is needed.
        """
        self.demand_kw = max(0.0, wanted_kw)
        return min(self.demand_kw, self.draw_cap_kw)

    def profile_ask_kw(self, ctx: TickContext) -> float:
        """What this rack's own profile wants — the batch scheduler's input."""
        return self.workload.demand_kw(ctx.t)

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
            "segment": self.segment.value,
            "tokens_per_s": self.tokens_per_s_for(self.drawn_kw),
            "demand_kw": self.demand_kw,
            "drawn_kw": self.drawn_kw,
            "it_kw": self.it_kw,
            "temp_c": self.temp_c,
            "removed_kw": self.removed_kw,
        }

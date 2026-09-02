"""Step-down transformer: the first place electricity becomes heat."""

from dataclasses import dataclass, field
from enum import StrEnum

from app.simulations.sim1.electrical.overload import OverloadMonitor, OverloadState
from app.simulations.sim1.models import Delivery, TickContext
from app.simulations.sim1.thermal import step_lumped


class TransformerState(StrEnum):
    NORMAL = "normal"
    OVERLOAD = "overload"
    OVERHEATED = "overheated"
    TRIPPED = "tripped"


@dataclass
class Transformer:
    """Losses are modelled the way transformer losses actually behave.

    A flat efficiency would make loss linear in load, so an overload would look
    thermally identical to normal running. Real losses are a constant core term
    plus a copper term that grows with the *square* of current, which is what
    makes sustained overload cook the windings — the failure mode this component
    exists to show.
    """

    name: str
    capacity_kw: float = 1000.0
    no_load_loss_kw: float = 1.5
    full_load_loss_kw: float = 8.0
    ambient_c: float = 25.0
    thermal_mass_kws_per_c: float = 2000.0
    ua_kw_per_c: float = 0.127
    overheat_c: float = 140.0
    trip_ratio: float = 1.5
    hold_limit_s: float = 1800.0
    """Transformers ride through moderate overload for a long time — far longer
    than a UPS. Long enough that the winding temperature, not the timer, is
    usually what fails first."""

    winding_c: float = field(init=False)
    state: TransformerState = field(default=TransformerState.NORMAL, init=False)
    monitor: OverloadMonitor = field(init=False)
    throughput_kw: float = field(default=0.0, init=False)

    def __post_init__(self) -> None:
        self.winding_c = self.ambient_c
        self.monitor = OverloadMonitor(
            rating_kw=self.capacity_kw,
            trip_ratio=self.trip_ratio,
            hold_limit_s=self.hold_limit_s,
        )

    @property
    def failed(self) -> bool:
        return self.state in (TransformerState.OVERHEATED, TransformerState.TRIPPED)

    def trip(self) -> None:
        """Externally forced failure — how the ``side_a_lost`` scenarios start."""
        self.state = TransformerState.TRIPPED

    def loss_at(self, throughput_kw: float) -> float:
        if throughput_kw <= 0.0:
            return 0.0
        load = throughput_kw / self.capacity_kw if self.capacity_kw > 0.0 else 0.0
        return self.no_load_loss_kw + self.full_load_loss_kw * load * load

    def probe(self, ctx: TickContext, upstream_kw: float) -> float:
        if self.failed:
            return 0.0
        throughput = min(upstream_kw, self.capacity_kw)
        return max(0.0, throughput - self.loss_at(throughput))

    def input_for(self, output_kw: float) -> float:
        """Input needed to deliver ``output_kw``, inverting the quadratic loss.

        ``output + loss_at(output)`` is not enough: loss grows with the square of
        throughput, so grossing up by the loss at the *output* under-delivers by
        the second-order term. Left uncorrected that leaves a fraction of a kW
        short every tick — which the UPS then covers from its battery, quietly
        reporting a healthy site as permanently on battery. Three fixed-point
        iteration converges in a handful of passes because the loss derivative
        here is around 1 %.
        """
        if output_kw <= 0.0:
            return 0.0
        x = output_kw + self.loss_at(output_kw)
        for _ in range(24):
            nxt = output_kw + self.loss_at(x)
            if abs(nxt - x) < 1e-12:
                return nxt
            x = nxt
        return x

    def request(self, ctx: TickContext, demand_kw: float) -> float:
        if self.failed:
            return 0.0
        return self.input_for(demand_kw)

    def deliver(self, ctx: TickContext, supply_kw: float, demand_kw: float) -> Delivery:
        if self.failed:
            self.throughput_kw = 0.0
            self._integrate_thermal(ctx, loss_kw=0.0)
            return Delivery()

        drawn = min(supply_kw, self.input_for(demand_kw), self.capacity_kw * self.trip_ratio)
        self.throughput_kw = drawn
        loss = min(self.loss_at(drawn), drawn)
        delivered = drawn - loss

        overload = self.monitor.update(drawn, ctx.dt)
        self._integrate_thermal(ctx, loss_kw=loss)

        if self.state is not TransformerState.OVERHEATED:
            if overload is OverloadState.TRIPPED:
                self.state = TransformerState.TRIPPED
            elif overload is OverloadState.OVERLOAD:
                self.state = TransformerState.OVERLOAD
            else:
                self.state = TransformerState.NORMAL

        return Delivery.passive(drawn_kw=drawn, delivered_kw=delivered)

    def idle(self, ctx: TickContext) -> None:
        """Let the windings cool while this side runs off the generator instead."""
        self.throughput_kw = 0.0
        self._integrate_thermal(ctx, loss_kw=0.0)

    def _integrate_thermal(self, ctx: TickContext, loss_kw: float) -> None:
        step = step_lumped(
            temp_c=self.winding_c,
            heat_in_kw=loss_kw,
            sink_c=self.ambient_c,
            ua_kw_per_c=self.ua_kw_per_c,
            mass_kws_per_c=self.thermal_mass_kws_per_c,
            dt=ctx.dt,
        )
        self.winding_c = step.temp_c
        if self.winding_c >= self.overheat_c:
            self.state = TransformerState.OVERHEATED

    def telemetry(self) -> dict[str, float | str]:
        return {
            "state": self.state.value,
            "winding_c": self.winding_c,
            "throughput_kw": self.throughput_kw,
            "load_pct": self.monitor.load_pct(self.throughput_kw),
        }

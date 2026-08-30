"""Power distribution unit — the last electrical stage before the load."""

from dataclasses import dataclass, field

from app.simulations.sim1.electrical.overload import OverloadMonitor
from app.simulations.sim1.models import Delivery, TickContext


@dataclass
class Pdu:
    """Busway plus branch breakers. No routing logic: in a 2N build the choice
    of which side carries what is made upstream, by capacity."""

    name: str
    rating_kw: float = 800.0
    efficiency: float = 0.995
    trip_ratio: float = 1.6
    hold_limit_s: float = 120.0

    monitor: OverloadMonitor = field(init=False)
    output_kw: float = field(default=0.0, init=False)

    def __post_init__(self) -> None:
        self.monitor = OverloadMonitor(
            rating_kw=self.rating_kw,
            trip_ratio=self.trip_ratio,
            hold_limit_s=self.hold_limit_s,
        )

    def probe(self, ctx: TickContext, upstream_kw: float) -> float:
        if self.monitor.tripped:
            return 0.0
        return min(upstream_kw, self.rating_kw) * self.efficiency

    def request(self, ctx: TickContext, demand_kw: float) -> float:
        return demand_kw / self.efficiency

    def deliver(self, ctx: TickContext, supply_kw: float, demand_kw: float) -> Delivery:
        if self.monitor.tripped:
            self.output_kw = 0.0
            return Delivery()

        drawn = min(supply_kw, demand_kw / self.efficiency, self.rating_kw * self.trip_ratio)
        delivered = drawn * self.efficiency
        self.output_kw = delivered
        self.monitor.update(delivered, ctx.dt)
        return Delivery.passive(drawn_kw=drawn, delivered_kw=delivered)

    def telemetry(self) -> dict[str, float | str]:
        return {
            "state": self.monitor.state.value,
            "output_kw": self.output_kw,
            "load_pct": self.monitor.load_pct(self.output_kw),
        }

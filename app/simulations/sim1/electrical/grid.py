"""The utility feed — the top of one side of the 2N chain."""

from dataclasses import dataclass, field
from enum import StrEnum

from app.simulations.sim1.protocols import Delivery, TickContext


class GridState(StrEnum):
    ONLINE = "online"
    BROWNOUT = "brownout"
    OFFLINE = "offline"


@dataclass
class GridFeed:
    """A utility connection with a capacity and three availability states.

    Loss-free by construction: the conversion loss of the incoming supply lives
    in the :class:`~app.simulations.sim1.electrical.transformer.Transformer`
    below it, not here. ``drawn_kw`` on the returned :class:`Delivery` is the
    site's *import* from the utility, which is what the cost model bills.
    """

    name: str
    capacity_kw: float = 1500.0
    nominal_voltage_v: float = 20_000.0
    brownout_factor: float = 0.65
    """Fraction of capacity still available while sagging."""

    kind: str = field(default="grid", init=False)
    state: GridState = field(default=GridState.ONLINE, init=False)
    imported_kwh: float = field(default=0.0, init=False)

    @property
    def available_kw(self) -> float:
        if self.state is GridState.OFFLINE:
            return 0.0
        if self.state is GridState.BROWNOUT:
            return self.capacity_kw * self.brownout_factor
        return self.capacity_kw

    @property
    def voltage_v(self) -> float:
        if self.state is GridState.OFFLINE:
            return 0.0
        if self.state is GridState.BROWNOUT:
            return self.nominal_voltage_v * 0.85
        return self.nominal_voltage_v

    def probe(self, ctx: TickContext, upstream_kw: float = 0.0) -> float:
        return self.available_kw

    def request(self, ctx: TickContext, demand_kw: float) -> float:
        return demand_kw

    def deliver(self, ctx: TickContext, supply_kw: float, demand_kw: float) -> Delivery:
        delivered = min(demand_kw, self.available_kw)
        self.imported_kwh += delivered * ctx.dt / 3600.0
        return Delivery(delivered_kw=delivered, drawn_kw=delivered)

    def telemetry(self) -> dict[str, float | str]:
        return {
            "state": self.state.value,
            "available_kw": self.available_kw,
            "voltage_v": self.voltage_v,
        }

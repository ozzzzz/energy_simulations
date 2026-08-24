"""Double-conversion UPS: the component that decides whether an outage is felt.

Everything here is expressed in *output* terms — the kW leaving the inverter —
because that is the only frame in which the mains path and the battery path can
be added together without repeatedly dividing by efficiencies.
"""

from dataclasses import dataclass, field
from enum import StrEnum

from app.simulations.sim1.electrical.battery import BatteryString
from app.simulations.sim1.electrical.overload import OverloadMonitor
from app.simulations.sim1.protocols import Delivery, TickContext

_MICRO_KW = 1e-6
"""One milliwatt. Below this, a power flow is rounding noise, not a flow."""


class UpsState(StrEnum):
    ONLINE = "online"
    ON_BATTERY = "on_battery"
    BYPASS = "bypass"
    OFFLINE = "offline"


@dataclass
class Ups:
    name: str
    battery: BatteryString
    rating_kw: float = 750.0
    rectifier_efficiency: float = 0.985
    inverter_efficiency: float = 0.98
    bypass_efficiency: float = 0.999
    overload_capability: float = 1.25
    """A UPS really can deliver past its nameplate for a short while — that is
    what an overload rating is. Capping output at 100 % instead would silently
    curtail the load and make the overload, and therefore the bypass transfer it
    causes, unreachable."""

    trip_ratio: float = 1.5
    hold_limit_s: float = 60.0

    kind: str = field(default="ups", init=False)
    state: UpsState = field(default=UpsState.ONLINE, init=False)
    monitor: OverloadMonitor = field(init=False)
    mains_kw: float = field(default=0.0, init=False)
    battery_out_kw: float = field(default=0.0, init=False)
    charge_kw: float = field(default=0.0, init=False)
    output_kw: float = field(default=0.0, init=False)
    loss_kwh: float = field(default=0.0, init=False)
    battery_s: float = field(default=0.0, init=False)

    def __post_init__(self) -> None:
        self.monitor = OverloadMonitor(
            rating_kw=self.rating_kw,
            trip_ratio=self.trip_ratio,
            hold_limit_s=self.hold_limit_s,
        )

    @property
    def double_conversion_efficiency(self) -> float:
        return self.rectifier_efficiency * self.inverter_efficiency

    @property
    def on_bypass(self) -> bool:
        return self.monitor.tripped

    @property
    def max_output_kw(self) -> float:
        return self.rating_kw * self.overload_capability

    def probe(self, ctx: TickContext, upstream_kw: float) -> float:
        if self.on_bypass:
            return min(upstream_kw, self.max_output_kw) * self.bypass_efficiency

        mains_out = min(upstream_kw * self.double_conversion_efficiency, self.max_output_kw)
        battery_out = min(self.battery.discharge_capability_kw(ctx.dt), self.max_output_kw) * self.inverter_efficiency
        # A double-conversion UPS genuinely supplements a sagging input from the
        # battery rather than choosing one source, hence the sum.
        return min(mains_out + battery_out, self.max_output_kw)

    def request(self, ctx: TickContext, demand_kw: float) -> float:
        efficiency = self.bypass_efficiency if self.on_bypass else self.double_conversion_efficiency
        return demand_kw / efficiency

    def charge_request(self, ctx: TickContext, critical_out_kw: float) -> float:
        """Recharge ask, in AC input kW. Bounded by the UPS's own headroom.

        Headroom-limiting matters: two 100 kW chargers coming online on top of a
        690 kW load would push a freshly started generator over its rating, and
        that failure would look exactly like a modelled one.
        """
        if self.on_bypass:
            return 0.0
        headroom_kw = max(0.0, self.rating_kw - critical_out_kw)
        return min(self.battery.charge_capability_kw(ctx.dt) / self.rectifier_efficiency, headroom_kw)

    def deliver(
        self,
        ctx: TickContext,
        supply_kw: float,
        demand_kw: float,
        charge_kw: float = 0.0,
    ) -> Delivery:
        if self.on_bypass:
            served = min(demand_kw, min(supply_kw, self.max_output_kw) * self.bypass_efficiency)
            mains_drawn = served / self.bypass_efficiency
            battery_out = 0.0
            charge_ac = 0.0
            stored = 0.0
        else:
            efficiency = self.double_conversion_efficiency
            served = min(demand_kw, min(supply_kw * efficiency, self.max_output_kw))
            mains_drawn = served / efficiency

            gap = min(demand_kw, self.max_output_kw) - served
            battery_out = 0.0
            # A milliwatt threshold, not `> 0`. Rounding residue from grossing
            # demand up and back down through three efficiencies would otherwise
            # pull microwatts off the battery on every fully supplied tick, and
            # the state machine would report a healthy site as on battery.
            if gap > _MICRO_KW:
                capability_dc = min(self.battery.discharge_capability_kw(ctx.dt), self.max_output_kw)
                wanted_dc = gap / self.inverter_efficiency
                battery_out = self.battery.discharge(ctx, min(wanted_dc, capability_dc)) * self.inverter_efficiency

            # Recharge is the lowest-priority tier: it only ever uses input the
            # critical load did not need.
            spare_ac = max(0.0, supply_kw - mains_drawn)
            offered_dc = min(charge_kw, spare_ac) * self.rectifier_efficiency
            stored = self.battery.charge(ctx, offered_dc)
            charge_ac = stored / self.rectifier_efficiency

        delivered = served + battery_out
        drawn = mains_drawn + charge_ac
        loss = (drawn + battery_out) - (delivered + stored)

        self.mains_kw = served
        self.battery_out_kw = battery_out
        self.charge_kw = charge_ac
        self.output_kw = delivered
        self.loss_kwh += loss * ctx.dt / 3600.0

        self.monitor.update(delivered, ctx.dt)
        if self.monitor.tripped:
            # A real UPS in overload transfers to bypass rather than dropping
            # the load: the site survives but silently loses battery protection,
            # so the *next* grid event is the one that kills it.
            self.state = UpsState.BYPASS
        elif battery_out > _MICRO_KW:
            self.state = UpsState.ON_BATTERY
            self.battery_s += ctx.dt
        elif supply_kw > 0.0:
            self.state = UpsState.ONLINE
        else:
            self.state = UpsState.OFFLINE

        return Delivery(
            delivered_kw=delivered,
            drawn_kw=drawn,
            loss_kw=loss,
            injected_kw=battery_out,
            stored_kw=stored,
        )

    def telemetry(self) -> dict[str, float | str]:
        return {
            "state": self.state.value,
            "output_kw": self.output_kw,
            "mains_kw": self.mains_kw,
            "battery_out_kw": self.battery_out_kw,
            "charge_kw": self.charge_kw,
            "load_pct": self.monitor.load_pct(self.output_kw),
            "rating_kw": self.rating_kw,
        }

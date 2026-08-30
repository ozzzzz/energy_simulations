"""The cooling plant: one electrical load, two heat sinks, one lagged input."""

from dataclasses import dataclass, field

from app.simulations.sim1.cooling.cdu import Cdu
from app.simulations.sim1.cooling.chiller import Chiller
from app.simulations.sim1.cooling.crah import Crah
from app.simulations.sim1.cooling.loop import CoolantLoop
from app.simulations.sim1.models import TickContext


@dataclass(frozen=True, slots=True)
class CoolingSupply:
    """What the plant can offer the racks this tick, given the power it got."""

    supply_c: float
    room_c: float
    ua_scale: float
    mech_kw: float
    pump_kw: float
    chiller_kw: float
    crah_kw: float
    liquid_capacity_kw: float
    air_capacity_kw: float

    def sink_c(self, liquid_capture_rate: float) -> float:
        """Blended sink temperature for a rack with the given capture split.

        A single blended sink keeps the rack's thermal solve in closed form while
        still letting both channels matter: a hot room drags the sink up even
        when the liquid loop is fine, and vice versa.
        """
        return liquid_capture_rate * self.supply_c + (1.0 - liquid_capture_rate) * self.room_c


@dataclass
class CoolingPlant:
    loop: CoolantLoop = field(default_factory=CoolantLoop)
    cdu: Cdu = field(default_factory=Cdu)
    chiller: Chiller = field(default_factory=Chiller)
    crah: Crah = field(default_factory=Crah)

    name: str = "cooling"
    request_kw: float = field(default=0.0, init=False)
    granted_kw: float = field(default=0.0, init=False)
    rejected_liquid_kw: float = field(default=0.0, init=False)
    rejected_air_kw: float = field(default=0.0, init=False)
    liquid_heat_kw: float = field(default=0.0, init=False)
    air_heat_kw: float = field(default=0.0, init=False)

    def request(self, ctx: TickContext) -> float:
        """Phase 2. Electrical demand, derived from *last* tick's loop state.

        This is the one lagged edge in the tick — see :mod:`.loop` for why the
        cut belongs here.
        """
        chiller_target = self.chiller.plan(ctx, loop_c=self.loop.supply_c, observed_heat_kw=self.liquid_heat_kw)
        crah_target = self.crah.plan(ctx, observed_heat_kw=self.air_heat_kw)
        self.request_kw = (
            self.cdu.electrical_demand_kw()
            + self.chiller.electrical_for(chiller_target)
            + self.crah.electrical_for(crah_target)
        )
        return self.request_kw

    def deliver(self, ctx: TickContext, electrical_kw: float) -> CoolingSupply:
        """Phase 4a. Spend the granted electricity, pumps first.

        Pumps outrank compressors: a chiller with no flow past it is useless,
        so starving the pumps to run the chiller would be the wrong trade.
        """
        remaining = max(0.0, electrical_kw)

        pump_kw = self.cdu.commit(ctx, min(self.cdu.electrical_demand_kw(), remaining))
        remaining -= pump_kw

        chiller_kw = min(self.chiller.electrical_for(self.chiller.demand_kw), remaining)
        remaining -= chiller_kw
        crah_kw = min(self.crah.electrical_for(self.crah.demand_kw), remaining)

        self.chiller.commit(ctx, chiller_kw, flow_factor=self.cdu.flow_factor)
        self.crah.commit(ctx, crah_kw)

        self.granted_kw = pump_kw + chiller_kw + crah_kw

        return CoolingSupply(
            supply_c=self.loop.supply_c,
            room_c=self.crah.room_c,
            ua_scale=self.cdu.flow_factor,
            mech_kw=self.granted_kw,
            pump_kw=pump_kw,
            chiller_kw=chiller_kw,
            crah_kw=crah_kw,
            liquid_capacity_kw=self.chiller.available_capacity_kw,
            air_capacity_kw=self.crah.available_capacity_kw,
        )

    def observe(self, ctx: TickContext, liquid_heat_kw: float, air_heat_kw: float) -> None:
        """Phase 4c. The lag closes: integrate both sinks with the heat that
        actually arrived, and remember it for next tick's demand."""
        self.liquid_heat_kw = liquid_heat_kw
        self.air_heat_kw = air_heat_kw
        self.rejected_liquid_kw = self.loop.integrate(ctx, liquid_heat_kw, self.chiller.removal_kw)
        self.rejected_air_kw = self.crah.integrate(ctx, air_heat_kw)

    @property
    def stored_kws(self) -> float:
        """Heat currently parked in the loop and the room air."""
        return self.loop.stored_kws + self.crah.mass_kws_per_c * (self.crah.room_c - self.crah.setpoint_c)

    def telemetry(self) -> dict[str, float | str]:
        rows: dict[str, float | str] = {
            "request_kw": self.request_kw,
            "granted_kw": self.granted_kw,
            "liquid_heat_kw": self.liquid_heat_kw,
            "air_heat_kw": self.air_heat_kw,
            "rejected_liquid_kw": self.rejected_liquid_kw,
            "rejected_air_kw": self.rejected_air_kw,
        }
        for prefix, component in (
            ("loop", self.loop),
            ("cdu", self.cdu),
            ("chiller", self.chiller),
            ("crah", self.crah),
        ):
            for key, value in component.telemetry().items():
                rows[f"{prefix}_{key}"] = value
        return rows

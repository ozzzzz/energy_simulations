"""The air channel: computer-room air handlers plus the room air they cool.

Only ~10 % of rack heat comes out this way in a direct-liquid-cooled hall, but
it is not decoration: room air has very little thermal mass, so when the CRAHs
fall behind, the air temperature moves within minutes and drags the racks' blended
sink temperature up with it.
"""

from dataclasses import dataclass, field
from enum import StrEnum

from app.simulations.sim1.protocols import TickContext
from app.simulations.sim1.units import clamp


class CrahState(StrEnum):
    RUNNING = "running"
    FAULTED = "faulted"


@dataclass
class Crah:
    name: str = "crah"
    capacity_kw: float = 100.0
    cop: float = 3.0
    """Worse than the chiller's, which is exactly why liquid cooling is worth
    the plumbing."""

    setpoint_c: float = 24.0
    gain_kw_per_c: float = 40.0
    room_volume_m3: float = 1000.0
    room_ua_kw_per_c: float = 0.5
    """Leakage to outdoors. Small, but it stops the room from integrating
    forever when the CRAHs are down."""

    outdoor_c: float = 30.0
    room_c: float = 24.0
    control_tau_s: float = 60.0

    kind: str = field(default="crah", init=False)
    state: CrahState = field(default=CrahState.RUNNING, init=False)
    demand_kw: float = field(default=0.0, init=False)
    removal_kw: float = field(default=0.0, init=False)
    electrical_kw: float = field(default=0.0, init=False)
    peak_room_c: float = field(init=False)

    def __post_init__(self) -> None:
        self.peak_room_c = self.room_c

    @property
    def faulted(self) -> bool:
        return self.state is CrahState.FAULTED

    @property
    def available_capacity_kw(self) -> float:
        return 0.0 if self.faulted else self.capacity_kw

    @property
    def mass_kws_per_c(self) -> float:
        # 1.2 kg/m3 * ~1.005 kJ/kg/K, plus a modest allowance for the fabric and
        # contents the air is in contact with.
        return self.room_volume_m3 * 1.206 * 3.0

    def fault(self) -> None:
        self.state = CrahState.FAULTED
        self.demand_kw = 0.0
        self.removal_kw = 0.0
        self.electrical_kw = 0.0

    def plan(self, ctx: TickContext, observed_heat_kw: float) -> float:
        if self.faulted:
            self.demand_kw = 0.0
            return 0.0
        target = clamp(
            observed_heat_kw + self.gain_kw_per_c * (self.room_c - self.setpoint_c),
            0.0,
            self.available_capacity_kw,
        )
        alpha = ctx.dt / (ctx.dt + self.control_tau_s) if self.control_tau_s > 0.0 else 1.0
        self.demand_kw += alpha * (target - self.demand_kw)
        return self.demand_kw

    def electrical_for(self, removal_kw: float) -> float:
        return removal_kw / self.cop if self.cop > 0.0 else 0.0

    def commit(self, ctx: TickContext, electrical_kw: float) -> float:
        self.electrical_kw = electrical_kw
        self.removal_kw = min(electrical_kw * self.cop, self.available_capacity_kw)
        return self.removal_kw

    def integrate(self, ctx: TickContext, heat_in_kw: float) -> float:
        """Advance room air temperature; returns heat rejected to outdoors."""
        leak_kw = self.room_ua_kw_per_c * (self.room_c - self.outdoor_c)
        net_kw = heat_in_kw - self.removal_kw - leak_kw
        self.room_c += net_kw * ctx.dt / self.mass_kws_per_c
        self.peak_room_c = max(self.peak_room_c, self.room_c)
        return self.removal_kw + leak_kw

    def telemetry(self) -> dict[str, float | str]:
        return {
            "state": self.state.value,
            "room_c": self.room_c,
            "removal_kw": self.removal_kw,
            "electrical_kw": self.electrical_kw,
            "capacity_kw": self.available_capacity_kw,
        }

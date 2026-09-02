"""The UPS battery string — the only thing that spans the generator's start.

Sizing note worth being explicit about, because it *is* the 2N story in one
number: each side's string holds roughly five minutes at the full *site* load.
In healthy 2N each side carries half the load, so the site gets ten minutes of
autonomy; lose a side and the survivor is back to the five minutes the spec
asks for.
"""

import math
from dataclasses import dataclass, field

from app.simulations.sim1.models import TickContext
from app.simulations.sim1.units import SECONDS_PER_HOUR, clamp, kwh


@dataclass
class BatteryString:
    name: str
    capacity_kwh: float = 60.0
    soc: float = 1.0
    cutoff_soc: float = 0.05
    """Below this the string is protected and delivers nothing. Real strings do
    not discharge to zero, and pretending otherwise inflates autonomy."""

    max_discharge_kw: float = 800.0
    max_charge_kw: float = 100.0
    discharge_efficiency: float = 0.96
    charge_efficiency: float = 0.96

    discharged_kwh: float = field(default=0.0, init=False)
    min_soc_seen: float = field(init=False)

    def __post_init__(self) -> None:
        self.min_soc_seen = self.soc

    @property
    def usable_kwh(self) -> float:
        return max(0.0, (self.soc - self.cutoff_soc) * self.capacity_kwh)

    @property
    def depleted(self) -> bool:
        return self.soc <= self.cutoff_soc + 1e-12

    def discharge_capability_kw(self, dt: float) -> float:
        """Power this string can put on the DC bus for a whole tick of ``dt``.

        Bounded by the C-rate *and* by the energy actually left — a string with
        30 seconds of charge cannot supply its rated power for a 60 second tick,
        and reporting that it can is how a simulation invents autonomy it does
        not have.
        """
        if self.depleted or dt <= 0.0:
            return 0.0
        energy_limited_kw = self.usable_kwh * SECONDS_PER_HOUR / dt * self.discharge_efficiency
        return min(self.max_discharge_kw, energy_limited_kw)

    def discharge(self, ctx: TickContext, bus_kw: float) -> float:
        """Put ``bus_kw`` on the DC bus, drawing more than that from the cells."""
        allowed = min(bus_kw, self.discharge_capability_kw(ctx.dt))
        if allowed <= 0.0:
            return 0.0
        cell_kw = allowed / self.discharge_efficiency
        drawn_kwh = min(kwh(cell_kw, ctx.dt), self.usable_kwh)
        self.soc = clamp(self.soc - drawn_kwh / self.capacity_kwh, 0.0, 1.0)
        self.discharged_kwh += drawn_kwh
        self.min_soc_seen = min(self.min_soc_seen, self.soc)
        return allowed

    def charge_capability_kw(self, dt: float) -> float:
        """Power the string can absorb from the bus for a whole tick."""
        if dt <= 0.0:
            return 0.0
        headroom_kwh = max(0.0, (1.0 - self.soc) * self.capacity_kwh)
        energy_limited_kw = headroom_kwh * SECONDS_PER_HOUR / dt / self.charge_efficiency
        return min(self.max_charge_kw, energy_limited_kw)

    def charge(self, ctx: TickContext, bus_kw: float) -> float:
        """Absorb up to ``bus_kw`` from the bus; returns what was actually taken."""
        allowed = min(bus_kw, self.charge_capability_kw(ctx.dt))
        if allowed <= 0.0:
            return 0.0
        stored_kwh = kwh(allowed, ctx.dt) * self.charge_efficiency
        self.soc = clamp(self.soc + stored_kwh / self.capacity_kwh, 0.0, 1.0)
        return allowed

    def autonomy_s(self, load_kw: float) -> float:
        """Seconds this string could carry ``load_kw``, reported every tick.

        Computed whether or not the string is discharging: the continuous
        hypothetical ("if the grid dropped now, you have 9.4 minutes") is far
        more useful than a number that only exists during an outage.
        """
        if self.usable_kwh <= 0.0:
            # A flat string has no runway, whatever the load. Reporting this as
            # undefined would render as "—" in the visualization at exactly the
            # moment the number matters most.
            return 0.0
        if load_kw <= 0.0:
            return math.inf
        return self.usable_kwh * self.discharge_efficiency * SECONDS_PER_HOUR / load_kw

    def telemetry(self) -> dict[str, float | str]:
        return {
            "soc": self.soc,
            "usable_kwh": self.usable_kwh,
        }

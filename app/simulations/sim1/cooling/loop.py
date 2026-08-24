"""The primary coolant loop — and the one place the tick's causality is cut.

Every other quantity in a sim1 tick is resolved simultaneously. The loop's
stored enthalpy is the single lagged term: the chiller sizes its demand from the
loop temperature as of the *end of the previous tick*.

That is the right place for the cut for three independent reasons. It is
physically true — compressor loading, valve strokes and the transport delay of
water around a loop are tens of seconds, so a 60 s lag on the chiller's response
to a heat step is more accurate than instantaneous response, not less. The
lagged quantity is a state variable with real inertia rather than a flow: 5000 L
of water is ~20,900 kW·s/°C and cannot move far within one tick. And it is what
makes the whole cooling chain physical instead of clipped — capacity exceeded
raises the supply temperature, which shrinks each rack's ΔT, which shrinks the
heat it can shed, which raises rack temperature, which throttles. No hard limit
anywhere in that chain.
"""

from dataclasses import dataclass, field

from app.simulations.sim1.protocols import TickContext
from app.simulations.sim1.units import WATER_KWS_PER_L_C


@dataclass
class CoolantLoop:
    name: str = "loop"
    volume_l: float = 5000.0
    """Sets the pace of every cooling failure. At 540 kW in and nothing out the
    loop climbs ~1.55 °C/min, so a chiller trip takes ~20 minutes to collapse
    the rack ΔT — slow enough to read on a chart. Halving the volume halves that
    and makes the scenario a cliff instead of a story."""

    supply_c: float = 20.0
    min_supply_c: float = 12.0
    """Chilled water cannot be driven arbitrarily cold; without this floor an
    over-sized chiller drags the loop towards absolute zero."""

    flow_kw_per_c: float = 60.0
    """Loop-wide heat capacity rate (mass flow x specific heat). Sets the
    supply-to-return spread at a given heat load."""

    return_c: float = field(init=False)
    peak_supply_c: float = field(init=False)

    def __post_init__(self) -> None:
        self.return_c = self.supply_c
        self.peak_supply_c = self.supply_c

    @property
    def mass_kws_per_c(self) -> float:
        return self.volume_l * WATER_KWS_PER_L_C

    def integrate(self, ctx: TickContext, heat_in_kw: float, heat_out_kw: float) -> float:
        """Advance the loop temperature; returns the heat actually rejected.

        Removal is capped so the loop cannot be pushed below ``min_supply_c``
        inside a tick — the same guard a real chiller's low-limit cutout gives.
        """
        headroom_kw = heat_in_kw + self.mass_kws_per_c * (self.supply_c - self.min_supply_c) / ctx.dt
        rejected_kw = min(heat_out_kw, max(0.0, headroom_kw))
        self.supply_c += (heat_in_kw - rejected_kw) * ctx.dt / self.mass_kws_per_c
        self.return_c = self.supply_c + (heat_in_kw / self.flow_kw_per_c if self.flow_kw_per_c > 0.0 else 0.0)
        self.peak_supply_c = max(self.peak_supply_c, self.supply_c)
        return rejected_kw

    @property
    def stored_kws(self) -> float:
        """Enthalpy above the loop's floor, for the thermal conservation check."""
        return self.mass_kws_per_c * (self.supply_c - self.min_supply_c)

    def telemetry(self) -> dict[str, float | str]:
        return {"supply_c": self.supply_c, "return_c": self.return_c}

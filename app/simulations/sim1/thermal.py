"""The one thermal integrator every sim1 body uses.

sim0 integrated temperature as ``dT = (heat_in - cooling_capacity) / mass * dt``:
heat removal was an independent input, so a rack could not shed heat *because*
it was hotter than its coolant. sim1's closed cooling loop needs exactly that
mechanism — it is what makes a rising loop temperature throttle the racks
instead of a hard capacity clip doing it.

Adding the proportional term makes explicit Euler only conditionally stable
(``dt < 2 * mass / UA``); at the reference numbers that limit is ~350 s for a
rack and someone will eventually pass ``--dt 600``. So this solves the ODE
instead of stepping it: exact for a piecewise-constant heat input and sink
temperature, unconditionally stable, and it yields the removed-heat integral in
closed form rather than by quadrature.
"""

import math
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ThermalStep:
    temp_c: float
    """Body temperature at the end of the tick."""

    removed_kws: float
    """Heat energy (kW*s) that left the body into the sink over the tick."""

    steady_state_c: float
    """Temperature this body would settle at if the inputs held."""

    def removed_kw_over(self, dt: float) -> float:
        """Average removal rate over a tick of length ``dt``."""
        return self.removed_kws / dt if dt > 0.0 else 0.0


def step_lumped(
    temp_c: float,
    heat_in_kw: float,
    sink_c: float,
    ua_kw_per_c: float,
    mass_kws_per_c: float,
    dt: float,
) -> ThermalStep:
    """Advance a lumped thermal mass coupled to a sink by conductance ``ua``.

    Solves ``m dT/dt = Q - UA (T - T_sink)`` exactly over the tick, then reads
    the removed heat straight off the energy balance
    ``Q*dt - m*dT``, which is why the conservation tests close to machine
    precision instead of to quadrature error.
    """
    if mass_kws_per_c <= 0.0:
        return ThermalStep(temp_c=sink_c, removed_kws=heat_in_kw * dt, steady_state_c=sink_c)

    if ua_kw_per_c <= 0.0:
        # Adiabatic body: nothing leaves, everything accumulates.
        end_c = temp_c + heat_in_kw * dt / mass_kws_per_c
        return ThermalStep(temp_c=end_c, removed_kws=0.0, steady_state_c=math.inf)

    steady_c = sink_c + heat_in_kw / ua_kw_per_c
    decay = math.exp(-dt * ua_kw_per_c / mass_kws_per_c)
    end_c = steady_c + (temp_c - steady_c) * decay
    removed_kws = heat_in_kw * dt - mass_kws_per_c * (end_c - temp_c)
    return ThermalStep(temp_c=end_c, removed_kws=removed_kws, steady_state_c=steady_c)

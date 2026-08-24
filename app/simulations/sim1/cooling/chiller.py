"""Chiller: rejects loop heat to outside, and draws real electricity to do it.

This is the component that closes the loop between cooling and power. Its draw
comes off the same PDUs as the racks, so facility load is IT plus cooling plus
conversion losses and PUE is computed rather than assumed — and a chiller trip
perturbs the electrical picture in both directions at once: its own ~80 kW of
draw disappears while its heat removal collapses.
"""

from dataclasses import dataclass, field
from enum import StrEnum

from app.simulations.sim1.protocols import TickContext
from app.simulations.sim1.units import clamp


class ChillerState(StrEnum):
    RUNNING = "running"
    FAULTED = "faulted"


@dataclass
class Chiller:
    name: str = "chiller"
    capacity_kw: float = 600.0
    cop: float = 6.0
    setpoint_c: float = 18.0
    gain_kw_per_c: float = 70.0
    """Proportional term of the capacity controller, in kW of extra removal per
    °C of loop overshoot. Roughly loop mass over a five-minute pull-down."""

    control_tau_s: float = 120.0
    """First-order lag on the demand — what a real capacity controller is, and
    what damps the two-tick limit cycle that the cooling/power feedback would
    otherwise produce when the site sits exactly at capacity."""

    kind: str = field(default="chiller", init=False)
    state: ChillerState = field(default=ChillerState.RUNNING, init=False)
    demand_kw: float = field(default=0.0, init=False)
    removal_kw: float = field(default=0.0, init=False)
    electrical_kw: float = field(default=0.0, init=False)

    @property
    def faulted(self) -> bool:
        return self.state is ChillerState.FAULTED

    @property
    def available_capacity_kw(self) -> float:
        return 0.0 if self.faulted else self.capacity_kw

    def fault(self) -> None:
        self.state = ChillerState.FAULTED
        self.demand_kw = 0.0
        self.removal_kw = 0.0
        self.electrical_kw = 0.0

    def restore(self) -> None:
        self.state = ChillerState.RUNNING

    def plan(self, ctx: TickContext, loop_c: float, observed_heat_kw: float) -> float:
        """Phase 2. Removal this chiller intends, from last tick's loop state."""
        if self.faulted:
            self.demand_kw = 0.0
            return 0.0

        target = clamp(
            observed_heat_kw + self.gain_kw_per_c * (loop_c - self.setpoint_c),
            0.0,
            self.available_capacity_kw,
        )
        alpha = ctx.dt / (ctx.dt + self.control_tau_s) if self.control_tau_s > 0.0 else 1.0
        self.demand_kw += alpha * (target - self.demand_kw)
        return self.demand_kw

    def electrical_for(self, removal_kw: float) -> float:
        return removal_kw / self.cop if self.cop > 0.0 else 0.0

    def commit(self, ctx: TickContext, electrical_kw: float, flow_factor: float) -> float:
        """Phase 4. Convert granted electricity into heat removal.

        ``flow_factor`` is the fraction of design pump power actually available:
        a chiller with no coolant moving past it removes nothing, however much
        electricity its compressors are given.
        """
        self.electrical_kw = electrical_kw
        removal = min(electrical_kw * self.cop, self.available_capacity_kw) * clamp(flow_factor, 0.0, 1.0)
        self.removal_kw = removal
        return removal

    def telemetry(self) -> dict[str, float | str]:
        return {
            "state": self.state.value,
            "removal_kw": self.removal_kw,
            "electrical_kw": self.electrical_kw,
            "capacity_kw": self.available_capacity_kw,
        }

"""Coolant distribution unit: the pumps between the racks and the primary loop.

Small in kW, decisive in effect. Heat transfer from cold plate to coolant scales
with flow, so losing pump power does not degrade cooling proportionally to the
power lost — it stops cooling almost entirely while the chiller carries on
compressing nothing.
"""

from dataclasses import dataclass, field
from enum import StrEnum

from app.simulations.sim1.models import TickContext


class CduState(StrEnum):
    RUNNING = "running"
    FAULTED = "faulted"


@dataclass
class Cdu:
    name: str = "cdu"
    pump_demand_kw: float = 20.0
    state: CduState = field(default=CduState.RUNNING, init=False)
    pump_kw: float = field(default=0.0, init=False)

    @property
    def faulted(self) -> bool:
        return self.state is CduState.FAULTED

    def fault(self) -> None:
        self.state = CduState.FAULTED
        self.pump_kw = 0.0

    @property
    def flow_factor(self) -> float:
        """Fraction of design flow, used to scale the rack-to-coolant conductance.

        Scaling the conductance rather than clipping a heat rate is what keeps
        the rack's temperature solve analytic: partial flow means a worse ΔT, not
        a capped kW.
        """
        if self.faulted or self.pump_demand_kw <= 0.0:
            return 0.0
        return min(1.0, self.pump_kw / self.pump_demand_kw)

    def electrical_demand_kw(self) -> float:
        return 0.0 if self.faulted else self.pump_demand_kw

    def commit(self, ctx: TickContext, electrical_kw: float) -> float:
        self.pump_kw = 0.0 if self.faulted else max(0.0, electrical_kw)
        return self.pump_kw

    def telemetry(self) -> dict[str, float | str]:
        return {"state": self.state.value, "pump_kw": self.pump_kw, "flow_factor": self.flow_factor}

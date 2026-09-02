"""Automatic transfer switch: picks the utility feed or the generator bus.

The interesting property is that transferring is not instantaneous. For
``transfer_time_s`` the switch feeds nothing at all — which is the concrete
reason the UPS downstream of it has to exist, and the reason a generator that
starts "in time" still cannot prevent a load interruption on its own.
"""

from dataclasses import dataclass, field
from enum import StrEnum

from app.simulations.sim1.models import TickContext


class AtsState(StrEnum):
    ON_PRIMARY = "on_primary"
    TRANSFERRING = "transferring"
    ON_GENERATOR = "on_generator"
    RETRANSFERRING = "retransferring"


class AtsSource(StrEnum):
    PRIMARY = "primary"
    GENERATOR = "generator"
    NONE = "none"


@dataclass
class AutomaticTransferSwitch:
    name: str
    transfer_time_s: float = 1.0
    retransfer_delay_s: float = 60.0
    """How long the utility must be back before returning to it. Without this
    delay a flickering grid makes the switch chatter, which is worse than the
    outage."""

    state: AtsState = field(default=AtsState.ON_PRIMARY, init=False)
    transfers: int = field(default=0, init=False)
    _timer_s: float = field(default=0.0, init=False, repr=False)
    _mains_ok_s: float = field(default=0.0, init=False, repr=False)

    @property
    def source(self) -> AtsSource:
        if self.state is AtsState.ON_PRIMARY:
            return AtsSource.PRIMARY
        if self.state is AtsState.ON_GENERATOR:
            return AtsSource.GENERATOR
        return AtsSource.NONE

    @property
    def on_generator(self) -> bool:
        return self.state is AtsState.ON_GENERATOR

    def probe(self, ctx: TickContext, primary_kw: float, generator_kw: float) -> float:
        """Capacity through the switch, using the state as it stands *now*.

        Deliberately reads the pre-transfer state: on the tick the utility dies
        the switch is still on a dead primary, so this returns zero and the UPS
        upstream-capacity calculation falls back to its battery. That is exactly
        the sequence a real site sees.

        Both sides are told the *whole* generator capacity here. Two sides each
        believing they could take all 800 kW over-counts the shared bus, but
        contention is resolved where it physically is — at the generator, whose
        output is capped at its rating and allocated proportionally to the asks.
        """
        if self.state is AtsState.ON_PRIMARY:
            return primary_kw
        if self.state is AtsState.ON_GENERATOR:
            return generator_kw
        return 0.0

    def request(self, ctx: TickContext, demand_kw: float) -> float:
        # A switch adds no loss and no parasitic; the ask passes straight through.
        return demand_kw

    def step(self, ctx: TickContext, primary_available: bool, generator_running: bool) -> AtsSource:
        """Advance the switch and report which source it is now feeding from."""
        if self.state is AtsState.ON_PRIMARY:
            if not primary_available and generator_running:
                self.state = AtsState.TRANSFERRING
                self._timer_s = self.transfer_time_s
        elif self.state is AtsState.TRANSFERRING:
            self._timer_s -= ctx.dt
            if self._timer_s <= 0.0:
                self.state = AtsState.ON_GENERATOR
                self.transfers += 1
                self._mains_ok_s = 0.0
        elif self.state is AtsState.ON_GENERATOR:
            if primary_available:
                self._mains_ok_s += ctx.dt
                # The anti-flap delay is skipped when the generator has quit:
                # waiting a minute on a dead source to avoid chatter is the
                # wrong trade.
                if self._mains_ok_s >= self.retransfer_delay_s or not generator_running:
                    self.state = AtsState.RETRANSFERRING
                    self._timer_s = self.transfer_time_s
            else:
                self._mains_ok_s = 0.0
        else:  # RETRANSFERRING
            self._timer_s -= ctx.dt
            if self._timer_s <= 0.0:
                self.state = AtsState.ON_PRIMARY
                self.transfers += 1

        return self.source

    def telemetry(self) -> dict[str, float | str]:
        return {"state": self.state.value, "source": self.source.value, "transfers": float(self.transfers)}

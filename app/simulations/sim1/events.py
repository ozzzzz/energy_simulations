"""Scripted exogenous failures, and the schedule that fires them.

Events are data, not code: a scenario is a list of them. That keeps scenarios
declarative, lets the engine derive its dt-refinement windows from the schedule
without executing anything, and means the visualization can label the timeline
from the same source the simulation ran from.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class ScheduledEvent:
    t: float
    target: str
    """``grid_a`` | ``grid_b`` | ``tx_a`` | ``tx_b`` | ``chiller`` | ``crah`` | ``cdu``"""

    action: str
    """``offline`` | ``online`` | ``brownout`` | ``trip`` | ``fault`` | ``restore``"""

    settle_s: float = 900.0
    """How long after this event the engine keeps a fine tick. Different failures
    play out over very different timescales — an outage resolves in minutes, a
    warming coolant loop takes the better part of an hour."""

    label: str = ""

    def described(self) -> str:
        return self.label or f"{self.target} {self.action}"


@dataclass
class EventSchedule:
    events: tuple[ScheduledEvent, ...] = ()
    _next: int = field(default=0, init=False, repr=False)

    def __post_init__(self) -> None:
        self.events = tuple(sorted(self.events, key=lambda e: e.t))

    def due(self, t: float) -> list[ScheduledEvent]:
        """Every event whose time has arrived, each returned exactly once."""
        fired: list[ScheduledEvent] = []
        while self._next < len(self.events) and self.events[self._next].t <= t:
            fired.append(self.events[self._next])
            self._next += 1
        return fired

    def boundaries(self) -> tuple[float, ...]:
        """Times the engine must land a tick on exactly."""
        return tuple(event.t for event in self.events)

    def refinement_windows(self, lead_s: float) -> tuple[tuple[float, float], ...]:
        return tuple((max(0.0, e.t - lead_s), e.t + e.settle_s) for e in self.events)

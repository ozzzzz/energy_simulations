"""Scripted exogenous failures, and the schedule that fires them.

Events are data, not code: a scenario is a list of them. That keeps scenarios
declarative, lets the engine derive its dt-refinement windows from the schedule
without executing anything, and means the visualization can label the timeline
from the same source the simulation ran from.

``ScheduledEvent`` itself is a shape and lives in :mod:`models`; the schedule
that fires them is behaviour and lives here.
"""

from dataclasses import dataclass, field

from app.simulations.sim1.models import ScheduledEvent


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

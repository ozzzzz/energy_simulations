"""Shared overload logic for transformer, UPS, PDU and generator.

Four private copies of "am I over my rating, and for how long" is how a
codebase rots, and the hold-timer semantics are the easiest thing to get subtly
different between copies.
"""

from dataclasses import dataclass, field
from enum import StrEnum


class OverloadState(StrEnum):
    NORMAL = "normal"
    OVERLOAD = "overload"
    TRIPPED = "tripped"


@dataclass
class OverloadMonitor:
    """Rating supervision with a hold timer and an instantaneous trip threshold.

    ``ratio <= 1``          -> NORMAL, and the accumulated hold time decays.
    ``1 < ratio <= trip``   -> OVERLOAD; ``hold_s`` accumulates and exceeding
                               ``hold_limit_s`` trips.
    ``ratio > trip``        -> trips immediately.

    A trip is latched: real breakers and UPS bypass contactors do not re-close
    on their own, and a self-healing failure teaches nothing.
    """

    rating_kw: float
    trip_ratio: float = 1.5
    hold_limit_s: float = 60.0
    decay_ratio: float = 0.5
    """Fraction of ``dt`` subtracted from ``hold_s`` per second spent below rating."""

    state: OverloadState = field(init=False, default=OverloadState.NORMAL)
    hold_s: float = field(init=False, default=0.0)
    peak_ratio: float = field(init=False, default=0.0)
    overload_s: float = field(init=False, default=0.0)

    def update(self, throughput_kw: float, dt: float) -> OverloadState:
        ratio = throughput_kw / self.rating_kw if self.rating_kw > 0.0 else 0.0
        self.peak_ratio = max(self.peak_ratio, ratio)

        if self.state is OverloadState.TRIPPED:
            return self.state

        if ratio > self.trip_ratio:
            self.state = OverloadState.TRIPPED
            self.hold_s = self.hold_limit_s
            self.overload_s += dt
            return self.state

        if ratio > 1.0:
            self.hold_s += dt
            self.overload_s += dt
            self.state = OverloadState.TRIPPED if self.hold_s >= self.hold_limit_s else OverloadState.OVERLOAD
            return self.state

        self.hold_s = max(0.0, self.hold_s - self.decay_ratio * dt)
        self.state = OverloadState.NORMAL
        return self.state

    @property
    def tripped(self) -> bool:
        return self.state is OverloadState.TRIPPED

    def load_pct(self, throughput_kw: float) -> float:
        return 100.0 * throughput_kw / self.rating_kw if self.rating_kw > 0.0 else 0.0

"""GPU workload profiles — the only exogenous driver in a healthy run.

Re-derived rather than shared with sim0. The differences that matter: demand is
expressed as a fraction of the rack's own nominal and peak rather than in
absolute kW, and a profile can carry scripted spike windows, so the load-spike
scenario needs no multiplier bolted on outside the model.
"""

import math
import random
from dataclasses import dataclass, field
from enum import StrEnum

from app.simulations.sim1.units import DAY_SECONDS, clamp


class Profile(StrEnum):
    IDLE = "idle"
    INFERENCE = "inference"
    TRAINING = "training"
    BURST = "burst"


@dataclass(frozen=True, slots=True)
class SpikeWindow:
    """A scripted demand override, as a fraction of the rack's peak."""

    start_s: float
    end_s: float
    peak_fraction: float = 1.0

    def contains(self, t: float) -> bool:
        return self.start_s <= t < self.end_s


def _diurnal_factor(t: float, peak_hour: float = 14.0, low_fraction: float = 0.45) -> float:
    """Smooth daily demand cycle: 1.0 at ``peak_hour``, ``low_fraction`` 12 h later."""
    phase = 2.0 * math.pi * (t / DAY_SECONDS - peak_hour / 24.0)
    mid = (1.0 + low_fraction) / 2.0
    amplitude = (1.0 - low_fraction) / 2.0
    return mid + amplitude * math.cos(phase)


def _pulse(t: float, period: float, width: float, low: float, high: float) -> float:
    return high if (t % period) < width else low


def _dip(t: float, period: float, width: float, high: float, low: float) -> float:
    return low if (t % period) < width else high


@dataclass
class WorkloadProfile:
    """Per-rack demand with an Ornstein-Uhlenbeck jitter on top of a base shape.

    Stateful: the jitter is correlated in time, so ``demand_kw`` must be called
    once per rack per tick, in order. White noise would be wrong here — real
    load wanders over minutes, and uncorrelated noise averages away exactly the
    excursions that stress a power chain.
    """

    profile: Profile | str
    nominal_kw: float = 135.0
    peak_kw: float = 155.0
    noise_ratio: float = 0.05
    noise_tau_s: float = 600.0
    seed: int = 0
    spikes: tuple[SpikeWindow, ...] = ()

    _rng: random.Random = field(init=False, repr=False)
    _noise: float = field(default=0.0, init=False, repr=False)
    _last_t: float | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self.profile = Profile(self.profile)
        self._rng = random.Random(self.seed)

    def base_kw(self, t: float) -> float:
        for spike in self.spikes:
            if spike.contains(t):
                return self.peak_kw * spike.peak_fraction

        if self.profile is Profile.IDLE:
            return 0.15 * self.nominal_kw
        if self.profile is Profile.INFERENCE:
            shape = _pulse(t, period=1800.0, width=300.0, low=0.62, high=1.02)
            return self.nominal_kw * shape * _diurnal_factor(t)
        if self.profile is Profile.TRAINING:
            # Steady near-nominal with periodic checkpoint dips.
            return self.nominal_kw * _dip(t, period=3600.0, width=180.0, high=0.99, low=0.72)
        # BURST: a training baseline punctuated by short all-out bursts.
        burst = _pulse(t, period=900.0, width=120.0, low=0.0, high=1.0)
        base = self.nominal_kw * 0.9
        return base + burst * (self.peak_kw - base)

    def demand_kw(self, t: float) -> float:
        base = self.base_kw(t)
        dt = 0.0 if self._last_t is None else max(0.0, t - self._last_t)
        self._last_t = t

        if self.noise_ratio > 0.0 and self.noise_tau_s > 0.0:
            decay = math.exp(-dt / self.noise_tau_s) if dt > 0.0 else 1.0
            self._noise = decay * self._noise + math.sqrt(max(0.0, 1.0 - decay * decay)) * self._rng.gauss(0.0, 1.0)

        return clamp(base * (1.0 + self.noise_ratio * self._noise), 0.0, self.peak_kw)

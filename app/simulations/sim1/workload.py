"""Rack-level workload shapes, and the noise source the demand layer reuses.

Re-derived rather than shared with sim0. The differences that matter: demand is
expressed as a fraction of the rack's own nominal and peak rather than in
absolute kW, a profile can carry scripted spike windows, and each profile
declares whether it is user-facing or batch.

Batch racks are driven from here. Interactive racks are driven by
:mod:`app.simulations.sim1.demand` instead, from actual user traffic — this
module only supplies their idle floor and their peak ceiling.
"""

import math
import random
from dataclasses import dataclass, field

from app.simulations.sim1.models import PROFILE_SEGMENTS, Profile, Segment, SpikeWindow
from app.simulations.sim1.units import DAY_SECONDS, clamp


@dataclass
class CorrelatedNoise:
    """Multiplicative AR(1) jitter, shared by rack profiles and user arrivals.

    White noise would be wrong for either: real load wanders over minutes, and
    uncorrelated noise averages away exactly the excursions that stress a power
    chain or overflow a queue.
    """

    ratio: float = 0.05
    tau_s: float = 600.0
    seed: int = 0

    _rng: random.Random = field(init=False, repr=False)
    _value: float = field(default=0.0, init=False, repr=False)
    _last_t: float | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self._rng = random.Random(self.seed)

    def factor(self, t: float) -> float:
        """Advance the process to ``t`` and return the multiplier ``1 + ratio*z``.

        Stateful: call once per consumer per tick, in order.
        """
        if self.ratio <= 0.0 or self.tau_s <= 0.0:
            return 1.0
        dt = 0.0 if self._last_t is None else max(0.0, t - self._last_t)
        self._last_t = t
        decay = math.exp(-dt / self.tau_s) if dt > 0.0 else 1.0
        self._value = decay * self._value + math.sqrt(max(0.0, 1.0 - decay * decay)) * self._rng.gauss(0.0, 1.0)
        return 1.0 + self.ratio * self._value


def diurnal_factor(t: float, peak_hour: float = 14.0, low_fraction: float = 0.45, clock_offset_s: float = 0.0) -> float:
    """Smooth daily demand cycle: 1.0 at ``peak_hour``, ``low_fraction`` 12 h later.

    ``clock_offset_s`` is the wall-clock time that simulated ``t = 0`` corresponds
    to. It exists so a scenario can *start* near the daily peak: a failure has to
    land on busy traffic to mean anything, and without this the only way to get
    that is to run twelve idle hours first.
    """
    phase = 2.0 * math.pi * ((t + clock_offset_s) / DAY_SECONDS - peak_hour / 24.0)
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
    clock_offset_s: float = 0.0
    """Wall-clock time that ``t = 0`` corresponds to. See :func:`diurnal_factor`."""

    _noise: CorrelatedNoise = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.profile = Profile(self.profile)
        self._noise = CorrelatedNoise(ratio=self.noise_ratio, tau_s=self.noise_tau_s, seed=self.seed)

    @property
    def segment(self) -> Segment:
        return PROFILE_SEGMENTS[Profile(self.profile)]

    def base_kw(self, t: float) -> float:
        for spike in self.spikes:
            if spike.contains(t):
                return self.peak_kw * spike.peak_fraction

        if self.profile is Profile.IDLE:
            return 0.15 * self.nominal_kw
        if self.profile is Profile.INFERENCE:
            shape = _pulse(t, period=1800.0, width=300.0, low=0.62, high=1.02)
            return self.nominal_kw * shape * diurnal_factor(t, clock_offset_s=self.clock_offset_s)
        if self.profile is Profile.TRAINING:
            # Steady near-nominal with periodic checkpoint dips.
            return self.nominal_kw * _dip(t, period=3600.0, width=180.0, high=0.99, low=0.72)
        # BURST: a training baseline punctuated by short all-out bursts.
        burst = _pulse(t, period=900.0, width=120.0, low=0.0, high=1.0)
        base = self.nominal_kw * 0.9
        return base + burst * (self.peak_kw - base)

    def demand_kw(self, t: float) -> float:
        return clamp(self.base_kw(t) * self._noise.factor(t), 0.0, self.peak_kw)

import math
import random
from dataclasses import dataclass, field

DAY_SECONDS = 24 * 3600.0


def _pulse(t: float, period: float, width: float, low: float, high: float) -> float:
    return high if (t % period) < width else low


def _dip(t: float, period: float, width: float, high: float, low: float) -> float:
    return low if (t % period) < width else high


def _diurnal_factor(t: float, peak_hour: float = 14.0, low_frac: float = 0.45) -> float:
    """1.0 at peak_hour (users active), low_frac at the trough 12h later (users asleep)."""
    phase = (t % DAY_SECONDS) / DAY_SECONDS
    peak_phase = peak_hour / 24.0
    cos_term = math.cos(2 * math.pi * (phase - peak_phase))
    return low_frac + (1.0 - low_frac) * 0.5 * (1.0 + cos_term)


@dataclass
class WorkloadInput:
    """Per-rack power demand as a function of time. Base shape per profile is
    deterministic; a seeded gaussian jitter is layered on top so runs are
    reproducible (same seed -> same trace) but not a flat line.

    The jitter is an AR(1)/Ornstein-Uhlenbeck process, not independent noise
    per tick: consecutive samples are correlated with a `noise_tau_s`-second
    memory, so the trace wanders smoothly instead of looking like static once
    plotted over thousands of ticks."""

    profile: str
    noise_ratio: float = 0.05
    noise_tau_s: float = 600.0
    seed: int = 0
    _rng: random.Random = field(init=False, repr=False)
    _prev_noise: float = field(init=False, default=0.0, repr=False)
    _last_t: float | None = field(init=False, default=None, repr=False)

    def __post_init__(self) -> None:
        self._rng = random.Random(self.seed)

    def demand_kw(self, t: float) -> float:
        base_kw = self._base_kw(t)

        elapsed_s = 0.0 if self._last_t is None else max(0.0, t - self._last_t)
        decay = math.exp(-elapsed_s / self.noise_tau_s) if self.noise_tau_s > 0 else 0.0
        innovation = self._rng.gauss(0.0, 1.0)
        self._prev_noise = decay * self._prev_noise + math.sqrt(max(0.0, 1.0 - decay**2)) * innovation
        self._last_t = t

        noise_kw = self._prev_noise * base_kw * self.noise_ratio
        return max(0.0, base_kw + noise_kw)

    def _base_kw(self, t: float) -> float:
        if self.profile == "idle":
            return 20.0
        if self.profile == "inference":
            # user-driven traffic: follows the day/night cycle
            return _pulse(t, period=30.0, width=5.0, low=90.0, high=140.0) * _diurnal_factor(t)
        if self.profile == "training":
            # scheduled batch jobs: sustained near-peak regardless of time of day
            return _dip(t, period=300.0, width=10.0, high=150.0, low=100.0)
        if self.profile == "mixed":
            if t < 60.0:
                return 20.0
            if t < 300.0:
                return _dip(t - 60.0, period=300.0, width=10.0, high=150.0, low=100.0)
            return _pulse(t - 300.0, period=30.0, width=5.0, low=90.0, high=140.0) * _diurnal_factor(t)
        raise ValueError(f"unknown workload profile: {self.profile!r}")


@dataclass
class PowerInput:
    """Collapses Grid+Transformer+UPS+Battery+Generator+PDU into one available-power signal.

    step()'s dt/requested_kw are accepted but unused here: sim_0's capacity is a
    scripted/constant function of time only. A real UPS/Battery/Generator chain in
    sim_1 is a closed loop instead (battery charge depletes proportional to actual
    load drawn, generator startup is a timed process) and needs both dt (to integrate
    its own state) and requested_kw (to know how hard it's being asked to work) — this
    signature lets that swap happen without touching the engine.py call site."""

    capacity_kw: float = 750.0

    def step(self, t: float, dt: float, requested_kw: float) -> float:
        return self.capacity_kw


@dataclass
class CoolingInput:
    """Collapses Cooling Plant+Loop into available heat-removal signals, split by medium.
    GB300 NVL72 racks capture roughly 90% of heat via liquid (direct-to-chip cold plates)
    and 10% via air (OSFP/storage/PDB) — the 700 kW total and 90/10 split are sized from
    that published ratio, not from sim_1_plan.md's original 600/100 approximation.

    Capacity is constant except during an optional scripted incident window
    [failure_start_s, failure_end_s), where it drops to (1 - failure_severity) of
    normal — e.g. a CDU/chiller fault. Still just a scripted function of time
    (no failure-probability model, no repair process) — a real Cooling Plant
    object with its own failure/recovery physics is a sim_1 concern.

    step()/liquid_step()/air_step()'s dt/requested_kw are accepted but unused, same
    reasoning as PowerInput.step(). requested_kw is conventionally the *previous*
    tick's actual heat load (see engine.py) since this tick's load isn't known until
    after Rack.step runs — a real Cooling Plant reacting to load can use that lag."""

    liquid_capacity_kw: float = 630.0
    air_capacity_kw: float = 70.0
    failure_start_s: float | None = None
    failure_end_s: float | None = None
    failure_severity: float = 0.0  # fraction of capacity lost during the incident window

    def _capacity_factor(self, t: float) -> float:
        if self.failure_start_s is None:
            return 1.0
        end_s = self.failure_end_s if self.failure_end_s is not None else float("inf")
        return (1.0 - self.failure_severity) if self.failure_start_s <= t < end_s else 1.0

    def step(self, t: float, dt: float, requested_kw: float) -> float:
        return self.liquid_step(t, dt, requested_kw) + self.air_step(t, dt, requested_kw)

    def liquid_step(self, t: float, dt: float, requested_kw: float) -> float:
        return self.liquid_capacity_kw * self._capacity_factor(t)

    def air_step(self, t: float, dt: float, requested_kw: float) -> float:
        return self.air_capacity_kw * self._capacity_factor(t)

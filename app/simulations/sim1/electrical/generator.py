"""Diesel standby generation, plus the bus that shares it between both sides.

There is one generator, not two. That is deliberate: it is the single point of
failure in an otherwise 2N site, and the ``grid_outage_gen_fail`` scenario
exists to make the consequence concrete.
"""

import random
from dataclasses import dataclass, field
from enum import StrEnum

from app.simulations.sim1.electrical.overload import OverloadMonitor
from app.simulations.sim1.protocols import TickContext
from app.simulations.sim1.units import SECONDS_PER_HOUR


class GeneratorState(StrEnum):
    STOPPED = "stopped"
    CRANKING = "cranking"
    RETRY_WAIT = "retry_wait"
    RUNNING = "running"
    COOLDOWN = "cooldown"
    FAILED = "failed"


@dataclass
class FuelTank:
    capacity_l: float = 4000.0
    level_l: float = 4000.0
    consumed_l: float = field(default=0.0, init=False)

    @property
    def empty(self) -> bool:
        return self.level_l <= 1e-9

    def draw(self, litres: float) -> float:
        taken = min(litres, self.level_l)
        self.level_l -= taken
        self.consumed_l += taken
        return taken


@dataclass
class GenBus:
    """One tick's worth of generator output, shared between the two sides.

    Allocation is proportional to each side's ask rather than first-come, so the
    result does not depend on which side's ``deliver`` runs first. Because
    ``sum(min(ask_i, available * share_i)) == min(total_ask, available)``, the
    generator knows its own total output — and therefore its fuel burn — before
    either side calls :meth:`allocate`.
    """

    running: bool
    available_kw: float
    total_ask_kw: float
    output_kw: float

    def allocate(self, ask_kw: float) -> float:
        if self.total_ask_kw <= 0.0 or ask_kw <= 0.0:
            return 0.0
        share = ask_kw / self.total_ask_kw
        return min(ask_kw, self.available_kw * share)


@dataclass
class DieselGenerator:
    name: str = "genset"
    rating_kw: float = 800.0
    start_time_s: float = 30.0
    start_success_p: float = 0.98
    max_start_attempts: int = 3
    retry_delay_s: float = 10.0
    ramp_s: float = 10.0
    cooldown_s: float = 300.0
    idle_l_per_h: float = 8.0
    l_per_h_per_kw: float = 0.24
    """Fuel burn is affine in load, not proportional: a lightly loaded genset is
    inefficient, which is why sites avoid running them near idle."""

    seed: int = 0
    tank: FuelTank = field(default_factory=FuelTank)

    kind: str = field(default="generator", init=False)
    state: GeneratorState = field(default=GeneratorState.STOPPED, init=False)
    starts: int = field(default=0, init=False)
    start_failures: int = field(default=0, init=False)
    run_s: float = field(default=0.0, init=False)
    output_kw: float = field(default=0.0, init=False)
    fuel_rate_l_per_h: float = field(default=0.0, init=False)
    monitor: OverloadMonitor = field(init=False)

    _rng: random.Random = field(init=False, repr=False)
    _start_requested: bool = field(default=False, init=False, repr=False)
    _ask_kw: float = field(default=0.0, init=False, repr=False)
    _crank_s: float = field(default=0.0, init=False, repr=False)
    _retry_s: float = field(default=0.0, init=False, repr=False)
    _running_s: float = field(default=0.0, init=False, repr=False)
    _cooldown_s: float = field(default=0.0, init=False, repr=False)
    _attempts: int = field(default=0, init=False, repr=False)

    def __post_init__(self) -> None:
        self._rng = random.Random(self.seed)
        self.monitor = OverloadMonitor(rating_kw=self.rating_kw, trip_ratio=1.3, hold_limit_s=120.0)

    @property
    def running(self) -> bool:
        return self.state is GeneratorState.RUNNING

    @property
    def run_hours(self) -> float:
        return self.run_s / SECONDS_PER_HOUR

    @property
    def ramp_factor(self) -> float:
        if not self.running or self.ramp_s <= 0.0:
            return 1.0 if self.running else 0.0
        return min(1.0, self._running_s / self.ramp_s)

    def command(self, start: bool) -> None:
        """Phase 1.5. Raise or drop the start signal.

        Separate from ``deliver`` because the only honest moment to notice "both
        mains are gone" is after capacity has been probed, and the genset has to
        be told before it is asked to produce anything.
        """
        self._start_requested = start
        if start and self.state is GeneratorState.STOPPED:
            self.state = GeneratorState.CRANKING
            self._crank_s = 0.0
            self._attempts = 0
        elif start and self.state is GeneratorState.COOLDOWN:
            # Still spinning — a re-request just puts it back on load.
            self.state = GeneratorState.RUNNING

    def reset_asks(self) -> None:
        self._ask_kw = 0.0

    def register_ask(self, kw: float) -> None:
        """Phase 2. A side's ATS parks its upstream ask on the shared bus."""
        self._ask_kw += max(0.0, kw)

    def probe(self, ctx: TickContext, upstream_kw: float = 0.0) -> float:
        """Capacity available *now*, before this tick's state advance.

        While cranking this is zero, so the sides correctly fall back to their
        batteries on the tick the utility dies.
        """
        if not self.running:
            return 0.0
        return self.rating_kw * self.ramp_factor

    def request(self, ctx: TickContext, demand_kw: float = 0.0) -> float:
        return self._ask_kw

    def deliver(self, ctx: TickContext, supply_kw: float = 0.0, demand_kw: float = 0.0) -> GenBus:
        self._advance(ctx)

        available = self.rating_kw * self.ramp_factor if self.running else 0.0
        output = min(self._ask_kw, available)
        self.output_kw = output
        self.monitor.update(output, ctx.dt)

        # Cooling down still means turning, so it still burns fuel at idle.
        if self.state in (GeneratorState.RUNNING, GeneratorState.COOLDOWN):
            self.run_s += ctx.dt
            self.fuel_rate_l_per_h = self.idle_l_per_h + self.l_per_h_per_kw * output
            wanted_l = self.fuel_rate_l_per_h * ctx.dt / SECONDS_PER_HOUR
            drawn_l = self.tank.draw(wanted_l)
            if drawn_l < wanted_l - 1e-12 or self.tank.empty:
                self.state = GeneratorState.FAILED
                self.output_kw = output = 0.0
        else:
            self.fuel_rate_l_per_h = 0.0

        return GenBus(
            running=self.running,
            available_kw=available,
            total_ask_kw=self._ask_kw,
            output_kw=output,
        )

    def _advance(self, ctx: TickContext) -> None:
        if self.state is GeneratorState.FAILED:
            return

        if self.state is GeneratorState.CRANKING:
            # Threshold checked against the timer as it stood at the start of
            # this tick, so a 30 s start resolves on the first tick boundary at
            # or after t+30 rather than one tick early.
            if self._crank_s >= self.start_time_s:
                self._attempts += 1
                if self._rng.random() < self.start_success_p:
                    self.state = GeneratorState.RUNNING
                    self.starts += 1
                    self._running_s = 0.0
                else:
                    self.start_failures += 1
                    if self._attempts >= self.max_start_attempts:
                        self.state = GeneratorState.FAILED
                    else:
                        self.state = GeneratorState.RETRY_WAIT
                        self._retry_s = 0.0
            else:
                self._crank_s += ctx.dt
            return

        if self.state is GeneratorState.RETRY_WAIT:
            if self._retry_s >= self.retry_delay_s:
                self.state = GeneratorState.CRANKING
                self._crank_s = 0.0
            else:
                self._retry_s += ctx.dt
            return

        if self.state is GeneratorState.RUNNING:
            self._running_s += ctx.dt
            if not self._start_requested:
                self.state = GeneratorState.COOLDOWN
                self._cooldown_s = 0.0
            return

        if self.state is GeneratorState.COOLDOWN:
            # Keeps turning but off load, so the site does not pay a fresh 30 s
            # start for a grid that flickers twice in a minute.
            if self._cooldown_s >= self.cooldown_s:
                self.state = GeneratorState.STOPPED
            else:
                self._cooldown_s += ctx.dt
            return

        if self.state is GeneratorState.STOPPED and self._start_requested:
            self.state = GeneratorState.CRANKING
            self._crank_s = 0.0
            self._attempts = 0

    def telemetry(self) -> dict[str, float | str]:
        return {
            "state": self.state.value,
            "output_kw": self.output_kw,
            "fuel_l": self.tank.level_l,
            "fuel_rate_l_per_h": self.fuel_rate_l_per_h,
            "run_h": self.run_hours,
            "starts": float(self.starts),
            "start_failures": float(self.start_failures),
            "load_pct": self.monitor.load_pct(self.output_kw),
        }

"""User traffic: where the load actually comes from.

Without this layer the racks follow a synthetic kW curve, and a failure is only
legible in kilowatts — which says nothing about what anyone experienced. Here the
causality runs both ways:

    users arrive -> requests become tokens of work -> the scheduler asks the racks
    for the power needed to serve them -> the racks draw it (or cannot) -> served
    throughput follows from actual draw -> whatever was not served queues -> and
    once the queue is older than the latency budget, requests are dropped.

So a chiller trip is no longer just "the loop reached 87 °C". It throttles the
racks, which cuts serving capacity, which grows the queue, which drops user
requests. That last number is the one worth reporting.

Batch racks sit outside this loop deliberately: training jobs are queued work with
no user waiting, so losing capacity delays them rather than failing them.
"""

import math
from dataclasses import dataclass, field

from app.simulations.sim1.models import DemandResult, RequestMix, Segment, Surge, TickContext
from app.simulations.sim1.rack import Rack
from app.simulations.sim1.units import finite_or_none
from app.simulations.sim1.workload import CorrelatedNoise, diurnal_factor


@dataclass
class UserArrivals:
    """Offered requests per second: a daily cycle, correlated jitter, and surges."""

    peak_rps: float = 160.0
    low_fraction: float = 0.35
    """Night trough as a fraction of the daily peak. Deeper than the rack-level
    profile's 0.45 because this is raw human traffic, before any batch work is
    layered on top of it."""

    peak_hour: float = 14.0
    burstiness: float = 0.08
    burst_tau_s: float = 300.0
    seed: int = 0
    surges: tuple[Surge, ...] = ()
    clock_offset_s: float = 0.0
    """Wall-clock time that ``t = 0`` corresponds to, so a scenario can start on
    the daily peak instead of running up to it."""

    _noise: CorrelatedNoise = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._noise = CorrelatedNoise(ratio=self.burstiness, tau_s=self.burst_tau_s, seed=self.seed)

    def surge_factor(self, t: float) -> float:
        factor = 1.0
        for surge in self.surges:
            factor *= surge.factor(t)
        return factor

    def rps(self, t: float) -> float:
        """Advance the jitter and return this tick's offered rate."""
        base = self.peak_rps * diurnal_factor(t, self.peak_hour, self.low_fraction, self.clock_offset_s)
        return max(0.0, base * self.surge_factor(t) * self._noise.factor(t))


@dataclass
class UserLoad:
    """The scheduler. Turns arrivals into per-rack power asks, and actual draw
    back into served and dropped requests."""

    arrivals: UserArrivals = field(default_factory=UserArrivals)
    mix: RequestMix = field(default_factory=RequestMix)
    batch_catch_up_window_s: float = 3600.0
    """Batch work is owed, not urgent. Draining a backlog over an hour makes the
    racks run a little hotter once power returns; draining it over the
    interactive latency budget would just pin them at peak forever."""

    backlog_tokens: float = field(default=0.0, init=False)
    offered_rps: float = field(default=0.0, init=False)
    served_rps: float = field(default=0.0, init=False)
    dropped_rps: float = field(default=0.0, init=False)
    queue_latency_s: float = field(default=0.0, init=False)
    capacity_rps: float = field(default=0.0, init=False)
    utilisation: float = field(default=0.0, init=False)
    batch_backlog_kwh: float = field(default=0.0, init=False)

    _batch_targets: dict[str, float] = field(default_factory=dict, init=False, repr=False)
    """This tick's un-inflated batch profile asks, kept so ``settle`` can tell
    catch-up draw apart from the profile's own demand without advancing the
    profile's noise a second time."""

    requests_offered: float = field(default=0.0, init=False)
    requests_served: float = field(default=0.0, init=False)
    requests_dropped: float = field(default=0.0, init=False)
    slo_met_s: float = field(default=0.0, init=False)
    total_s: float = field(default=0.0, init=False)
    peak_queue_requests: float = field(default=0.0, init=False)
    peak_latency_s: float = field(default=0.0, init=False)

    def rps_to_tokens(self, rps: float) -> float:
        return rps * self.mix.tokens_per_request

    def tokens_to_rps(self, tokens_per_s: float) -> float:
        if self.mix.tokens_per_request <= 0.0:
            return 0.0
        return tokens_per_s / self.mix.tokens_per_request

    def plan(self, ctx: TickContext, racks: list[Rack]) -> list[float]:
        """Phase 2. Per-rack power ask, in rack order.

        Interactive racks split the offered work plus whatever backlog can be
        drained inside the latency budget — asking for the backlog *now* would
        demand infinite power on the first tick after an outage. Batch racks get
        their own profile's ask, plus a share of any delay they have accrued.
        """
        interactive = [rack for rack in racks if rack.segment is Segment.INTERACTIVE]

        self.offered_rps = self.arrivals.rps(ctx.t) if interactive else 0.0
        arriving_tokens_per_s = self.rps_to_tokens(self.offered_rps)
        drain_window_s = max(ctx.dt, self.mix.slo_latency_s)
        wanted_tokens_per_s = arriving_tokens_per_s + self.backlog_tokens / drain_window_s

        self.capacity_rps = self.tokens_to_rps(sum(rack.capacity_tokens_per_s for rack in interactive))
        if self.capacity_rps > 0.0:
            self.utilisation = self.offered_rps / self.capacity_rps
        else:
            # No capacity at all: utilisation is undefined rather than zero. Zero
            # would render as "idle" at the exact moment every request is being
            # dropped, which is the opposite of what happened.
            self.utilisation = math.inf if self.offered_rps > 0.0 else 0.0

        share = wanted_tokens_per_s / len(interactive) if interactive else 0.0
        asks: list[float] = []
        for rack in racks:
            if rack.segment is Segment.INTERACTIVE:
                asks.append(rack.kw_for(share))
            else:
                # Batch work that was curtailed earlier is owed, not lost, so a
                # backlog lets the scheduler run hotter once power returns.
                target_kw = rack.profile_ask_kw(ctx)
                self._batch_targets[rack.name] = target_kw
                catch_up_kw = self.batch_backlog_kwh * 3600.0 / max(ctx.dt, self.batch_catch_up_window_s)
                asks.append(min(rack.peak_kw, target_kw + catch_up_kw))
        return asks

    def settle(self, ctx: TickContext, racks: list[Rack]) -> DemandResult:
        """Phase 4. Read served throughput off actual draw, then age the queue."""
        interactive = [rack for rack in racks if rack.segment is Segment.INTERACTIVE]
        batch = [rack for rack in racks if rack.segment is Segment.BATCH]

        served_tokens_per_s = sum(rack.tokens_per_s_for(rack.drawn_kw) for rack in interactive)
        arrived_tokens = self.rps_to_tokens(self.offered_rps) * ctx.dt
        served_tokens = min(served_tokens_per_s * ctx.dt, self.backlog_tokens + arrived_tokens)

        self.backlog_tokens = max(0.0, self.backlog_tokens + arrived_tokens - served_tokens)

        # Little's law: how long the standing queue takes to clear at the rate
        # actually being achieved. Undefined, not infinite, when nothing is being
        # served but something is waiting.
        if served_tokens_per_s > 1e-9:
            latency_s = self.backlog_tokens / served_tokens_per_s
        else:
            latency_s = math.inf if self.backlog_tokens > 0.0 else 0.0

        waiting_tokens = self.backlog_tokens
        dropped_tokens = 0.0
        if self.mix.slo_latency_s > 0.0:
            keep_tokens = self.mix.slo_latency_s * served_tokens_per_s
            if self.backlog_tokens > keep_tokens:
                dropped_tokens = self.backlog_tokens - keep_tokens
                self.backlog_tokens = keep_tokens
                latency_s = self.mix.slo_latency_s

        self.served_rps = self.tokens_to_rps(served_tokens / ctx.dt) if ctx.dt > 0.0 else 0.0
        self.dropped_rps = self.tokens_to_rps(dropped_tokens / ctx.dt) if ctx.dt > 0.0 else 0.0
        self.queue_latency_s = latency_s

        self.requests_offered += self.offered_rps * ctx.dt
        self.requests_served += self.tokens_to_rps(served_tokens)
        self.requests_dropped += self.tokens_to_rps(dropped_tokens)
        self.total_s += ctx.dt
        if dropped_tokens <= 0.0 and (not math.isfinite(latency_s) or latency_s <= self.mix.slo_latency_s):
            self.slo_met_s += ctx.dt

        queued = self.tokens_to_rps(self.backlog_tokens)
        # Measured before abandonment: once requests are dropped the standing
        # queue is legitimately empty, so a post-clamp peak would read zero for
        # exactly the runs where the queue mattered most.
        self.peak_queue_requests = max(self.peak_queue_requests, self.tokens_to_rps(waiting_tokens))
        if math.isfinite(latency_s):
            self.peak_latency_s = max(self.peak_latency_s, latency_s)

        # Batch work owed: what the profile asked for and did not get.
        shortfall_kwh = sum(max(0.0, rack.demand_kw - rack.drawn_kw) for rack in batch) * ctx.dt / 3600.0
        credited_kwh = (
            sum(max(0.0, rack.drawn_kw - self._batch_targets.get(rack.name, rack.drawn_kw)) for rack in batch)
            * ctx.dt
            / 3600.0
        )
        self.batch_backlog_kwh = max(0.0, self.batch_backlog_kwh + shortfall_kwh - credited_kwh)

        return DemandResult(
            offered_rps=self.offered_rps,
            served_rps=self.served_rps,
            dropped_rps=self.dropped_rps,
            queued_requests=queued,
            queue_latency_s=finite_or_none(latency_s),
            capacity_rps=self.capacity_rps,
            utilisation=self.utilisation,
            batch_backlog_kwh=self.batch_backlog_kwh,
        )

    @property
    def slo_compliance_pct(self) -> float:
        return 100.0 * self.slo_met_s / self.total_s if self.total_s > 0.0 else 100.0

    @property
    def drop_pct(self) -> float:
        return 100.0 * self.requests_dropped / self.requests_offered if self.requests_offered > 0.0 else 0.0

    def telemetry(self) -> dict[str, float | str | None]:
        return {
            "offered_rps": self.offered_rps,
            "served_rps": self.served_rps,
            "dropped_rps": self.dropped_rps,
            "capacity_rps": self.capacity_rps,
            "queued_requests": self.tokens_to_rps(self.backlog_tokens),
            "queue_latency_s": self.queue_latency_s if math.isfinite(self.queue_latency_s) else self.mix.slo_latency_s,
            "utilisation_pct": finite_or_none(100.0 * self.utilisation),
            "batch_backlog_kwh": self.batch_backlog_kwh,
        }

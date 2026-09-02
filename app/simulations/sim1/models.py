"""Every shape the simulation passes around, and nothing that acts on one.

One module for the data types so that a component, the engine, the telemetry
layer and the viewer all name the same structure instead of each growing its
own. The rule that keeps it from becoming a junk drawer: this module may import
:mod:`units` and the standard library, and nothing else in ``sim1``. Anything
that needs a rack, a feed or a DataFrame's contents to do its job is behaviour
and belongs in the module that owns that behaviour.

The types come in four groups: what a tick hands a component (:class:`TickContext`)
and what it hands back (:class:`Delivery`, :class:`LoadResult`, :class:`ThermalStep`);
what the load is made of (:class:`Segment`, :class:`Profile`, :class:`SpikeWindow`,
:class:`Surge`, :class:`RequestMix`, :class:`DemandResult`); what a run is
configured by (:class:`ScheduledEvent`, :class:`SiteConfig`, :class:`ScenarioConfig`);
and what it leaves behind (:class:`SeriesSpec`, :class:`Buckets`, :class:`RunResult`,
:class:`Artifacts`, :class:`VideoSpec`).
"""

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

import pandas as pd

from app.simulations.sim1.units import DAY_SECONDS, clamp

# --- The tick contract -------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TickContext:
    """Everything a component may know about the current tick."""

    t: float
    """Simulated seconds since the start of the run."""

    dt: float
    """Length of *this* tick. Varies across a run — see ``engine`` refinement."""

    tick: int
    """Monotonic tick index. Useful for row ids; never for integration."""


@dataclass(frozen=True, slots=True)
class Delivery:
    """What one component did with power over one tick.

    The five terms exist so that a single identity covers passives, sources and
    storage alike::

        drawn_kw + injected_kw == delivered_kw + loss_kw + stored_kw

    ``loss_kw`` is electrical loss that became heat inside the component.
    ``injected_kw`` is power created here rather than passed through (battery
    discharge onto the DC bus, alternator output). ``stored_kw`` is power that
    left the electrical bus into storage. Losses *internal* to a battery sit
    outside this identity, because the stored energy is outside it too.
    """

    delivered_kw: float = 0.0
    drawn_kw: float = 0.0
    loss_kw: float = 0.0
    injected_kw: float = 0.0
    stored_kw: float = 0.0

    @property
    def residual_kw(self) -> float:
        """Signed violation of the balance identity. Zero for a correct component."""
        return (self.drawn_kw + self.injected_kw) - (self.delivered_kw + self.loss_kw + self.stored_kw)

    @classmethod
    def passive(cls, drawn_kw: float, delivered_kw: float) -> "Delivery":
        """A pass-through component: everything not delivered was lost as heat."""
        return cls(delivered_kw=delivered_kw, drawn_kw=drawn_kw, loss_kw=drawn_kw - delivered_kw)


@dataclass(frozen=True, slots=True)
class LoadResult:
    """What one load did with the power it was granted."""

    drawn_kw: float
    """Electrical draw. For a rack this equals the grant, exactly, by construction."""

    heat_kw: float
    """Heat generated. For IT equipment this equals ``drawn_kw``."""

    removed_kw: float
    """Average heat actually shed to the cooling sink over the tick."""

    temp_c: float
    """Temperature at the *end* of the tick."""

    state: str


@dataclass(frozen=True, slots=True)
class ThermalStep:
    temp_c: float
    """Body temperature at the end of the tick."""

    removed_kws: float
    """Heat energy (kW*s) that left the body into the sink over the tick."""

    def removed_kw_over(self, dt: float) -> float:
        """Average removal rate over a tick of length ``dt``."""
        return self.removed_kws / dt if dt > 0.0 else 0.0


# --- What the load is made of ------------------------------------------------


class Segment(StrEnum):
    """Who decides how hard a rack works.

    An INTERACTIVE rack serves user requests: its power follows arriving traffic,
    and losing capacity means queued and dropped requests. A BATCH rack runs
    scheduled training: it follows its own profile, and losing capacity delays
    jobs rather than failing them. The distinction matters because it is what
    makes a power or cooling failure legible in *user* terms.
    """

    INTERACTIVE = "interactive"
    BATCH = "batch"


class Profile(StrEnum):
    IDLE = "idle"
    INFERENCE = "inference"
    TRAINING = "training"
    BURST = "burst"


PROFILE_SEGMENTS: dict[Profile, Segment] = {
    Profile.IDLE: Segment.INTERACTIVE,
    Profile.INFERENCE: Segment.INTERACTIVE,
    Profile.TRAINING: Segment.BATCH,
    Profile.BURST: Segment.BATCH,
}


@dataclass(frozen=True, slots=True)
class SpikeWindow:
    """A scripted demand override, as a fraction of the rack's peak."""

    start_s: float
    end_s: float
    peak_fraction: float = 1.0

    def contains(self, t: float) -> bool:
        return self.start_s <= t < self.end_s


@dataclass(frozen=True, slots=True)
class Surge:
    """A scripted multiplier on arrivals — a launch, a viral moment, a redirect."""

    start_s: float
    end_s: float
    multiplier: float = 2.0
    ramp_s: float = 300.0
    """Traffic does not step. Ramping in and out over a few minutes is both more
    honest and what makes the queue's response readable."""

    label: str = ""

    def factor(self, t: float) -> float:
        if t < self.start_s or t >= self.end_s:
            return 1.0
        rise = 1.0 if self.ramp_s <= 0.0 else clamp((t - self.start_s) / self.ramp_s, 0.0, 1.0)
        fall = 1.0 if self.ramp_s <= 0.0 else clamp((self.end_s - t) / self.ramp_s, 0.0, 1.0)
        return 1.0 + (self.multiplier - 1.0) * min(rise, fall)


@dataclass
class RequestMix:
    tokens_per_request: float = 420.0
    """Mean work per request. Prompt plus generated tokens for a chat turn."""

    slo_latency_s: float = 8.0
    """How long a user will wait. Requests still queued beyond this are
    abandoned — a real drop, not a slow success, and the only honest way to make
    a capacity shortfall show up as something other than growing latency."""


@dataclass(frozen=True, slots=True)
class DemandResult:
    offered_rps: float
    served_rps: float
    dropped_rps: float
    queued_requests: float
    queue_latency_s: float | None
    capacity_rps: float
    utilisation: float
    batch_backlog_kwh: float


# --- How a run is configured -------------------------------------------------


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
class SiteConfig:
    """The reference build: 4 x GB300 NVL72 behind a 2N electrical chain."""

    n_racks: int = 4
    rack_nominal_kw: float = 135.0
    rack_peak_kw: float = 155.0
    profiles: tuple[str, ...] | str = ("training", "inference", "inference", "training")
    """Two racks running scheduled training, two serving user traffic. Scenarios
    that are purely electrical tests override this with all-batch racks."""
    spikes: tuple[SpikeWindow, ...] = ()

    grid_capacity_kw: float = 1500.0
    transformer_capacity_kw: float = 1000.0
    ups_rating_kw: float = 750.0
    pdu_rating_kw: float = 800.0

    battery_capacity_kwh: float = 60.0
    """Per side. Each string holds roughly five minutes at the full *site* load,
    so healthy 2N gives ten minutes of autonomy and a lost side gives five."""

    generator_rating_kw: float = 800.0
    generator_start_success_p: float = 0.98
    generator_start_time_s: float = 30.0
    fuel_capacity_l: float = 4000.0

    chiller_capacity_kw: float = 600.0
    crah_capacity_kw: float = 100.0
    loop_volume_l: float = 5000.0
    pump_demand_kw: float = 20.0

    cord_limit_kw: float = 900.0

    rack_peak_tokens_per_s: float = 40_000.0
    peak_rps_per_rack: float = 76.0
    """Daily-peak request rate per *interactive* rack. Expressed per rack so that
    changing how many racks serve users changes utilisation, not the traffic —
    Because peak power and peak throughput are the same point in this model, a
    rack at its nominal draw is already at ~85 % of its throughput ceiling: 76 rps
    at 420 tokens each puts it near nominal at the daily peak and leaves ~20 % of
    its throughput in reserve, which the burstiness eats into before any surge
    arrives."""

    tokens_per_request: float = 420.0
    slo_latency_s: float = 8.0
    traffic_low_fraction: float = 0.35
    traffic_burstiness: float = 0.08
    start_hour: float = 0.0
    """Wall-clock hour that ``t = 0`` corresponds to.

    A failure only means something if it lands on busy traffic, and the daily peak
    is at 14:00. Rather than run twelve idle hours to get there, an incident
    scenario starts its clock at 13:00 and schedules the failure a few minutes in
    — same load, a fraction of the waiting."""

    surges: tuple[Surge, ...] = ()

    seed: int = 1

    @property
    def it_nominal_kw(self) -> float:
        return self.n_racks * self.rack_nominal_kw

    @property
    def it_peak_kw(self) -> float:
        return self.n_racks * self.rack_peak_kw

    @property
    def interactive_racks(self) -> int:
        return sum(1 for profile in self.rack_profiles() if PROFILE_SEGMENTS[Profile(profile)] is Segment.INTERACTIVE)

    @property
    def peak_rps(self) -> float:
        return self.peak_rps_per_rack * self.interactive_racks

    def rack_profiles(self) -> list[str]:
        if isinstance(self.profiles, str):
            return [self.profiles] * self.n_racks
        if len(self.profiles) != self.n_racks:
            raise ValueError(f"expected {self.n_racks} profiles, got {len(self.profiles)}")
        return list(self.profiles)


@dataclass
class ScenarioConfig:
    name: str
    description: str
    site: SiteConfig = field(default_factory=SiteConfig)
    events: tuple[ScheduledEvent, ...] = ()
    default_duration_s: float = 7 * DAY_SECONDS
    default_dt_s: float = 60.0
    default_dt_fine_s: float = 1.0
    """A week-long run to observe a 30 second generator start is the wrong
    default, so each scenario carries its own timescale rather than inheriting
    one global duration."""


# --- What a run leaves behind ------------------------------------------------


@dataclass(frozen=True, slots=True)
class SeriesSpec:
    """How one column survives downsampling and quantization.

    ``agg`` is not cosmetic. Averaging a state code is meaningless, and
    averaging a rate over a bucket is the only aggregation that preserves
    energy. ``companion`` ships a second series for columns where the *peak* is
    the point: a 60 second 105 % UPS overload averaged into a five-minute bucket
    vanishes, which would downsample away the finding a scenario exists to show.
    """

    column: str
    scale: float
    agg: str = "mean"
    companion: str | None = None
    label: str = ""

    @property
    def companion_column(self) -> str | None:
        return None if self.companion is None else f"{self.column}__{self.companion}"


@dataclass(frozen=True, slots=True)
class Buckets:
    groups: tuple[tuple[int, ...], ...]
    """Row-index groups, in time order. Every row belongs to exactly one group."""

    def __len__(self) -> int:
        return len(self.groups)


@dataclass(frozen=True, slots=True)
class RunResult:
    scenario: str
    description: str
    facility: pd.DataFrame
    racks: pd.DataFrame
    events: pd.DataFrame
    kpis: dict[str, float | str | None]
    duration_s: float
    dt_s: float
    dt_fine_s: float


@dataclass(frozen=True, slots=True)
class Artifacts:
    directory: Path
    viz: Path | None = None
    analysis: Path | None = None
    facility_csv: Path | None = None
    racks_csv: Path | None = None
    events_csv: Path | None = None
    kpis_json: Path | None = None

    def written(self) -> list[Path]:
        return [
            p
            for p in (self.viz, self.analysis, self.facility_csv, self.racks_csv, self.events_csv, self.kpis_json)
            if p
        ]


@dataclass(frozen=True, slots=True)
class VideoSpec:
    """How the clip is recorded. Defaults give a 90 s 1080p file of ~10 MB.

    Ninety seconds rather than forty-five because a run read at 30 payload points
    per second is faster than anyone can follow the state changes.
    """

    seconds: float = 90.0
    fps: int = 30
    width: int = 1920
    height: int = 1080
    scale: int = 2  # device pixel ratio to capture at, then downscale — sharper text
    watermark: str = ""
    watermark_opacity: float = 0.22  # against the page's near-black ground
    timeline: bool = False  # the ribbon strip costs the diagram a third of the frame
    crf: int = 20
    quality: int = 92  # JPEG quality of the frames handed to ffmpeg

    @property
    def frames(self) -> int:
        return max(2, round(self.seconds * self.fps))

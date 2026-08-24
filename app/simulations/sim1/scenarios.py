"""Site configuration, the eight scenarios, and the design-margin arithmetic.

Config is Python dataclasses in a module-level dict, as in sim0 — no YAML. The
values are typed, the defaults are documented next to the field they belong to,
and a scenario can compute derived numbers instead of repeating them.
"""

from dataclasses import dataclass, field

from app.simulations.sim1.cooling.cdu import Cdu
from app.simulations.sim1.cooling.chiller import Chiller
from app.simulations.sim1.cooling.crah import Crah
from app.simulations.sim1.cooling.loop import CoolantLoop
from app.simulations.sim1.cooling.plant import CoolingPlant
from app.simulations.sim1.economics import Economics
from app.simulations.sim1.electrical.ats import AutomaticTransferSwitch
from app.simulations.sim1.electrical.battery import BatteryString
from app.simulations.sim1.electrical.feed import Feed
from app.simulations.sim1.electrical.generator import DieselGenerator, FuelTank
from app.simulations.sim1.electrical.grid import GridFeed
from app.simulations.sim1.electrical.pdu import Pdu
from app.simulations.sim1.electrical.transformer import Transformer
from app.simulations.sim1.electrical.ups import Ups
from app.simulations.sim1.events import EventSchedule, ScheduledEvent
from app.simulations.sim1.facility import Facility
from app.simulations.sim1.rack import Rack
from app.simulations.sim1.units import DAY_SECONDS
from app.simulations.sim1.workload import SpikeWindow, WorkloadProfile

HOUR = 3600.0


@dataclass
class SiteConfig:
    """The reference build: 4 x GB300 NVL72 behind a 2N electrical chain."""

    n_racks: int = 4
    rack_nominal_kw: float = 135.0
    rack_peak_kw: float = 155.0
    profiles: tuple[str, ...] | str = "training"
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
    seed: int = 1

    @property
    def it_nominal_kw(self) -> float:
        return self.n_racks * self.rack_nominal_kw

    @property
    def it_peak_kw(self) -> float:
        return self.n_racks * self.rack_peak_kw

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


def design_margins(site: SiteConfig) -> dict[str, float]:
    """The sizing arithmetic that decides whether the scenarios are interesting.

    Recomputed from the config rather than written down, so a change to
    ``rack_peak_kw`` cannot silently turn ``normal`` into an overload run — see
    ``tests/simulations/sim1/test_reference_config.py``.
    """
    chiller = Chiller(capacity_kw=site.chiller_capacity_kw)
    crah = Crah(capacity_kw=site.crah_capacity_kw)
    capture = Rack(name="ref", workload=WorkloadProfile("idle")).liquid_capture_rate
    ups = Ups(name="ref", battery=BatteryString(name="ref"), rating_kw=site.ups_rating_kw)
    transformer = Transformer(name="ref", capacity_kw=site.transformer_capacity_kw)

    def mech_for(it_kw: float) -> float:
        return (
            chiller.electrical_for(it_kw * capture) + crah.electrical_for(it_kw * (1.0 - capture)) + site.pump_demand_kw
        )

    nominal_out = site.it_nominal_kw + mech_for(site.it_nominal_kw)
    peak_out = site.it_peak_kw + mech_for(site.it_peak_kw)
    nominal_in = nominal_out / ups.double_conversion_efficiency
    facility_nominal = nominal_in + transformer.loss_at(nominal_in)

    return {
        "it_nominal_kw": site.it_nominal_kw,
        "it_peak_kw": site.it_peak_kw,
        "mech_nominal_kw": mech_for(site.it_nominal_kw),
        "mech_peak_kw": mech_for(site.it_peak_kw),
        "ups_out_nominal_kw": nominal_out,
        "ups_out_peak_kw": peak_out,
        "facility_nominal_kw": facility_nominal,
        "pue_nominal": facility_nominal / site.it_nominal_kw,
        "single_side_nominal_pct": 100.0 * nominal_out / site.ups_rating_kw,
        "single_side_peak_pct": 100.0 * peak_out / site.ups_rating_kw,
        "generator_headroom_kw": site.generator_rating_kw - facility_nominal,
    }


def build_facility(scenario: ScenarioConfig, seed: int | None = None) -> Facility:
    site = scenario.site
    base_seed = site.seed if seed is None else seed

    racks = [
        Rack(
            name=f"rack-{i + 1}",
            nominal_kw=site.rack_nominal_kw,
            peak_kw=site.rack_peak_kw,
            workload=WorkloadProfile(
                profile=profile,
                nominal_kw=site.rack_nominal_kw,
                peak_kw=site.rack_peak_kw,
                # Explicit integer offsets, never hash(str): string hashing is
                # salted per process, so a hashed seed would not reproduce
                # across invocations.
                seed=base_seed * 1000 + i,
                spikes=site.spikes,
            ),
        )
        for i, profile in enumerate(site.rack_profiles())
    ]

    def make_side(label: str) -> Feed:
        return Feed(
            side=label,
            grid=GridFeed(name=f"grid-{label}", capacity_kw=site.grid_capacity_kw),
            transformer=Transformer(name=f"tx-{label}", capacity_kw=site.transformer_capacity_kw),
            ats=AutomaticTransferSwitch(name=f"ats-{label}"),
            ups=Ups(
                name=f"ups-{label}",
                battery=BatteryString(name=f"batt-{label}", capacity_kwh=site.battery_capacity_kwh),
                rating_kw=site.ups_rating_kw,
            ),
            pdu=Pdu(name=f"pdu-{label}", rating_kw=site.pdu_rating_kw),
        )

    return Facility(
        side_a=make_side("a"),
        side_b=make_side("b"),
        genset=DieselGenerator(
            rating_kw=site.generator_rating_kw,
            start_time_s=site.generator_start_time_s,
            start_success_p=site.generator_start_success_p,
            seed=base_seed,
            tank=FuelTank(capacity_l=site.fuel_capacity_l, level_l=site.fuel_capacity_l),
        ),
        cooling=CoolingPlant(
            loop=CoolantLoop(volume_l=site.loop_volume_l),
            cdu=Cdu(pump_demand_kw=site.pump_demand_kw),
            chiller=Chiller(capacity_kw=site.chiller_capacity_kw),
            crah=Crah(capacity_kw=site.crah_capacity_kw),
        ),
        racks=racks,
        economics=Economics(),
        schedule=EventSchedule(events=scenario.events),
        cord_limit_kw=site.cord_limit_kw,
    )


_PEAK_EVERYWHERE = (SpikeWindow(start_s=0.0, end_s=1e9, peak_fraction=1.0),)

_SCENARIOS: dict[str, ScenarioConfig] = {
    "normal": ScenarioConfig(
        name="normal",
        description="Steady state: diurnal load, 50/50 across both sides, PUE around 1.27.",
        site=SiteConfig(profiles=("training", "inference", "inference", "training"), seed=1),
        default_duration_s=7 * DAY_SECONDS,
        default_dt_s=60.0,
    ),
    "grid_outage_gen_ok": ScenarioConfig(
        name="grid_outage_gen_ok",
        description=(
            "Both utility feeds drop. The UPSes ride it out on battery, the genset cranks for 30 s, "
            "each ATS transfers, diesel burns, and the batteries recharge once the mains return."
        ),
        site=SiteConfig(profiles="training", seed=2),
        events=(
            ScheduledEvent(t=1800.0, target="grid_a", action="offline", settle_s=900.0, label="Utility A lost"),
            ScheduledEvent(t=1800.0, target="grid_b", action="offline", settle_s=900.0, label="Utility B lost"),
            ScheduledEvent(t=3600.0, target="grid_a", action="online", settle_s=900.0, label="Utility A restored"),
            ScheduledEvent(t=3600.0, target="grid_b", action="online", settle_s=900.0, label="Utility B restored"),
        ),
        default_duration_s=2 * HOUR,
        default_dt_s=10.0,
        default_dt_fine_s=1.0,
    ),
    "grid_outage_gen_fail": ScenarioConfig(
        name="grid_outage_gen_fail",
        description=(
            "The same outage with a genset that will not start. Three attempts fail, the batteries "
            "drain to their cutoff, and the site goes dark."
        ),
        site=SiteConfig(profiles="training", generator_start_success_p=0.0, seed=3),
        events=(
            ScheduledEvent(t=600.0, target="grid_a", action="offline", settle_s=1800.0, label="Utility A lost"),
            ScheduledEvent(t=600.0, target="grid_b", action="offline", settle_s=1800.0, label="Utility B lost"),
        ),
        default_duration_s=40 * 60.0,
        default_dt_s=5.0,
        default_dt_fine_s=1.0,
    ),
    "load_spike": ScenarioConfig(
        name="load_spike",
        description=(
            "A training burst pins every rack at peak for three hours. The answer this scenario gives "
            "is 'yes, but only just': liquid heat reaches about 558 kW against 600 kW of chiller, while "
            "each UPS sits near half load. Cooling, not power, is the tighter margin."
        ),
        site=SiteConfig(
            profiles="training",
            spikes=(SpikeWindow(start_s=2 * HOUR, end_s=5 * HOUR, peak_fraction=1.0),),
            seed=4,
        ),
        events=(
            ScheduledEvent(t=2 * HOUR, target="marker", action="note", settle_s=3600.0, label="Load spike begins"),
            ScheduledEvent(t=5 * HOUR, target="marker", action="note", settle_s=1800.0, label="Load spike ends"),
        ),
        default_duration_s=8 * HOUR,
        default_dt_s=30.0,
        default_dt_fine_s=5.0,
    ),
    "cooling_failure": ScenarioConfig(
        name="cooling_failure",
        description=(
            "The chiller trips. The loop climbs about 1.5 C per minute, rack delta-T collapses, racks "
            "throttle and then shut down — and the chiller's own electrical draw disappears from the "
            "power stack at the same time. It is restored after four hours so the recovery path is "
            "visible too — without that, the hall never comes back, because nothing else can pull heat "
            "out of the loop."
        ),
        site=SiteConfig(profiles="training", seed=5),
        events=(
            ScheduledEvent(t=HOUR, target="chiller", action="fault", settle_s=3 * HOUR, label="Chiller tripped"),
            ScheduledEvent(t=5 * HOUR, target="chiller", action="restore", settle_s=2 * HOUR, label="Chiller restored"),
        ),
        default_duration_s=8 * HOUR,
        default_dt_s=30.0,
        default_dt_fine_s=5.0,
    ),
    "side_a_lost": ScenarioConfig(
        name="side_a_lost",
        description=(
            "Transformer A trips. UPS A quietly burns its battery down with no input, then side B "
            "carries the whole site at about 88 % of its nameplate and survives."
        ),
        site=SiteConfig(profiles="training", seed=6),
        events=(
            ScheduledEvent(t=1800.0, target="tx_a", action="trip", settle_s=2400.0, label="Transformer A tripped"),
        ),
        default_duration_s=4 * HOUR,
        default_dt_s=10.0,
        default_dt_fine_s=1.0,
    ),
    "side_a_lost_at_peak": ScenarioConfig(
        name="side_a_lost_at_peak",
        description=(
            "The same failure with IT at peak. UPS B sits just over 100 %, its hold timer expires and "
            "it transfers to bypass: the load survives, but battery protection is gone."
        ),
        site=SiteConfig(profiles="training", spikes=_PEAK_EVERYWHERE, seed=7),
        events=(
            ScheduledEvent(t=1800.0, target="tx_a", action="trip", settle_s=2400.0, label="Transformer A tripped"),
        ),
        default_duration_s=4 * HOUR,
        default_dt_s=10.0,
        default_dt_fine_s=1.0,
    ),
    "undersized_cords": ScenarioConfig(
        name="undersized_cords",
        description=(
            "2N on paper only: each cord is rated for 60 % of site peak. Losing a side curtails IT "
            "instead of failing over cleanly."
        ),
        site=SiteConfig(profiles="training", cord_limit_kw=474.0, seed=8),
        events=(
            ScheduledEvent(t=1800.0, target="tx_a", action="trip", settle_s=2400.0, label="Transformer A tripped"),
        ),
        default_duration_s=4 * HOUR,
        default_dt_s=10.0,
        default_dt_fine_s=1.0,
    ),
}


def scenario_names() -> list[str]:
    return list(_SCENARIOS)


def get_scenario(name: str) -> ScenarioConfig:
    try:
        return _SCENARIOS[name]
    except KeyError:
        raise ValueError(f"unknown sim1 scenario {name!r}; available: {', '.join(_SCENARIOS)}") from None

"""The nine scenarios, the build they share, and the design-margin arithmetic.

Config is Python dataclasses in a module-level dict, as in sim0 — no YAML. The
values are typed, the defaults are documented next to the field they belong to,
and a scenario can compute derived numbers instead of repeating them. The
dataclasses themselves — ``SiteConfig``, ``ScenarioConfig`` — are shapes, so
they live in :mod:`models`; what is here is the table of scenarios and the code
that turns one into a running :class:`Facility`.
"""

from app.simulations.sim1.cooling.cdu import Cdu
from app.simulations.sim1.cooling.chiller import Chiller
from app.simulations.sim1.cooling.crah import Crah
from app.simulations.sim1.cooling.loop import CoolantLoop
from app.simulations.sim1.cooling.plant import CoolingPlant
from app.simulations.sim1.demand import UserArrivals, UserLoad
from app.simulations.sim1.economics import Economics, Tariff
from app.simulations.sim1.electrical.ats import AutomaticTransferSwitch
from app.simulations.sim1.electrical.battery import BatteryString
from app.simulations.sim1.electrical.feed import Feed
from app.simulations.sim1.electrical.generator import DieselGenerator, FuelTank
from app.simulations.sim1.electrical.grid import GridFeed
from app.simulations.sim1.electrical.pdu import Pdu
from app.simulations.sim1.electrical.transformer import Transformer
from app.simulations.sim1.electrical.ups import Ups
from app.simulations.sim1.events import EventSchedule
from app.simulations.sim1.facility import Facility
from app.simulations.sim1.models import RequestMix, ScenarioConfig, ScheduledEvent, SiteConfig, SpikeWindow, Surge
from app.simulations.sim1.rack import Rack
from app.simulations.sim1.units import DAY_SECONDS
from app.simulations.sim1.workload import WorkloadProfile

MINUTE = 60.0
HOUR = 3600.0


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
        "interactive_racks": float(site.interactive_racks),
        "peak_rps": site.peak_rps,
        "capacity_rps": site.interactive_racks * site.rack_peak_tokens_per_s / site.tokens_per_request,
        "peak_utilisation_pct": (
            100.0 * site.peak_rps * site.tokens_per_request / (site.interactive_racks * site.rack_peak_tokens_per_s)
            if site.interactive_racks
            else 0.0
        ),
    }


def build_facility(scenario: ScenarioConfig, seed: int | None = None) -> Facility:
    site = scenario.site
    base_seed = site.seed if seed is None else seed

    racks = [
        Rack(
            name=f"rack-{i + 1}",
            nominal_kw=site.rack_nominal_kw,
            peak_kw=site.rack_peak_kw,
            peak_tokens_per_s=site.rack_peak_tokens_per_s,
            workload=WorkloadProfile(
                profile=profile,
                nominal_kw=site.rack_nominal_kw,
                peak_kw=site.rack_peak_kw,
                # Explicit integer offsets, never hash(str): string hashing is
                # salted per process, so a hashed seed would not reproduce
                # across invocations.
                seed=base_seed * 1000 + i,
                spikes=site.spikes,
                clock_offset_s=site.start_hour * HOUR,
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
            tank=FuelTank(level_l=site.fuel_capacity_l),
        ),
        cooling=CoolingPlant(
            loop=CoolantLoop(volume_l=site.loop_volume_l),
            cdu=Cdu(pump_demand_kw=site.pump_demand_kw),
            chiller=Chiller(capacity_kw=site.chiller_capacity_kw),
            crah=Crah(capacity_kw=site.crah_capacity_kw),
        ),
        racks=racks,
        economics=Economics(tariff=Tariff(clock_offset_s=site.start_hour * HOUR)),
        user_load=UserLoad(
            arrivals=UserArrivals(
                peak_rps=site.peak_rps,
                low_fraction=site.traffic_low_fraction,
                burstiness=site.traffic_burstiness,
                seed=base_seed * 7919,
                surges=site.surges,
                clock_offset_s=site.start_hour * HOUR,
            ),
            mix=RequestMix(tokens_per_request=site.tokens_per_request, slo_latency_s=site.slo_latency_s),
        ),
        schedule=EventSchedule(events=scenario.events),
        cord_limit_kw=site.cord_limit_kw,
    )


_PEAK_EVERYWHERE = (SpikeWindow(start_s=0.0, end_s=1e9, peak_fraction=1.0),)
_ALL_USERS = ("inference", "inference", "inference", "inference")

_SCENARIOS: dict[str, ScenarioConfig] = {
    "normal": ScenarioConfig(
        name="normal",
        description=(
            "Steady state over a week: user traffic follows the day, two racks train in the background, "
            "load splits 50/50 across both sides, PUE around 1.27 and nothing is dropped."
        ),
        site=SiteConfig(seed=1),
        default_duration_s=7 * DAY_SECONDS,
        default_dt_s=60.0,
    ),
    "grid_outage_gen_ok": ScenarioConfig(
        name="grid_outage_gen_ok",
        description=(
            "Both utility feeds drop at 10 min and return at 25 min. The UPSes ride it out on battery, "
            "the genset cranks for 30 s, each ATS transfers, diesel burns, and the batteries recharge. "
            "Users never notice."
        ),
        site=SiteConfig(start_hour=13.0, seed=2),
        events=(
            ScheduledEvent(t=10 * MINUTE, target="grid_a", action="offline", settle_s=900.0, label="Utility A lost"),
            ScheduledEvent(t=10 * MINUTE, target="grid_b", action="offline", settle_s=900.0, label="Utility B lost"),
            ScheduledEvent(t=25 * MINUTE, target="grid_a", action="online", settle_s=900.0, label="Utility A restored"),
            ScheduledEvent(t=25 * MINUTE, target="grid_b", action="online", settle_s=900.0, label="Utility B restored"),
        ),
        default_duration_s=HOUR,
        default_dt_s=10.0,
        default_dt_fine_s=1.0,
    ),
    "grid_outage_gen_fail": ScenarioConfig(
        name="grid_outage_gen_fail",
        description=(
            "The same outage at 5 min, with a genset that will not start. Three attempts fail, the "
            "batteries drain to their cutoff, the site goes dark — and every user request is dropped "
            "from that moment on."
        ),
        site=SiteConfig(start_hour=13.0, generator_start_success_p=0.0, seed=3),
        events=(
            ScheduledEvent(t=5 * MINUTE, target="grid_a", action="offline", settle_s=1800.0, label="Utility A lost"),
            ScheduledEvent(t=5 * MINUTE, target="grid_b", action="offline", settle_s=1800.0, label="Utility B lost"),
        ),
        default_duration_s=35 * MINUTE,
        default_dt_s=5.0,
        default_dt_fine_s=1.0,
    ),
    "user_surge": ScenarioConfig(
        name="user_surge",
        description=(
            "All four racks serve users, and traffic goes to 1.5x from 15 min to 75 min. Power and "
            "cooling both hold, but the racks are already near their throughput ceiling: the queue "
            "builds, latency hits the 8 s budget and requests start being dropped. The constraint is "
            "compute, not kW."
        ),
        site=SiteConfig(
            profiles=_ALL_USERS,
            surges=(Surge(start_s=15 * MINUTE, end_s=75 * MINUTE, multiplier=1.5, label="Traffic surge"),),
            start_hour=13.0,
            seed=9,
        ),
        events=(
            ScheduledEvent(t=15 * MINUTE, target="marker", action="note", settle_s=HOUR, label="Traffic surge begins"),
            ScheduledEvent(t=75 * MINUTE, target="marker", action="note", settle_s=1800.0, label="Traffic surge ends"),
        ),
        default_duration_s=2 * HOUR,
        default_dt_s=15.0,
        default_dt_fine_s=5.0,
    ),
    "load_spike": ScenarioConfig(
        name="load_spike",
        description=(
            "The power-and-cooling question with users out of the way: all four racks run batch pinned "
            "at peak from 15 min to 75 min. Power, yes — each UPS stays near half load. Cooling is the "
            "tighter margin, with about 44 kW of chiller headroom left of 600."
        ),
        site=SiteConfig(
            profiles="training",
            spikes=(SpikeWindow(start_s=15 * MINUTE, end_s=75 * MINUTE, peak_fraction=1.0),),
            seed=4,
        ),
        events=(
            ScheduledEvent(t=15 * MINUTE, target="marker", action="note", settle_s=1800.0, label="Batch load at peak"),
            ScheduledEvent(t=75 * MINUTE, target="marker", action="note", settle_s=1800.0, label="Peak load ends"),
        ),
        default_duration_s=2 * HOUR,
        default_dt_s=15.0,
        default_dt_fine_s=5.0,
    ),
    "cooling_failure": ScenarioConfig(
        name="cooling_failure",
        description=(
            "The chiller trips at 15 min. The loop climbs about 1.5 C per minute, rack delta-T "
            "collapses, racks throttle and then shut down — and the chiller's own electrical draw "
            "disappears from the power stack at the same time. Watch the request queue: throttling cuts "
            "serving capacity long before the racks go dark. Restored at 105 min, because nothing else "
            "can pull heat out of the loop."
        ),
        site=SiteConfig(start_hour=13.0, seed=5),
        events=(
            ScheduledEvent(t=15 * MINUTE, target="chiller", action="fault", settle_s=2 * HOUR, label="Chiller tripped"),
            ScheduledEvent(t=105 * MINUTE, target="chiller", action="restore", settle_s=HOUR, label="Chiller restored"),
        ),
        default_duration_s=3 * HOUR,
        default_dt_s=30.0,
        default_dt_fine_s=5.0,
    ),
    "side_a_lost": ScenarioConfig(
        name="side_a_lost",
        description=(
            "Transformer A trips at 15 min. UPS A quietly burns its battery down with no input, then "
            "side B carries the whole site at about 94 % of its nameplate and survives. Users see "
            "nothing."
        ),
        # `start_hour` puts t=0 on the daily traffic peak, so the trip lands on a
        # busy site 15 minutes in rather than 13 hours in.
        site=SiteConfig(start_hour=13.0, seed=6),
        events=(
            ScheduledEvent(t=15 * MINUTE, target="tx_a", action="trip", settle_s=HOUR, label="Transformer A tripped"),
        ),
        default_duration_s=90 * MINUTE,
        default_dt_s=15.0,
        default_dt_fine_s=1.0,
    ),
    "side_a_lost_at_peak": ScenarioConfig(
        name="side_a_lost_at_peak",
        description=(
            "A purely electrical test: all four racks run batch at peak, so there is no user traffic to "
            "confuse the picture. Transformer A trips at 10 min, UPS B sits just over 100 %, its hold "
            "timer expires and it transfers to bypass — the load survives, but battery protection is "
            "gone."
        ),
        site=SiteConfig(profiles="training", spikes=_PEAK_EVERYWHERE, seed=7),
        events=(
            ScheduledEvent(t=10 * MINUTE, target="tx_a", action="trip", settle_s=1800.0, label="Transformer A tripped"),
        ),
        default_duration_s=HOUR,
        default_dt_s=10.0,
        default_dt_fine_s=1.0,
    ),
    "undersized_cords": ScenarioConfig(
        name="undersized_cords",
        description=(
            "2N on paper only: each cord is rated for 60 % of site peak. Transformer A trips at 15 min, "
            "and losing a side curtails IT instead of failing over cleanly — the curtailment lands on "
            "user requests."
        ),
        site=SiteConfig(start_hour=13.0, cord_limit_kw=474.0, seed=8),
        events=(
            ScheduledEvent(t=15 * MINUTE, target="tx_a", action="trip", settle_s=HOUR, label="Transformer A tripped"),
        ),
        default_duration_s=90 * MINUTE,
        default_dt_s=15.0,
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

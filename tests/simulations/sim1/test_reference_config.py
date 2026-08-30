"""The sizing margins the whole scenario suite rests on.

If a stray change to ``rack_peak_kw`` or ``ups_rating_kw`` moves these, the
scenarios stop demonstrating what they claim to — ``normal`` quietly becomes an
overload run, or ``side_a_lost_at_peak`` stops overloading at all.
"""

import pytest

from app.simulations.sim1.demand import UserArrivals
from app.simulations.sim1.economics import Tariff
from app.simulations.sim1.models import SiteConfig
from app.simulations.sim1.scenarios import design_margins, get_scenario, scenario_names


def test_the_reference_build_matches_the_documented_arithmetic() -> None:
    m = design_margins(SiteConfig())

    assert m["it_nominal_kw"] == 540.0
    assert m["it_peak_kw"] == 620.0
    assert m["mech_nominal_kw"] == pytest.approx(119.0, abs=0.5)
    assert m["facility_nominal_kw"] == pytest.approx(688.0, abs=2.0)
    assert m["pue_nominal"] == pytest.approx(1.27, abs=0.02)


def test_one_side_can_carry_the_site_at_nominal_but_not_at_peak() -> None:
    """This is what makes 2N teach something instead of being decoration: the
    build sits right at the edge."""
    m = design_margins(SiteConfig())

    assert 85.0 < m["single_side_nominal_pct"] <= 95.0
    assert m["single_side_peak_pct"] > 100.0


def test_the_generator_covers_facility_nominal_with_margin_to_spare() -> None:
    m = design_margins(SiteConfig())
    assert m["generator_headroom_kw"] > 50.0


def test_the_chiller_covers_liquid_heat_at_peak_but_only_just() -> None:
    site = SiteConfig()
    liquid_heat_at_peak = site.it_peak_kw * 0.9
    assert liquid_heat_at_peak < site.chiller_capacity_kw
    assert site.chiller_capacity_kw - liquid_heat_at_peak < 60.0


def test_the_traffic_model_leaves_headroom_for_a_surge() -> None:
    """A cluster already at its throughput ceiling at the daily peak has no story
    to tell about a surge, and one at 40 % has no story about capacity."""
    m = design_margins(SiteConfig())
    assert m["interactive_racks"] == 2.0
    assert 70.0 < m["peak_utilisation_pct"] < 90.0


def test_scenarios_split_between_user_facing_and_purely_electrical() -> None:
    with_users = [name for name in scenario_names() if design_margins(get_scenario(name).site)["interactive_racks"] > 0]
    without = [name for name in scenario_names() if name not in with_users]
    assert len(with_users) >= 6
    assert without == ["load_spike", "side_a_lost_at_peak"]


def test_every_scenario_is_reachable_and_described() -> None:
    names = scenario_names()
    assert len(names) == 9
    for name in names:
        scenario = get_scenario(name)
        assert scenario.name == name
        assert len(scenario.description) > 40
        assert scenario.default_duration_s > 0.0
        assert 0.0 < scenario.default_dt_fine_s <= scenario.default_dt_s


def test_an_unknown_scenario_names_the_available_ones() -> None:
    with pytest.raises(ValueError, match="grid_outage_gen_ok"):
        get_scenario("nope")


def test_a_profile_list_must_match_the_rack_count() -> None:
    with pytest.raises(ValueError, match="expected 4 profiles"):
        SiteConfig(profiles=("training", "idle")).rack_profiles()


def test_incidents_happen_soon_enough_to_watch() -> None:
    """Nobody scrubs through twelve idle hours to reach the interesting part."""
    for name in scenario_names():
        scenario = get_scenario(name)
        if not scenario.events:
            continue
        first = min(event.t for event in scenario.events)
        assert first <= 20 * 60.0, f"{name}: first event at {first / 60:.0f} min"
        assert scenario.default_duration_s <= 3 * 3600.0, name


def test_scenarios_with_users_start_their_clock_on_the_traffic_peak() -> None:
    """An incident is only informative if the site was busy when it hit, so any
    scenario that both has users and schedules a failure starts near 14:00."""
    for name in scenario_names():
        scenario = get_scenario(name)
        site = scenario.site
        if not scenario.events or site.interactive_racks == 0:
            continue
        arrivals = UserArrivals(peak_rps=site.peak_rps, burstiness=0.0, clock_offset_s=site.start_hour * 3600.0)
        first = min(event.t for event in scenario.events)
        assert arrivals.rps(first) > 0.9 * site.peak_rps, name


def test_the_tariff_follows_the_same_clock_as_the_traffic() -> None:
    """Otherwise a run that starts at 13:00 would be billed at the night rate."""
    assert Tariff(clock_offset_s=0.0).price_at(0.0) == pytest.approx(0.09)
    assert Tariff(clock_offset_s=13 * 3600.0).price_at(0.0) == pytest.approx(0.19)

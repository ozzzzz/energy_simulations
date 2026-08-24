"""The sizing margins the whole scenario suite rests on.

If a stray change to ``rack_peak_kw`` or ``ups_rating_kw`` moves these, the
scenarios stop demonstrating what they claim to — ``normal`` quietly becomes an
overload run, or ``side_a_lost_at_peak`` stops overloading at all.
"""

import pytest

from app.simulations.sim1.scenarios import SiteConfig, design_margins, get_scenario, scenario_names


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


def test_every_scenario_is_reachable_and_described() -> None:
    names = scenario_names()
    assert len(names) == 8
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

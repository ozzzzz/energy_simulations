from app.simulations.sim1.electrical.battery import BatteryString
from app.simulations.sim1.protocols import TickContext


def _ctx(t: float = 0.0, dt: float = 1.0) -> TickContext:
    return TickContext(t=t, dt=dt, tick=int(t / dt))


def test_usable_energy_excludes_the_protected_reserve() -> None:
    battery = BatteryString(name="b", capacity_kwh=60.0, cutoff_soc=0.05)
    assert battery.usable_kwh == 57.0


def test_discharge_drains_cells_faster_than_it_delivers_to_the_bus() -> None:
    battery = BatteryString(name="b", capacity_kwh=60.0, discharge_efficiency=0.96)
    delivered = battery.discharge(_ctx(dt=3600.0), bus_kw=1.0)

    assert delivered == 1.0
    # One kWh onto the bus costs 1/0.96 kWh out of the cells.
    assert battery.discharged_kwh == 1.0 / 0.96


def test_a_full_string_carries_the_whole_site_for_about_five_minutes() -> None:
    """The sizing claim the 2N story rests on, asserted rather than asserted-in-prose."""
    battery = BatteryString(name="b", capacity_kwh=60.0)
    load_kw = 689.0
    expected_s = 57.0 * battery.discharge_efficiency * 3600.0 / load_kw

    elapsed = 0.0
    while battery.discharge(_ctx(t=elapsed), bus_kw=load_kw) > 0.0:
        elapsed += 1.0

    assert 280.0 < expected_s < 300.0
    assert abs(elapsed - expected_s) <= 2.0
    assert battery.depleted


def test_soc_never_leaves_its_bounds_under_sustained_abuse() -> None:
    battery = BatteryString(name="b", capacity_kwh=10.0)
    for i in range(500):
        battery.discharge(_ctx(t=float(i), dt=10.0), bus_kw=800.0)
        assert battery.cutoff_soc - 1e-12 <= battery.soc <= 1.0
    for i in range(500):
        battery.charge(_ctx(t=float(i), dt=10.0), bus_kw=800.0)
        assert battery.cutoff_soc <= battery.soc <= 1.0


def test_charge_is_capped_by_the_c_rate_not_only_by_headroom() -> None:
    battery = BatteryString(name="b", capacity_kwh=600.0, soc=0.5, max_charge_kw=100.0)
    taken = battery.charge(_ctx(dt=60.0), bus_kw=500.0)
    assert taken == 100.0


def test_charge_is_capped_by_remaining_headroom_when_nearly_full() -> None:
    battery = BatteryString(name="b", capacity_kwh=60.0, soc=0.999, max_charge_kw=100.0)
    taken = battery.charge(_ctx(dt=3600.0), bus_kw=100.0)
    assert taken < 100.0
    assert battery.soc == 1.0


def test_round_trip_efficiency_is_the_product_of_both_legs() -> None:
    battery = BatteryString(name="b", capacity_kwh=100.0, soc=0.5)
    stored_before = battery.soc * battery.capacity_kwh

    battery.charge(_ctx(dt=3600.0), bus_kw=10.0)
    gained = battery.soc * battery.capacity_kwh - stored_before
    assert abs(gained - 10.0 * battery.charge_efficiency) < 1e-9

    peak = battery.soc * battery.capacity_kwh
    battery.discharge(_ctx(dt=3600.0), bus_kw=5.0)
    spent = peak - battery.soc * battery.capacity_kwh
    assert abs(spent - 5.0 / battery.discharge_efficiency) < 1e-9


def test_discharge_capability_shrinks_with_the_tick_length() -> None:
    """A string with 30 s of charge left cannot supply rated power for 60 s.

    Reporting that it can is how a simulation invents autonomy it does not have.
    """
    battery = BatteryString(name="b", capacity_kwh=1.0, cutoff_soc=0.0, max_discharge_kw=800.0)
    assert battery.discharge_capability_kw(dt=1.0) == 800.0
    assert battery.discharge_capability_kw(dt=3600.0) < 1.0


def test_autonomy_is_reported_even_when_not_discharging() -> None:
    battery = BatteryString(name="b", capacity_kwh=60.0)
    assert abs(battery.autonomy_s(load_kw=689.0) - 57.0 * 0.96 * 3600.0 / 689.0) < 1e-9
    assert battery.autonomy_s(load_kw=0.0) == float("inf")

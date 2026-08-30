from app.simulations.sim1.electrical.ats import AtsSource, AtsState, AutomaticTransferSwitch
from app.simulations.sim1.models import TickContext


def _ctx(t: float, dt: float = 1.0) -> TickContext:
    return TickContext(t=t, dt=dt, tick=int(t / dt))


def test_never_transfers_to_a_generator_that_is_not_running() -> None:
    ats = AutomaticTransferSwitch(name="ats")
    for i in range(60):
        source = ats.step(_ctx(float(i)), primary_available=False, generator_running=False)
        assert source is AtsSource.PRIMARY
    assert ats.transfers == 0


def test_output_is_dead_during_the_transfer_itself() -> None:
    """The gap the UPS exists to cover: a generator that starts 'in time' still
    cannot prevent an interruption on its own."""
    ats = AutomaticTransferSwitch(name="ats", transfer_time_s=3.0)

    assert ats.step(_ctx(0.0), primary_available=False, generator_running=True) is AtsSource.NONE
    assert ats.state is AtsState.TRANSFERRING
    assert ats.probe(_ctx(0.0), primary_kw=1000.0, generator_kw=800.0) == 0.0

    assert ats.step(_ctx(1.0), primary_available=False, generator_running=True) is AtsSource.NONE
    assert ats.step(_ctx(2.0), primary_available=False, generator_running=True) is AtsSource.NONE
    assert ats.step(_ctx(3.0), primary_available=False, generator_running=True) is AtsSource.GENERATOR
    assert ats.probe(_ctx(3.0), primary_kw=1000.0, generator_kw=800.0) == 800.0


def test_retransfer_waits_for_the_utility_to_prove_itself() -> None:
    ats = AutomaticTransferSwitch(name="ats", transfer_time_s=1.0, retransfer_delay_s=30.0)
    ats.step(_ctx(0.0), primary_available=False, generator_running=True)
    ats.step(_ctx(1.0), primary_available=False, generator_running=True)
    assert ats.state is AtsState.ON_GENERATOR

    for i in range(2, 30):
        ats.step(_ctx(float(i)), primary_available=True, generator_running=True)
        assert ats.state is AtsState.ON_GENERATOR

    for t in (32.0, 33.0, 34.0):
        ats.step(_ctx(t), primary_available=True, generator_running=True)
    assert ats.state is AtsState.ON_PRIMARY


def test_a_flickering_grid_does_not_make_the_switch_chatter() -> None:
    ats = AutomaticTransferSwitch(name="ats", transfer_time_s=1.0, retransfer_delay_s=60.0)
    for i in range(200):
        # Utility present for 5 s, absent for 5 s, forever.
        present = (i // 5) % 2 == 0
        ats.step(_ctx(float(i)), primary_available=present, generator_running=True)

    assert ats.transfers <= 2, f"switch chattered {ats.transfers} times"


def test_a_dead_generator_skips_the_anti_flap_delay() -> None:
    """Waiting a minute on a source that has quit is the wrong trade."""
    ats = AutomaticTransferSwitch(name="ats", transfer_time_s=1.0, retransfer_delay_s=600.0)
    ats.step(_ctx(0.0), primary_available=False, generator_running=True)
    ats.step(_ctx(1.0), primary_available=False, generator_running=True)
    assert ats.state is AtsState.ON_GENERATOR

    ats.step(_ctx(2.0), primary_available=True, generator_running=False)
    ats.step(_ctx(3.0), primary_available=True, generator_running=False)
    assert ats.state is AtsState.ON_PRIMARY

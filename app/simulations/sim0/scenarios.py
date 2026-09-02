"""The four sim0 scenarios. Their shape lives in :mod:`models`."""

from app.simulations.sim0.inputs import DAY_SECONDS, CoolingInput
from app.simulations.sim0.models import ScenarioConfig

_SCENARIOS: dict[str, ScenarioConfig] = {
    "inference": ScenarioConfig(name="inference", profile="inference", seed=1),
    "training": ScenarioConfig(name="training", profile="training", seed=2),
    # rack 1 trains all the time, racks 2-4 serve inference all the time
    "mixed": ScenarioConfig(name="mixed", profile=["training", "inference", "inference", "inference"], seed=3),
    "cooling_failure": ScenarioConfig(
        name="cooling_failure",
        profile="training",
        seed=4,
        # a CDU/chiller incident on day 3, cutting cooling to 15% of normal for 1.5 days
        cooling=CoolingInput(
            failure_start_s=3 * DAY_SECONDS,
            failure_end_s=3 * DAY_SECONDS + 1.5 * DAY_SECONDS,
            failure_severity=0.85,
        ),
    ),
}


def get_scenario(name: str) -> ScenarioConfig:
    try:
        return _SCENARIOS[name]
    except KeyError as exc:
        raise ValueError(f"unknown scenario {name!r}, available: {sorted(_SCENARIOS)}") from exc

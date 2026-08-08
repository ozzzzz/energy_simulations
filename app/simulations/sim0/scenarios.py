from dataclasses import dataclass, field

from app.simulations.sim0.inputs import DAY_SECONDS, CoolingInput, PowerInput, WorkloadInput


@dataclass
class ScenarioConfig:
    name: str
    profile: str | list[str]
    seed: int
    power: PowerInput = field(default_factory=lambda: PowerInput(750.0))
    cooling: CoolingInput = field(default_factory=CoolingInput)  # 630 kW liquid + 70 kW air by default
    n_racks: int = 4

    def build_workloads(self) -> list[WorkloadInput]:
        # each rack gets its own RNG stream (same base shape, independent jitter)
        # so the 4 traces aren't identical copies of one signal. `profile` can be a
        # single name shared by every rack, or a list assigning a different profile
        # per rack (e.g. mixed: rack 1 trains, the rest serve inference).
        profiles = self.profile if isinstance(self.profile, list) else [self.profile] * self.n_racks
        return [WorkloadInput(profiles[i], seed=self.seed * 1000 + i) for i in range(self.n_racks)]


_SCENARIOS: dict[str, ScenarioConfig] = {
    "inference": ScenarioConfig(name="inference", profile="inference", seed=1),
    "training": ScenarioConfig(name="training", profile="training", seed=2),
    # rack 1 trains all the time, racks 2-4 serve inference all the time
    "mixed": ScenarioConfig(name="mixed", profile=["training", "inference", "inference", "inference"], seed=3),
    "cooling_failure": ScenarioConfig(
        name="cooling_failure",
        profile="training",
        seed=4,
        # a CDU/chiller incident on day 3, cutting cooling to 15% of normal for 2 hours
        cooling=CoolingInput(
            failure_start_s=3 * DAY_SECONDS,
            failure_end_s=3 * DAY_SECONDS + 2 * 3600.0,
            failure_severity=0.85,
        ),
    ),
}


def get_scenario(name: str) -> ScenarioConfig:
    try:
        return _SCENARIOS[name]
    except KeyError as exc:
        raise ValueError(f"unknown scenario {name!r}, available: {sorted(_SCENARIOS)}") from exc

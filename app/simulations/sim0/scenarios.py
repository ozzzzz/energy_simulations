from dataclasses import dataclass

from app.simulations.sim0.inputs import DAY_SECONDS, CoolingInput, PowerInput, WorkloadInput


@dataclass
class ScenarioConfig:
    name: str
    profile: str
    power: PowerInput
    cooling: CoolingInput
    n_racks: int = 4
    seed: int = 0

    def build_workloads(self) -> list[WorkloadInput]:
        # each rack gets its own RNG stream (same base shape, independent jitter)
        # so the 4 traces aren't identical copies of one signal.
        return [WorkloadInput(self.profile, seed=self.seed * 1000 + i) for i in range(self.n_racks)]


def _scenario(name: str, profile: str, seed: int, cooling: CoolingInput | None = None) -> ScenarioConfig:
    # CoolingInput() defaults to 630 kW liquid + 70 kW air (700 kW total, ~90/10 split).
    return ScenarioConfig(
        name=name, profile=profile, power=PowerInput(750.0), cooling=cooling or CoolingInput(), seed=seed
    )


_SCENARIOS: dict[str, ScenarioConfig] = {
    "inference": _scenario("inference", "inference", seed=1),
    "training": _scenario("training", "training", seed=2),
    "mixed": _scenario("mixed", "mixed", seed=3),
    "cooling_failure": _scenario(
        "cooling_failure",
        "training",
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

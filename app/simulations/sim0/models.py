"""Every shape sim0 passes around, and nothing that acts on one.

Same rule as :mod:`app.simulations.sim1.models`: the data types live in one
place so the engine, the rack and the report all name the same structure, and
this module imports nothing from the package except :mod:`inputs`, which is
where the input *signals* a scenario is built from are defined.
"""

from dataclasses import dataclass, field
from enum import StrEnum

from app.simulations.sim0.inputs import CoolingInput, PowerInput, WorkloadInput


class RackState(StrEnum):
    IDLE = "idle"
    RUNNING = "running"
    THROTTLING = "throttling"
    EMERGENCY_SHUTDOWN = "emergency_shutdown"
    RECOVERING = "recovering"


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

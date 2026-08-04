from dataclasses import dataclass, field
from enum import StrEnum


class RackState(StrEnum):
    IDLE = "idle"
    RUNNING = "running"
    THROTTLING = "throttling"
    EMERGENCY_SHUTDOWN = "emergency_shutdown"
    RECOVERING = "recovering"


@dataclass
class Rack:
    """GB300 NVL72 rack. nominal_kw/peak_kw/throttle_temp_c/liquid_heat_fraction are
    published specs (132 kW TDP, ~155 kW peak EDPp, 85°C junction throttle, ~90% of
    heat captured by liquid cold plates vs ~10% by air on OSFP/storage/PDB); shutdown_temp_c,
    recovery_temp_c, thermal_mass_kws_per_c and throttle_ratio have no public
    source and are our own placeholders for the simplified thermal model."""

    name: str
    nominal_kw: float = 132.0
    peak_kw: float = 155.0
    ambient_c: float = 25.0
    throttle_temp_c: float = 85.0
    shutdown_temp_c: float = 95.0
    recovery_temp_c: float = 70.0
    thermal_mass_kws_per_c: float = 900.0
    throttle_ratio: float = 0.6
    liquid_heat_fraction: float = 0.9

    temp_c: float = field(init=False)
    state: RackState = field(init=False)
    energy_kwh: float = field(init=False, default=0.0)

    def __post_init__(self) -> None:
        self.temp_c = self.ambient_c
        self.state = RackState.IDLE

    def step(self, dt: float, demand_kw: float, power_scale: float, cooling_available_kw: float) -> dict:
        requested_kw = demand_kw * power_scale
        down_states = (RackState.EMERGENCY_SHUTDOWN, RackState.RECOVERING)

        if self.state in down_states and self.temp_c > self.recovery_temp_c:
            # hard down: zero draw until temp_c falls to recovery_temp_c, no reclassification
            # in between — otherwise a momentary dip below shutdown_temp_c (but still above
            # recovery_temp_c) got misread as "cool enough", let the rack draw power again,
            # and reheat straight back into shutdown in an endless throttle/shutdown flap.
            consumed_kw = 0.0
            dtemp = (consumed_kw - cooling_available_kw) / self.thermal_mass_kws_per_c * dt
            self.temp_c = max(self.ambient_c, self.temp_c + dtemp)
            self.state = RackState.EMERGENCY_SHUTDOWN if self.temp_c >= self.shutdown_temp_c else RackState.RECOVERING
        else:
            if self.state in down_states:
                self.state = RackState.RUNNING  # cooled below recovery_temp_c, resume drawing power

            # the rack can never draw more than its own rated peak, throttling or not
            limit_kw = self.peak_kw * self.throttle_ratio if self.state is RackState.THROTTLING else self.peak_kw
            consumed_kw = min(requested_kw, limit_kw)

            dtemp = (consumed_kw - cooling_available_kw) / self.thermal_mass_kws_per_c * dt
            self.temp_c = max(self.ambient_c, self.temp_c + dtemp)

            if self.temp_c >= self.shutdown_temp_c:
                self.state = RackState.EMERGENCY_SHUTDOWN
                consumed_kw = 0.0
            elif self.temp_c >= self.throttle_temp_c:
                self.state = RackState.THROTTLING
            elif consumed_kw > 0:
                self.state = RackState.RUNNING
            else:
                self.state = RackState.IDLE

        self.energy_kwh += consumed_kw * dt / 3600.0

        return {
            "rack": self.name,
            "state": self.state.value,
            "demand_kw": demand_kw,
            "consumed_kw": consumed_kw,
            "temp_c": self.temp_c,
        }

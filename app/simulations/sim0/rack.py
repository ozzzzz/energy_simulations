from dataclasses import dataclass, field

from app.simulations.sim0.models import RackState


@dataclass
class Rack:
    """GB300 NVL72 rack. nominal_kw/peak_kw/throttle_temp_c/liquid_capture_rate are
    published specs — nominal_kw is TDP (Thermal Design Power), peak_kw is EDPp
    (Electrical Design Power, peak), throttle_temp_c is the GPU junction throttle
    point (85°C), liquid_capture_rate is the fraction of rack heat captured by
    liquid cold plates vs air (~90/10 for GB300 NVL72's direct-liquid-cooled
    GPU/CPU/NVSwitch vs air-cooled OSFP/storage/PDB). shutdown_temp_c,
    recovery_temp_c, thermal_mass_kws_per_c, throttle_ratio and psu_efficiency
    have no public source and are our own placeholders for the simplified model
    (psu_efficiency ~97% is a typical high-efficiency PSU/VRM figure, not a
    GB300-specific spec).

    consumed_kw is the rack's total electrical draw from the PDU (what peak_kw/
    EDPp actually limits, and what fully turns into heat regardless of where the
    loss happens). psu_efficiency only splits that same draw for reporting: it_kw
    is the useful compute power that reaches the GPUs, loss_kw is PSU/VRM
    conversion loss — it does NOT change the thermal calc, which already uses
    total draw as total heat."""

    name: str
    nominal_kw: float = 132.0
    peak_kw: float = 155.0
    ambient_c: float = 25.0
    throttle_temp_c: float = 85.0
    shutdown_temp_c: float = 95.0
    recovery_temp_c: float = 70.0
    thermal_mass_kws_per_c: float = 900.0
    throttle_ratio: float = 0.6
    liquid_capture_rate: float = 0.9
    psu_efficiency: float = 0.97

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
            "it_kw": consumed_kw * self.psu_efficiency,
            "loss_kw": consumed_kw * (1.0 - self.psu_efficiency),
            "temp_c": self.temp_c,
        }

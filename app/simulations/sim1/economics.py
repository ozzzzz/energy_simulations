"""Money. Four separate lines, because they answer four different questions."""

from dataclasses import dataclass, field

from app.simulations.sim1.units import DAY_SECONDS, SECONDS_PER_MINUTE


@dataclass
class Tariff:
    """Flat or time-of-use electricity price."""

    off_peak_usd_per_kwh: float = 0.09
    peak_usd_per_kwh: float = 0.19
    peak_start_hour: float = 8.0
    peak_end_hour: float = 20.0
    clock_offset_s: float = 0.0
    """Wall-clock time that ``t = 0`` corresponds to, so tariff windows line up
    with the traffic curve rather than with the start of the run."""

    def price_at(self, t: float) -> float:
        hour = ((t + self.clock_offset_s) % DAY_SECONDS) / 3600.0
        on_peak = self.peak_start_hour <= hour < self.peak_end_hour
        return self.peak_usd_per_kwh if on_peak else self.off_peak_usd_per_kwh


@dataclass
class Economics:
    tariff: Tariff = field(default_factory=Tariff)
    diesel_usd_per_l: float = 1.10

    unserved_compute_usd_per_kwh: float = 3.00
    """Opportunity cost of curtailed GPU-hours. Continuous, and the number that
    actually matters at this scale: a throttled rack costs money every second."""

    downtime_usd_per_min: float = 500.0
    """Charged only while *every* rack is down — the SLA-breach step function.
    The widely quoted $9k/min is a hyperscale figure and would be absurd for
    four racks."""

    def energy_usd(self, t: float, grid_kwh: float) -> float:
        return grid_kwh * self.tariff.price_at(t)

    def diesel_usd(self, litres: float) -> float:
        return litres * self.diesel_usd_per_l

    def unserved_usd(self, unserved_kwh: float) -> float:
        return unserved_kwh * self.unserved_compute_usd_per_kwh

    def downtime_usd(self, down_seconds: float) -> float:
        return down_seconds / SECONDS_PER_MINUTE * self.downtime_usd_per_min

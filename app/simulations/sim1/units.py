"""Unit conventions and the few numeric helpers every sim1 module needs.

Power and heat are kW, energy is kWh, temperature is °C, time is seconds.

sim1 runs on a *non-uniform* timebase: the engine refines dt inside event
windows, so a run mixes 60 s and 1 s ticks. Every rate must therefore be
integrated with the tick's own dt — ``mean * count`` is always wrong here and
``sum(rate * dt)`` is always right. That is why :func:`kwh` takes dt explicitly
rather than assuming a fixed step.
"""

import math

SECONDS_PER_HOUR = 3600.0
SECONDS_PER_MINUTE = 60.0
DAY_SECONDS = 24 * SECONDS_PER_HOUR

# Water: 4.18 kJ per litre per K, i.e. kW*s per litre per °C.
WATER_KWS_PER_L_C = 4.18
# Air at ~20 °C: 1.2 kg/m3 * 1.005 kJ/kg/K.
AIR_KWS_PER_M3_C = 1.206


def kwh(kw: float, dt_s: float) -> float:
    """Energy in kWh from a power held constant over ``dt_s`` seconds."""
    return kw * dt_s / SECONDS_PER_HOUR


def kw_from_kwh(energy_kwh: float, dt_s: float) -> float:
    """Average power in kW needed to move ``energy_kwh`` over ``dt_s`` seconds."""
    if dt_s <= 0.0:
        return 0.0
    return energy_kwh * SECONDS_PER_HOUR / dt_s


def clamp(value: float, low: float, high: float) -> float:
    return low if value < low else high if value > high else value


def safe_ratio(numerator: float, denominator: float, default: float = 0.0) -> float:
    """``numerator / denominator``, or ``default`` when the denominator vanishes."""
    if denominator == 0.0:
        return default
    return numerator / denominator


def finite_or_none(value: float) -> float | None:
    """Map inf/nan to ``None``.

    ``json.dumps`` happily emits bare ``Infinity`` and ``NaN``, which
    ``JSON.parse`` rejects — the visualization would load blank. Non-finite
    values are legitimate here (battery autonomy with zero discharge, PUE with
    zero IT load), so they are normalised at the boundary instead of avoided.
    """
    return value if math.isfinite(value) else None

# Energy Simulations

Discrete-event simulations of AI data center energy systems (SimPy), with a CLI to run scenarios and a Dash/Plotly viewer.

## Simulations

### Sim-0 — rack-centric ([docs/sim_0_plan.md](docs/sim_0_plan.md))

4 GPU racks as the modeled core; power/cooling/workload are input signals. Cooling is split into
liquid (direct-to-chip, handles most of the load) and air (residual). Default run length is one
week at a 1-minute tick, long enough to show the daily peak (users active) / trough (asleep) cycle.

Scenarios (`--scenario`):

| Scenario | Profile |
|---|---|
| `inference` | user-driven traffic: short request spikes, follows the day/night cycle |
| `training` | scheduled batch jobs: sustained near-peak draw with rare dips (checkpoint/sync), runs flat around the clock |
| `mixed` | rack 1 trains, racks 2-4 serve inference, all at once |
| `cooling_failure` | training load + a scripted CDU/chiller incident on day 3 (cooling cut to 15% for 2h) — exercises throttle/shutdown/recovery |

```bash
uv sync

uv run app sim0-run --scenario inference --output /tmp/inference.csv
uv run app sim0-run --scenario training --output /tmp/training.csv
uv run app sim0-run --scenario mixed --output /tmp/mixed.csv
uv run app sim0-run --scenario cooling_failure --output /tmp/cooling_failure.csv

uv run app sim0-dashboard --scenario inference
uv run app sim0-dashboard --scenario training
uv run app sim0-dashboard --scenario mixed
uv run app sim0-dashboard --scenario cooling_failure
```

Add `--duration <seconds>` / `--dt <seconds>` to override the default week / 1-minute tick.

There's also an input/output-focused dashboard (`sim0-dashboard-io`): same data, laid out so it's unmistakable
which lines the rack doesn't control (inputs — dashed: workload demand, power/cooling actually available)
vs what the rack does in response (outputs — solid: draw, IT load/PSU loss split, temperature, cooling used).

```bash
uv run app sim0-dashboard-io --scenario cooling_failure
```

### Terminology

Field/unit names follow standard data center and GPU industry usage, not invented shorthand:

| Term | Meaning |
|---|---|
| kW / kWh | Real power / energy — SI units used throughout (no kVA/power-factor modeling) |
| °C | Temperature — Celsius, standard in DC thermal specs (ASHRAE) |
| `nominal_kw` | TDP (Thermal Design Power) — sustained rated draw |
| `peak_kw` | EDPp (Electrical Design Power, peak) — burst draw a circuit must be provisioned for |
| `throttle_temp_c` | GPU junction throttle threshold (Tj) — vendor-published thermal limit before throttling |
| `liquid_capture_rate` | Liquid capture rate — fraction of rack heat removed by direct liquid cooling (DLC) vs air, a standard DLC data center metric |
| `consumed_kw` | The rack's total electrical draw from the PDU (what `peak_kw`/EDPp actually limits) — fully becomes heat regardless of conversion loss |
| `psu_efficiency` / `it_kw` / `loss_kw` | PSU/VRM efficiency splits that same draw into useful IT load (`it_kw`) vs conversion loss (`loss_kw`), for reporting only — both still count as heat |
| `delivery_efficiency_pct` | `it_kw ÷ consumed_kw` — a partial, rack-level PUE-style figure (PSU/VRM loss only, not full facility PUE) |
| `uptime_pct` | Availability — DC industry usually expresses this in "nines" (99.9%, 99.99%) or Uptime Institute Tier ratings; we report a raw percentage |
| PUE | Power Usage Effectiveness (Total Facility Power ÷ IT Power) — the standard DC efficiency KPI; `delivery_efficiency_pct` covers the rack's own PSU/VRM loss but not the full picture, since cooling equipment's own electrical draw and upstream UPS/transformer loss still aren't tracked |

## Development

Install deps and pre-commit hooks:

```bash
uv sync
uv run pre-commit install
```

Run tests:

```bash
uv run pytest
uv run pytest --cov=app --cov-report=term-missing
```

Lint and format:

```bash
uv run ruff check .
uv run ruff format .
```

Type check:

```bash
uv run pyright
```

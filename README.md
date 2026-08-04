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
| `mixed` | idle → training → inference over the run |
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

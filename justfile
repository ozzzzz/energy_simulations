# Run `just` with no arguments to list recipes.

set positional-arguments

default:
    @just --list

# --- simulations -------------------------------------------------------------

# List sim1 scenarios and the reference design margins.
scenarios:
    uv run app sim1-list

# Run a sim1 scenario: KPIs to stdout, artifacts to out/sim1/<scenario>/.
sim1 scenario="normal" *args:
    uv run app sim1-run --scenario {{scenario}} {{args}}

# Run a sim1 scenario and open the single-file visualization.
viz scenario="grid_outage_gen_ok" *args:
    uv run app sim1-viz --scenario {{scenario}} --open {{args}}

# Run every sim1 scenario and compare their headline KPIs.
compare *args:
    uv run app sim1-compare {{args}}

# Run every sim1 scenario in full and write all artifacts.
sim1-all:
    #!/usr/bin/env bash
    set -euo pipefail
    # Names come from the scenario registry, not from parsing `sim1-list` output.
    for scenario in $(uv run python -c 'from app.simulations.sim1.scenarios import scenario_names; print(*scenario_names())'); do
        echo "==> $scenario"
        uv run app sim1-run --scenario "$scenario" --no-open
    done

# Run a sim0 scenario (rack-centric model).
sim0 scenario="inference" *args:
    uv run app sim0-run --scenario {{scenario}} {{args}}

# Serve the sim0 Dash viewer on :8050.
sim0-dashboard scenario="inference" *args:
    uv run app sim0-dashboard --scenario {{scenario}} {{args}}

# Delete generated run artifacts.
clean:
    rm -rf out/

# --- development -------------------------------------------------------------

install:
    uv sync
    uv run pre-commit install

test *args:
    uv run pytest {{args}}

cov:
    uv run pytest --cov=app --cov-report=term-missing

fmt:
    uv run ruff format app tests
    uv run ruff check --fix app tests

lint:
    uv run pre-commit run --all-files

types:
    uvx pyright

# Everything CI runs.
check: lint cov

serve *args:
    uv run app serve {{args}}

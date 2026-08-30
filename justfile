# Run `just` with no arguments to list recipes.

default:
    @just --list

# Install dependencies and the pre-commit hooks.
install:
    uv sync
    uv run pre-commit install

# Run the test suite.
test:
    uv run pytest

# Every sim-1 scenario. KPIs, graphs and CSVs to out/sim1/<scenario>/.
sim1-all:
    uv run app sim1-run --scenario normal --no-open
    uv run app sim1-run --scenario grid_outage_gen_ok --no-open
    uv run app sim1-run --scenario grid_outage_gen_fail --no-open
    uv run app sim1-run --scenario user_surge --no-open
    uv run app sim1-run --scenario load_spike --no-open
    uv run app sim1-run --scenario cooling_failure --no-open
    uv run app sim1-run --scenario side_a_lost --no-open
    uv run app sim1-run --scenario side_a_lost_at_peak --no-open
    uv run app sim1-run --scenario undersized_cords --no-open

# A 90 s 1080p MP4 of every sim-1 scenario, into out/sim1/<scenario>/<scenario>.mp4.
sim1-videos:
    uv run app sim1-video --scenario normal
    uv run app sim1-video --scenario grid_outage_gen_ok
    uv run app sim1-video --scenario grid_outage_gen_fail
    uv run app sim1-video --scenario user_surge
    uv run app sim1-video --scenario load_spike
    uv run app sim1-video --scenario cooling_failure
    uv run app sim1-video --scenario side_a_lost
    uv run app sim1-video --scenario side_a_lost_at_peak
    uv run app sim1-video --scenario undersized_cords

# Every sim-0 scenario. Graphs and CSVs to out/sim0/.
sim0-all:
    uv run app sim0-run --scenario inference --output out/sim0/inference.csv --html out/sim0/inference.html
    uv run app sim0-run --scenario training --output out/sim0/training.csv --html out/sim0/training.html
    uv run app sim0-run --scenario mixed --output out/sim0/mixed.csv --html out/sim0/mixed.html
    uv run app sim0-run --scenario cooling_failure --output out/sim0/cooling_failure.csv --html out/sim0/cooling_failure.html

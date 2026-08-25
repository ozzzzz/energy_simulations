"""'Self-contained' as a machine-checkable property, with no browser involved."""

import re
from importlib.resources import files

import pytest

from app.simulations.sim1.engine import run_scenario
from app.simulations.sim1.scenarios import get_scenario
from app.simulations.sim1.viz.builder import build_html, write_html

EXTERNAL_TAG = re.compile(r"<(?:script|link|img|iframe)[^>]*(?:src|href)\s*=\s*[\"']?(?:https?:)?//", re.I)


@pytest.fixture(scope="module")
def html() -> str:
    name = "cooling_failure"
    result = run_scenario(name, duration_s=7200.0)
    return build_html(result, get_scenario(name), target_points=300)


def test_no_placeholder_survives_the_build(html: str) -> None:
    for placeholder in ("__SIM_CSS__", "__SIM_DATA__", "__SIM_JS__"):
        assert placeholder not in html


def test_nothing_is_loaded_from_outside_the_document(html: str) -> None:
    match = EXTERNAL_TAG.search(html)
    assert match is None, f"external reference: {match.group(0)}"
    for token in ("@import", "fetch(", "XMLHttpRequest", "WebSocket", "importScripts"):
        assert token not in html, token


def test_the_page_carries_its_own_styles_scripts_and_data(html: str) -> None:
    assert "<style>" in html and "--side-a" in html
    assert 'id="sim-data" type="application/json"' in html
    assert '"scenario":"cooling_failure"' in html
    # All four script modules landed, in order.
    for marker in ("const SIM = ", "function drawSeries", "const DIAGRAM = ", "KPI_TILES"):
        assert marker in html, marker
    assert html.index("const SIM = ") < html.index("const DIAGRAM = ")


def test_the_payload_sits_inside_a_json_script_block(html: str) -> None:
    """Parsed as data, never as JavaScript, so no value can execute."""
    head, _, tail = html.partition('<script id="sim-data" type="application/json">')
    body, _, _ = tail.partition("</script>")
    assert body.startswith("{")
    assert "sim-data" not in head.split("<body>")[-1].replace('id="sim-data"', "")


def test_writing_creates_parent_directories(tmp_path) -> None:
    name = "normal"
    result = run_scenario(name, duration_s=600.0)
    path = write_html(result, get_scenario(name), tmp_path / "deep" / "nested" / "index.html", target_points=100)
    assert path.exists()
    assert path.read_text().startswith("<!doctype html>")


def test_the_page_carries_what_the_incident_countdown_needs() -> None:
    name = "cooling_failure"
    result = run_scenario(name, duration_s=3600.0)
    html = build_html(result, get_scenario(name), target_points=200)

    # The wall-clock hand t=0 corresponds to, so times read as 13:15 not 00:15.
    assert '"start_hour":13' in html.replace(" ", "")
    # The readout slot, the countdown logic, and the labelled incidents.
    assert 'id="readout-incident"' in html
    assert "nearestIncident" in html
    assert "Chiller tripped" in html


def test_the_gauge_cannot_land_on_a_node_title() -> None:
    """The SoC bar was positioned relative to the node's *bottom* edge, which on
    the short battery node put it straight through the title text.

    The layout is computed by the browser, so this asserts the two source
    invariants rather than the rendered result: the gauge is anchored below the
    title, and the battery node is tall enough for title, gauge and value.
    """
    js = (files("app.simulations.sim1.viz") / "js" / "20-diagram.js").read_text(encoding="utf-8")

    assert "const gaugeY = spec.y + 23;" in js, "gauge is anchored to the bottom edge again"
    assert "spec.y + spec.h - 34" not in js

    # Title baseline 16, gauge 23-28, value baseline h-7. With h=48 the value
    # glyphs start at 30, clearing the gauge; at h=40 they would overlap it.
    match = re.search(r"id: `batt_\$\{side\}`,.*?h: (\d+),", js, re.S)
    assert match is not None, "battery node not found"
    height = int(match.group(1))
    assert height - 7 - 11 >= 28, f"battery node h={height} puts the value line on the gauge"


def test_state_text_is_only_created_for_nodes_that_have_a_state() -> None:
    """An empty text element is invisible but occupies the gauge's row, so
    creating one unconditionally left a collision waiting to happen."""
    js = (files("app.simulations.sim1.viz") / "js" / "20-diagram.js").read_text(encoding="utf-8")
    assert "if (spec.state || spec.rackState) {" in js

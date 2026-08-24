"""'Self-contained' as a machine-checkable property, with no browser involved."""

import re

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

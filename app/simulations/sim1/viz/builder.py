"""Assemble the single self-contained HTML file.

The template, stylesheet and scripts are real files in the package rather than
Python string literals. That buys syntax highlighting, formatting and linting for
the CSS and JS, readable diffs — and, decisively, avoids escaping every ``{`` and
``${`` in several hundred lines of JavaScript to get it past ``str.format``.

The scripts are concatenated in filename order, which is what keeps this from
becoming one unmaintainable blob: each numbered file has a single job and none
exceeds a couple of hundred lines.

The template on its own is a valid page that renders a "no data" state, so the
layout can be iterated on by opening ``template.html`` directly.
"""

from importlib.resources import files
from pathlib import Path
from typing import Any

from app.simulations.sim1.models import RunResult, ScenarioConfig
from app.simulations.sim1.viz.payload import build_payload, dumps

CSS_PLACEHOLDER = "/*__SIM_CSS__*/"
DATA_PLACEHOLDER = "/*__SIM_DATA__*/"
JS_PLACEHOLDER = "/*__SIM_JS__*/"

_PACKAGE = "app.simulations.sim1.viz"


def _read(*parts: str) -> str:
    resource = files(_PACKAGE)
    for part in parts:
        resource = resource.joinpath(part)
    return resource.read_text(encoding="utf-8")


def _scripts() -> str:
    directory = files(_PACKAGE).joinpath("js")
    names = sorted(entry.name for entry in directory.iterdir() if entry.name.endswith(".js"))
    return "\n".join(_read("js", name) for name in names)


def build_html(result: RunResult, scenario: ScenarioConfig, target_points: int = 2500) -> str:
    payload = build_payload(result, scenario, target_points=target_points)
    return render(payload)


def render(payload: dict[str, Any]) -> str:
    html = _read("template.html")
    html = html.replace(CSS_PLACEHOLDER, _read("styles.css"))
    html = html.replace(JS_PLACEHOLDER, _scripts())
    # Data last: the payload is arbitrary text and must not be scanned for the
    # other placeholders.
    return html.replace(DATA_PLACEHOLDER, dumps(payload))


def write_html(
    result: RunResult,
    scenario: ScenarioConfig,
    path: str | Path,
    target_points: int = 2500,
) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(build_html(result, scenario, target_points=target_points), encoding="utf-8")
    return destination

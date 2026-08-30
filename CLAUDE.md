# Conventions

## Imports

All imports go at the top of the file. Never import inside a function, method or an
`if TYPE_CHECKING` block — no lazy imports, no deferred imports, no "import it here to
keep startup fast" imports. If a module is expensive to import, that is a fact about
the module, not a reason to hide the import inside a call.

## Simulation layout

Every simulation package (`app/simulations/sim0/`, `sim1/`, and any future `simN/`)
keeps its **data types in one `models.py`** and its behaviour in the module that owns
that behaviour:

- `models.py` holds the shapes — frozen dataclasses, enums, config and result records,
  the things that are passed around: a tick's context, what a component returns, a
  scenario's configuration, a run's result, an output spec.
- Behaviour modules (`engine.py`, `rack.py`, `electrical/*`, `cooling/*`, `report.py`,
  …) import those types from `models.py`. They never redefine a structure locally, and
  they never define a near-duplicate of one that already exists there.
- `models.py` may import the package's `units.py` and the standard library, **nothing
  else in the package**. That rule is what keeps it free of import cycles and is the
  test for whether something belongs in it: if a type needs a rack, a feed or a running
  facility to be useful, it is behaviour, not a model.
- A contract between components is written as a docstring on the module that owns it,
  not as a `Protocol` class nothing imports.

When adding a field, a record or a config knob, put it in `models.py` first and import
it — do not start a second definition somewhere convenient.

## Keep it small

- No abstraction without a second caller. No `Protocol`, ABC or base class written for
  documentation's sake — a docstring says the same thing and cannot go stale unnoticed.
- Delete code nothing calls: unused fields, counters incremented but never read, helpers
  with no caller, viewers with no entry point. `uvx vulture app tests --min-confidence 60`
  finds most of them; check each hit before removing it, since tests count as callers.
- A dependency that only one dead module needed goes with it (`uv remove <pkg>`).

## Tooling

- Python through `uv`: `uv sync`, `uv add <pkg>`, `uv run <cmd>`. Never raw pip/venv.
- `just test` runs the suite. Lint and format only the files touched:
  `uv run ruff check --fix <file>`, `uv run ruff format <file>` — never the repo root.

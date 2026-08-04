# Energy Simulations

FastAPI service, uv, Docker, GitHub Actions.

## Getting Started

```bash
cp .env.example .env
uv sync
```

Run the API server:

```bash
uv run app serve
# or with options:
uv run app serve --port 8080 --reload
```

Generate OpenAPI schema:

```bash
uv run app generate-openapi
# outputs to docs/openapi.json
```

## Project Structure

```
app/
├── config.py          # pydantic-settings, APP_ env var prefix
├── server.py          # FastAPI app, CORS, lifespan
├── cli.py             # Typer CLI entry point
├── v1/
│   ├── router.py      # /api/v1 prefix
│   └── routes/
│       └── health.py  # GET /api/v1/health
tests/
.env.example
Dockerfile
docker-compose.yml
.pre-commit-config.yaml
pyrightconfig.json
```

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `APP_LOG_LEVEL` | `INFO` | Logging level |
| `APP_DEBUG` | `false` | FastAPI debug mode |
| `APP_ALLOWED_ORIGINS` | `*` | CORS origins, semicolon-separated |

## Docker

```bash
docker compose up --build
```

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

# python_template

Minimal Python service template with FastAPI, Telegram bot, uv, Docker, and GitHub Actions.

## Stack

- **[uv](https://docs.astral.sh/uv/)** — package manager
- **FastAPI** — HTTP API (`/api/v1/`)
- **pydantic-settings** — config from env vars
- **Typer** — CLI (`serve`, `bot`, `generate-openapi`)
- **python-telegram-bot** — Telegram bot
- **Ruff** — linter + formatter
- **Pyright** — type checker
- **Docker** — containerization
- **GitHub Actions** — CI (lint, test, docker build)

## Using as a Template

### On GitHub

Go to the repo → **Settings** → enable **"Template repository"**.

Then: **Use this template** → **Create a new repository**.

### With gh CLI

```bash
gh repo create my-new-service --template ozzzzz/python_template --private --clone
cd my-new-service
```

## Rename the Project

After cloning, run the rename script (pure stdlib, no deps needed):

```bash
# Rename app/ → my_service/, update all imports and config
uv run python rename.py my_service

# Also rename env prefix: APP_ → MY_SERVICE_
uv run python rename.py my_service --env-prefix MY_SERVICE

# Preview without changing anything
uv run python rename.py my_service --dry-run
```

## Getting Started

```bash
cp .env.example .env   # fill in APP_TELEGRAM_TOKEN at minimum
uv sync
```

Run the API server:

```bash
uv run app serve
# or with options:
uv run app serve --port 8080 --reload
```

Run the Telegram bot:

```bash
uv run app bot
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
└── bot/
    └── bot.py         # Telegram bot (polling)
tests/
.env.example
Dockerfile
docker-compose.yml
.pre-commit-config.yaml
pyrightconfig.json
rename.py
```

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `APP_LOG_LEVEL` | `INFO` | Logging level |
| `APP_DEBUG` | `false` | FastAPI debug mode |
| `APP_ALLOWED_ORIGINS` | `*` | CORS origins, semicolon-separated |
| `APP_TELEGRAM_TOKEN` | — | Telegram bot token (required) |

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

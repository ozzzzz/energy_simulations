import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Config
from app.server import create_app


@pytest.fixture
def config():
    return Config(log_level="DEBUG", debug=True, allowed_origins="*")


@pytest.fixture
def app(config):
    return create_app(config)


@pytest.mark.asyncio
async def test_root(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_health(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"

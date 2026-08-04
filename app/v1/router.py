from fastapi import APIRouter

from app.v1.routes.health import router as health_router

router = APIRouter(prefix="/api/v1")

router.include_router(health_router)

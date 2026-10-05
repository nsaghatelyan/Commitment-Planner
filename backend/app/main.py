from fastapi import FastAPI

from app.api.analysis import router as analysis_router
from app.api.connections import router as connections_router
from app.api.health import router as health_router
from app.api.tenants import router as tenants_router


def create_app() -> FastAPI:
    app = FastAPI(title="Savings Tool API", version="0.1.0")
    app.include_router(health_router)
    app.include_router(analysis_router)
    app.include_router(tenants_router)
    app.include_router(connections_router)
    return app


app = create_app()

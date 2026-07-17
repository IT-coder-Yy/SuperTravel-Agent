from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


DEFAULT_CORS_ORIGINS = [
    "http://localhost:8080",
    "http://127.0.0.1:8080",
]


def create_fastapi_app(lifespan: Any) -> FastAPI:
    """Create FastAPI application with stable metadata and CORS defaults."""
    app = FastAPI(
        title="Sage Multi-Agent Framework",
        description="现代化多智能体协作框架API",
        version="0.8",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=DEFAULT_CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    return app

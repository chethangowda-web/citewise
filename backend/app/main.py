from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.admin import router as admin_router
from app.api.auth_routes import router as auth_router
from app.api.routes import router
from app.config import get_settings
from app.db import init_db
from app.observability import RequestContextMiddleware, setup_logging

setup_logging()


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="Policy RAG Assistant", version="0.2.0", lifespan=lifespan)
app.add_middleware(RequestContextMiddleware)
app.add_middleware(  # added last so it is outermost and also answers preflights
    CORSMiddleware,
    allow_origins=[o.strip() for o in get_settings().cors_origins.split(",")],
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID", "Retry-After"],
)
app.include_router(router, prefix="/api")
app.include_router(admin_router, prefix="/api")
app.include_router(auth_router, prefix="/api")

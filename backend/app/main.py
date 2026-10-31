from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.admin import router as admin_router
from app.api.admin_ops import router as admin_ops_router
from app.api.admin_users import router as admin_users_router
from app.api.feedback import router as feedback_router
from app.api.auth_routes import router as auth_router
from app.api.routes import router
from app.config import get_settings
from app.db import init_db
from app.observability import RequestContextMiddleware, setup_logging
from app.retention import start_retention_thread

setup_logging()


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    stop = start_retention_thread()  # None unless RETENTION_DAYS or AUDIT_RETENTION_DAYS is set
    yield
    if stop:
        stop.set()


app = FastAPI(title="Citewise", version="0.2.0", lifespan=lifespan)
app.add_middleware(RequestContextMiddleware)
app.add_middleware(  # added last so it is outermost and also answers preflights
    CORSMiddleware,
    allow_origins=[o.strip() for o in get_settings().cors_origins.split(",")],
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID", "Retry-After"],
)
app.include_router(router, prefix="/api")
app.include_router(admin_router, prefix="/api")
app.include_router(admin_users_router, prefix="/api")
app.include_router(admin_ops_router, prefix="/api")
app.include_router(feedback_router, prefix="/api")
app.include_router(auth_router, prefix="/api")

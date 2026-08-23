from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, ORJSONResponse

from app import __version__
from app.logging_config import configure_logging
from app.repository_provider import close_repository, repository_healthcheck
from app.routes_internal import router as internal_router
from app.routes_internal import status_router
from app.routes_public import router as public_router
from app.settings import get_settings

configure_logging()
settings = get_settings()
bot_policy_html = (
    Path(__file__).resolve().parent.parent / "web" / "bot.html"
).read_text(encoding="utf-8")

if settings.service_mode in {"api", "all"} and not settings.internal_token:
    raise RuntimeError(
        "INTERNAL_TOKEN must be set when SERVICE_MODE is 'api' or 'all': "
        "the public service mounts authenticated operational status routes."
    )


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    close_repository()


app = FastAPI(
    title="Sa3arly Catalog and Price Engine",
    version=__version__,
    default_response_class=ORJSONResponse,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origin_list,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Internal-Token"],
)

if settings.service_mode in {"api", "all"}:
    app.include_router(public_router)
    app.include_router(status_router)
if settings.service_mode in {"worker", "all"}:
    app.include_router(internal_router)
    if settings.service_mode == "worker":
        app.include_router(status_router)


@app.get("/healthz", include_in_schema=False)
def healthz():
    return {"ok": True, "service_mode": settings.service_mode, "version": __version__}


@app.get("/readyz", include_in_schema=False)
def readyz():
    return {"ok": repository_healthcheck()}


@app.get("/bot", response_class=HTMLResponse, include_in_schema=False)
def bot_policy():
    return HTMLResponse(content=bot_policy_html)

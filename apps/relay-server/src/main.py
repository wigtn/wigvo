import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from src.call_manager import call_manager
from src.config import settings
from src.logging_config import setup_logging
from src.middleware.rate_limit import RateLimitMiddleware
from src.routes.calls import router as calls_router
from src.routes.health import router as health_router
from src.routes.inbound_dispatch import router as inbound_dispatch_router
from src.routes.stream import router as stream_router
from src.routes.twilio_webhook import router as twilio_router

STATIC_DIR = Path(__file__).parent.parent / "static"

setup_logging(
    log_level=settings.log_level,
    log_dir=settings.log_dir,
    max_bytes=settings.log_max_bytes,
    backup_count=settings.log_backup_count,
)
logger = logging.getLogger("wigvo-relay")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(
        "WIGVO Relay Server starting on %s:%s",
        settings.relay_server_host,
        settings.relay_server_port,
    )
    from src.inbound.service import dispatch_service

    await dispatch_service.start()
    try:
        yield
    finally:
        await dispatch_service.stop()
        # Graceful shutdown: 모든 활성 통화 정리
        await call_manager.shutdown_all()


app = FastAPI(
    title="WIGVO Relay Server",
    version="3.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(RateLimitMiddleware, calls_per_minute=60)

app.include_router(health_router)
app.include_router(calls_router, prefix="/relay")
app.include_router(inbound_dispatch_router, prefix="/relay")
app.include_router(stream_router, prefix="/relay")
app.include_router(twilio_router, prefix="/twilio")


@app.get("/test")
async def test_page():
    """Web test console page."""
    return FileResponse(STATIC_DIR / "test.html")


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

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
from src.observability.operations import operations
from src.middleware.rate_limit import RateLimitMiddleware
from src.routes.calls import router as calls_router
from src.routes.health import router as health_router
from src.routes.inbound_dispatch import router as inbound_dispatch_router
from src.routes.loadtest import router as loadtest_router
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
    from src.inbound.media import (
        install_inbound_media_handlers,
        shutdown_inbound_media,
    )
    from src.inbound.service import dispatch_service

    operations.start()
    install_inbound_media_handlers()
    await dispatch_service.start()
    if settings.load_test_mode:
        from src.observability.loop_lag import sampler

        sampler.start()
        logger.warning("LOAD TEST MODE enabled — OpenAI/Twilio calls are stubbed")
    try:
        yield
    finally:
        if settings.load_test_mode:
            from src.observability.loop_lag import sampler

            await sampler.stop()
        await dispatch_service.stop()
        await shutdown_inbound_media()
        # Graceful shutdown: 모든 활성 통화 정리
        await call_manager.shutdown_all()
        await operations.stop()


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

# 부하테스트: 단일 하니스 IP가 다수 통화를 몰아 생성하면 IP당 분당 리밋에
# 먼저 걸려 이벤트루프 포화 측정이 오염된다. 부하모드에서만 사실상 해제(프로덕션은 60/분 유지).
app.add_middleware(
    RateLimitMiddleware,
    calls_per_minute=1_000_000 if settings.load_test_mode else 60,
)

app.include_router(health_router)
app.include_router(calls_router, prefix="/relay")
app.include_router(inbound_dispatch_router, prefix="/relay")
app.include_router(stream_router, prefix="/relay")
app.include_router(twilio_router, prefix="/twilio")
app.include_router(loadtest_router, prefix="/loadtest")


@app.get("/test")
async def test_page():
    """Web test console page."""
    return FileResponse(STATIC_DIR / "test.html")


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

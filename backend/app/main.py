"""FastAPI application (ADR-0001). Run: `uvicorn app.main:app --reload` from backend/."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.admin_documents import router as admin_documents_router
from app.api.admin_logs import router as admin_logs_router
from app.api.admin_ops import router as admin_ops_router
from app.api.admin_review import router as admin_review_router
from app.api.applicability import router as applicability_router
from app.api.documents import router as documents_router
from app.api.questions import router as questions_router
from app.api.search import router as search_router
from app.core.config import get_settings
from app.core.logging import RequestIdMiddleware, configure_logging
from app.ops.alerts import alert_loop
from app.ops.health import health_public
from app.ops.state import STATE

settings = get_settings()
configure_logging(settings)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Build/load the BM25 index and alias tables once so the first /search stays under budget;
    start the alert evaluator (M15)."""
    from app.db.session import get_sessionmaker
    from app.retrieval.retriever import RetrievalService

    svc = RetrievalService()
    try:
        with get_sessionmaker()() as session:
            svc.ensure(session)
    except Exception:  # pragma: no cover - database may be absent in some environments
        logger.exception("retrieval service could not be initialised at startup")
    app.state.retrieval = svc
    app.state.ops = STATE
    task = None
    if settings.alerts_enabled:
        task = asyncio.create_task(alert_loop(settings, svc))
    logger.info(
        "startup", extra={"version": settings.app_version, "alerts": settings.alerts_enabled}
    )
    try:
        yield
    finally:
        if task is not None:
            task.cancel()
        svc.graph.close()


app = FastAPI(
    lifespan=lifespan,
    title="QCVN Chatbot API",
    version=settings.app_version,
    description="Tra cứu quy chuẩn kỹ thuật môi trường và các Thông tư liên quan — "
    "citation-first, abstention-first.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["X-Request-Id", "X-Query-Id"],
)
app.add_middleware(RequestIdMiddleware)
app.include_router(documents_router)
app.include_router(applicability_router)
app.include_router(search_router)
app.include_router(questions_router)
app.include_router(admin_ops_router)
app.include_router(admin_logs_router)
app.include_router(admin_review_router)
app.include_router(admin_documents_router)


@app.get("/api/v1/health")
def health() -> dict[str, object]:
    from app.db.session import get_sessionmaker

    try:
        with get_sessionmaker()() as session:
            return health_public(session, getattr(app.state, "retrieval", None), get_settings())
    except Exception:  # pragma: no cover - environment dependent
        return {
            "status": "degraded",
            "database": False,
            "index_active": None,
            "bm25_loaded": False,
            "version": settings.app_version,
        }

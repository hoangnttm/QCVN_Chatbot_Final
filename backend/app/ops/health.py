"""Health checks (M15). `health_public` is cheap (one `select 1`, in-memory index state) and safe
for load balancers; `health_details` adds Neo4j / Gemini probes on request."""

from __future__ import annotations

import time
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db import models as m
from app.ops.state import STATE


def _db_check(session: Session) -> tuple[bool, int]:
    t0 = time.perf_counter()
    try:
        session.execute(text("select 1")).scalar_one()
        return True, int((time.perf_counter() - t0) * 1000)
    except Exception:  # pragma: no cover - environment dependent
        return False, int((time.perf_counter() - t0) * 1000)


def _index_state(retrieval: Any) -> dict[str, Any]:
    bm25 = getattr(retrieval, "bm25", None)
    metas = getattr(bm25, "metas", None)
    return {
        "active_label": getattr(retrieval, "index_label", None),
        "bm25_loaded": bm25 is not None,
        "bm25_docs": len(metas) if metas is not None else None,
    }


def health_public(session: Session, retrieval: Any, settings: Settings) -> dict[str, Any]:
    db_ok, _ = _db_check(session)
    idx = _index_state(retrieval)
    ok = db_ok and bool(idx["active_label"]) and idx["bm25_loaded"]
    return {
        "status": "ok" if ok else "degraded",
        "database": db_ok,
        "index_active": idx["active_label"],
        "bm25_loaded": idx["bm25_loaded"],
        "version": settings.app_version,
    }


def _neo4j_probe(settings: Settings) -> dict[str, Any]:
    try:
        from neo4j import GraphDatabase

        with GraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_user, settings.neo4j_password),
            connection_timeout=2.0,
        ) as d:
            d.verify_connectivity()
        return {"ok": True}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:200]}"}


def _gemini_probe(settings: Settings) -> dict[str, Any]:
    if not settings.gemini_api_key:
        return {"ok": False, "error": "GEMINI_API_KEY not set"}
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(
            api_key=settings.gemini_api_key,
            http_options=types.HttpOptions(timeout=10_000),
        )
        client.models.count_tokens(model=settings.gemini_answer_model, contents="ping")
        return {"ok": True}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:200]}"}


def health_details(
    session: Session, retrieval: Any, settings: Settings, probe: bool = False
) -> dict[str, Any]:
    db_ok, db_ms = _db_check(session)
    idx = _index_state(retrieval)
    snap = STATE.snapshot()
    gemini: dict[str, Any] = {
        "configured": bool(settings.gemini_api_key),
        "model": settings.gemini_answer_model,
        "last_success_at": snap["llm_last_success_at"],
        "last_error_at": snap["llm_last_error_at"],
        "last_error": snap["llm_last_error"],
        "consecutive_errors": snap["llm_consecutive_errors"],
        "calls": snap["llm_calls"],
        "errors": snap["llm_errors"],
    }
    if probe and settings.health_gemini_probe:
        gemini["probe"] = _gemini_probe(settings)
    neo4j: dict[str, Any] = {"ok": None, "skipped": True}
    if probe:
        graph = getattr(retrieval, "graph", None)
        if graph is None:
            neo4j = _neo4j_probe(settings)
        else:  # projection freshness + GDS, the conditions PPR / rule 8 run under
            st = graph.status(force=True)
            reason = st.usable_for(idx["active_label"], need_gds=True)
            neo4j = {
                "ok": st.ok,
                "error": st.error or reason,
                "gds_version": st.gds_version,
                "projection_version": st.projection_version,
                "index_label": st.index_label,
                "vectors": st.vectors,
                "usable": reason is None,
            }
    alerts_active = 0
    if db_ok:
        alerts_active = int(
            session.execute(
                select(func.count()).where(m.AlertEvent.resolved_at.is_(None))
            ).scalar_one()
        )
    ok = db_ok and bool(idx["active_label"]) and idx["bm25_loaded"]
    return {
        "status": "ok" if ok else "degraded",
        "version": settings.app_version,
        "uptime_s": snap["uptime_s"],
        "checks": {
            "database": {"ok": db_ok, "latency_ms": db_ms},
            "index": {**idx, "ok": bool(idx["active_label"]) and idx["bm25_loaded"]},
            "neo4j": neo4j,
            "gemini": gemini,
        },
        "alerts_active": alerts_active,
    }

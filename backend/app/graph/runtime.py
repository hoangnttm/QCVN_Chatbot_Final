"""Process-wide access to the Neo4j projection at request time (plan 2026-09-29 §1).

The graph is optional: every caller asks `usable(active_label)` first and falls back to the
graph-free behaviour when Neo4j is unreachable, GDS is missing (PPR only) or the projection was
built for a different index_version than the one active in PostgreSQL (stale graph).
"""

from __future__ import annotations

import threading
import time
from typing import Any

from pydantic import BaseModel
from sqlalchemy.engine import make_url

from app.core.config import Settings
from app.graph import vectors

STATUS_TTL_S = 30.0


class GraphStatus(BaseModel):
    enabled: bool
    ok: bool = False
    error: str | None = None
    gds_version: str | None = None
    index_label: str | None = None  # the index_version the projection was built from
    projection_version: str | None = None
    built_at: str | None = None
    vectors: int | None = None  # chunk embeddings stored on the nodes (app.graph.vectors)
    # PostgreSQL database the projection was built from / the one this process reads (schema v1.4)
    source_database: str | None = None
    current_database: str | None = None
    server_version: tuple[int, int] | None = None
    checked_at: float = 0.0

    def usable_for(
        self, active_label: str | None, need_gds: bool = False, need_vectors: bool = False
    ) -> str | None:
        """None when usable, else the reason (goes into SearchResult.warnings)."""
        if not self.enabled:
            return "graph disabled (GRAPH_BACKEND=off)"
        if not self.ok:
            return f"graph unavailable: {self.error or 'unknown error'}"[:160]
        if self.projection_version is None:
            return "graph projection missing (run scripts/build_graph.py)"
        if active_label is not None and self.index_label != active_label:
            return (
                f"graph projection stale (built for {self.index_label}, "
                f"active {active_label}) — run scripts/build_graph.py"
            )
        if self.source_database and self.current_database != self.source_database:
            # two loads of the corpus share chunk ids and the index label: only the source tells
            # them apart
            return (
                f"graph projection built from another database ({self.source_database}, "
                f"current {self.current_database}) — set NEO4J_DATABASE or run scripts/build_graph.py"
            )
        if need_gds and not self.gds_version:
            return "Neo4j Graph Data Science plugin not installed"
        if need_vectors and not self.vectors:
            return "graph projection has no chunk embeddings (run scripts/build_graph.py)"
        return None


class GraphRuntime:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._driver: Any = None
        self._status: GraphStatus | None = None
        self._lock = threading.Lock()

    @property
    def database(self) -> str:
        return self.settings.neo4j_database

    @property
    def timeout(self) -> float:
        return self.settings.neo4j_timeout_s

    def driver(self) -> Any:
        if self._driver is None:
            with self._lock:
                if self._driver is None:
                    from neo4j import GraphDatabase

                    self._driver = GraphDatabase.driver(
                        self.settings.neo4j_uri,
                        auth=(self.settings.neo4j_user, self.settings.neo4j_password),
                        # connecting is one-off (pooled); "localhost" may try ::1 first and wait
                        connection_timeout=max(5.0, self.timeout),
                        connection_acquisition_timeout=max(5.0, self.timeout),
                        max_connection_pool_size=10,
                        notifications_min_severity="OFF",
                    )
        return self._driver

    def run(self, cypher: str, timeout: float | None = None, **params: Any) -> list[dict[str, Any]]:
        """One read query with a server-side timeout; raises on any driver error."""
        from neo4j import Query

        with self.driver().session(database=self.database) as s:
            return s.run(Query(cypher, timeout=timeout or self.timeout), **params).data()

    def status(self, force: bool = False) -> GraphStatus:
        if self.settings.graph_backend != "neo4j":
            return GraphStatus(enabled=False)
        cur = self._status
        if cur is not None and not force and time.monotonic() - cur.checked_at < STATUS_TTL_S:
            return cur
        st = GraphStatus(enabled=True, checked_at=time.monotonic())
        try:
            meta = self.run(
                "OPTIONAL MATCH (m:Meta {id: 'projection'}) RETURN m.index_label AS index_label, "
                "m.projection_version AS projection_version, toString(m.built_at) AS built_at, "
                "m.vectors AS vectors, m.source_database AS source_database"
            )
            if meta:
                st.index_label = meta[0]["index_label"]
                st.projection_version = meta[0]["projection_version"]
                st.built_at = meta[0]["built_at"]
                st.vectors = meta[0]["vectors"]
                st.source_database = meta[0]["source_database"]
                st.current_database = make_url(self.settings.database_url).database
            st.server_version = vectors.server_version(self.run)
            try:
                st.gds_version = self.run("RETURN gds.version() AS v")[0]["v"]
            except Exception:  # noqa: BLE001 - procedure unknown: GDS not installed
                st.gds_version = None
            st.ok = True
        except Exception as e:  # noqa: BLE001
            st.error = f"{type(e).__name__}: {str(e)[:160]}"
        self._status = st
        return st

    def mark_failed(self, error: Exception) -> None:
        """A query failed mid-request: report the graph as down until the next status check."""
        self._status = GraphStatus(
            enabled=self.settings.graph_backend == "neo4j",
            ok=False,
            error=f"{type(error).__name__}: {str(error)[:160]}",
            checked_at=time.monotonic(),
        )

    def close(self) -> None:
        if self._driver is not None:
            self._driver.close()
            self._driver = None

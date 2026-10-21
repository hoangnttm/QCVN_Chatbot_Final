"""`GraphStore` — the one interface the engine / retrieval use for graph questions (ADR-0002 §4).

Two implementations: PostgreSQL (structure_nodes parent chain, always available) and Neo4j
(projection built by `app.graph.build_graph`). Both answer the same questions: ancestors of a
node, the structural path behind an evidence chunk, and neighbours of a node.
"""

from __future__ import annotations

from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models as m


class GraphStore(Protocol):
    def ancestors(self, node_id: str) -> list[dict[str, Any]]: ...

    def path_for_chunk(self, chunk_id: str) -> list[dict[str, Any]]: ...


def _node_dict(n: m.StructureNode) -> dict[str, Any]:
    return {
        "id": n.node_id,
        "kind": n.kind,
        "number": n.number,
        "title": n.title or n.term,
        "citation_label": n.citation_label,
        "printed_page": n.printed_page,
    }


class PostgresGraphStore:
    def __init__(self, session: Session) -> None:
        self.s = session

    def ancestors(self, node_id: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        cur = self.s.get(m.StructureNode, node_id)
        while cur is not None:
            if cur.kind != "Boilerplate":
                out.append(_node_dict(cur))
            cur = self.s.get(m.StructureNode, cur.parent_id) if cur.parent_id else None
        return list(reversed(out))

    def path_for_chunk(self, chunk_id: str) -> list[dict[str, Any]]:
        c = self.s.get(m.EvidenceChunk, chunk_id)
        if c is None:
            return []
        if c.chunk_type == "table_row":
            row = self.s.get(m.TableRow, c.source_id)
            if row is None:
                return []
            table = self.s.get(m.TableDef, row.table_code)
            chain = self.ancestors(table.node_id) if table else []
            chain.append(
                {
                    "id": row.row_id,
                    "kind": "TableRow",
                    "number": row.row_no,
                    "title": row.cells.get("parameter")
                    or row.cells.get("item")
                    or row.cells.get("sector"),
                    "citation_label": row.citation_label,
                    "printed_page": row.printed_page,
                }
            )
            return chain
        if c.chunk_type == "registry_fact":
            doc = self.s.get(m.Document, c.document_id)
            return [
                {
                    "id": c.document_id,
                    "kind": "Document",
                    "title": doc.document_code if doc else None,
                },
                {"id": c.chunk_id, "kind": "RegistryFact", "citation_label": c.citation_label},
            ]
        return self.ancestors(c.source_id)

    def neighbors(self, node_id: str) -> dict[str, Any]:
        n = self.s.get(m.StructureNode, node_id)
        if n is None:
            return {}
        children = self.s.execute(
            select(m.StructureNode)
            .where(m.StructureNode.parent_id == node_id)
            .order_by(m.StructureNode.order)
        ).scalars()
        chunks = self.s.execute(
            select(m.EvidenceChunk.chunk_id, m.EvidenceChunk.citation_label).where(
                m.EvidenceChunk.source_id == node_id
            )
        ).all()
        return {
            "node": _node_dict(n),
            "parent": _node_dict(self.s.get(m.StructureNode, n.parent_id)) if n.parent_id else None,
            "children": [_node_dict(c) for c in children],
            "evidence": [{"chunk_id": a, "citation_label": b} for a, b in chunks],
        }


class Neo4jGraphStore:
    """Same questions answered from the Neo4j projection (labels/edges in app.graph.build_graph)."""

    def __init__(self, driver, database: str = "neo4j") -> None:
        self.driver = driver
        self.database = database

    def ancestors(self, node_id: str) -> list[dict[str, Any]]:
        q = (
            "MATCH (n:Structure {id: $id}) OPTIONAL MATCH p = (root)-[:CONTAINS*]->(n) "
            "WITH n, p ORDER BY length(p) DESC LIMIT 1 "
            "RETURN [x IN coalesce(nodes(p), [n]) WHERE NOT x:LegalDocument "
            "AND coalesce(x.kind, '') <> 'Boilerplate' | {id: x.id, kind: x.kind, number: x.number, "
            "title: x.title, citation_label: x.citation_label, printed_page: x.printed_page}] AS chain"
        )
        with self.driver.session(database=self.database) as s:
            rec = s.run(q, id=node_id).single()
            return list(rec["chain"]) if rec else []

    def path_for_chunk(self, chunk_id: str) -> list[dict[str, Any]]:
        q = (
            "MATCH (c:EvidenceChunk {id: $id})-[:EXTRACTED_FROM]->(src) "
            "OPTIONAL MATCH p = (root)-[:CONTAINS*]->(src) "
            "WITH src, p ORDER BY length(p) DESC LIMIT 1 "
            "RETURN [x IN coalesce(nodes(p), [src]) WHERE NOT x:LegalDocument "
            "AND coalesce(x.kind, '') <> 'Boilerplate' | {id: x.id, kind: coalesce(x.kind, labels(x)[0]), "
            "number: x.number, title: coalesce(x.title, x.parameter), citation_label: x.citation_label, "
            "printed_page: x.printed_page}] AS chain"
        )
        with self.driver.session(database=self.database) as s:
            rec = s.run(q, id=chunk_id).single()
            return list(rec["chain"]) if rec else []

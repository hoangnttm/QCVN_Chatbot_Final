"""Invariants of the projection against its source (schema v1.4).

The graph is derived from PostgreSQL; these checks say whether a given Neo4j database still is
the projection of a given PostgreSQL database: the fact store is there in full, every statement
points to the chunk it is cited from, zones are scoped by their column scheme, conditions match
`rule_conditions`. Read-only on both sides.
"""

from __future__ import annotations

import json
from typing import Any

from neo4j import GraphDatabase
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db import models as m
from app.graph.build_graph import FACT_NODE_LABELS, SCHEMA_VERSION, source_revision
from app.ontology.loader import load_dictionaries


def _pairs(result: Any) -> dict[Any, Any]:
    return {r[0]: r[1] for r in result}


def _row(name: str, expected: Any, actual: Any) -> dict[str, Any]:
    return {"check": name, "expected": expected, "actual": actual, "ok": expected == actual}


def verify(session: Session, settings: Settings, database: str | None = None) -> list[dict]:
    database = database or settings.neo4j_database
    d = load_dictionaries(settings=settings)
    linked = {x.name for x in d.condition_dimensions if x.linked_entity}
    out: list[dict[str, Any]] = []

    by_type = _pairs(
        session.execute(select(m.Fact.fact_type, func.count()).group_by(m.Fact.fact_type))
    )
    node_types = _pairs(
        session.execute(
            select(m.Fact.fact_type, func.count())
            .where(m.Fact.ref_table.in_(FACT_NODE_LABELS))
            .group_by(m.Fact.fact_type)
        )
    )
    edge_types = {
        k: v - node_types.get(k, 0) for k, v in by_type.items() if v > node_types.get(k, 0)
    }
    rules = {
        r.rule_code: r
        for r in session.execute(
            select(
                m.LimitRule.rule_code,
                m.LimitRule.document_id,
                m.LimitRule.zone,
                m.LimitRule.zone_scheme,
                m.LimitRule.column_key,
                m.LimitRule.cell_raw,
                m.LimitRule.trust,
                m.LimitRule.valid_from,
            )
        )
    }
    cond = _pairs(
        session.execute(
            select(m.RuleCondition.dimension, func.count()).group_by(m.RuleCondition.dimension)
        )
    )
    documents = set(session.execute(select(m.Document.document_id)).scalars())
    embedded = session.execute(
        select(func.count()).where(m.EvidenceChunk.embedding.is_not(None))
    ).scalar_one()
    revision = source_revision(session)

    with (
        GraphDatabase.driver(
            settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
        ) as driver,
        driver.session(database=database) as s,
    ):

        def one(cypher: str, **params: Any) -> Any:
            return s.run(cypher, **params).single()[0]

        def table(cypher: str) -> dict[str, int]:
            return {r[0]: r[1] for r in s.run(cypher)}

        meta = s.run("MATCH (m:Meta {id: 'projection'}) RETURN properties(m) AS p").single()
        meta = dict(meta["p"]) if meta else {}
        out.append(_row("schema version", SCHEMA_VERSION, meta.get("schema_version")))
        out.append(
            _row(
                "built from this PostgreSQL database",
                [session.get_bind().url.database, revision],
                [meta.get("source_database"), meta.get("source_revision")],
            )
        )
        out.append(
            _row(
                "statements on nodes, by type",
                dict(sorted(node_types.items())),
                dict(sorted(table("MATCH (n:Fact) RETURN n.fact_kind, count(*)").items())),
            )
        )
        out.append(
            _row(
                "statements on edges, by type",
                dict(sorted(edge_types.items())),
                dict(
                    sorted(
                        table(
                            "MATCH ()-[e]->() WHERE e.fact_ids IS NOT NULL "
                            "RETURN e.fact_kind, sum(size(e.fact_ids))"
                        ).items()
                    )
                ),
            )
        )
        out.append(
            _row(
                "statements in total",
                sum(by_type.values()),
                one("MATCH (n:Fact) RETURN count(n)")
                + one(
                    "MATCH ()-[e]->() WHERE e.fact_ids IS NOT NULL "
                    "RETURN coalesce(sum(size(e.fact_ids)), 0)"
                ),
            )
        )
        out.append(
            _row(
                "Fact nodes without exactly one FACT_STATED_IN to their chunk",
                0,
                one(
                    "MATCH (n:Fact) OPTIONAL MATCH (n)-[:FACT_STATED_IN]->(c:EvidenceChunk) "
                    "WITH n, collect(c.id) AS cs WHERE size(cs) <> 1 OR cs[0] <> n.fact_chunk_id "
                    "RETURN count(n)"
                ),
            )
        )
        out.append(
            _row(
                "Fact nodes or edges without a legal address",
                0,
                one(
                    "MATCH (n:Fact) WHERE n.address IS NULL OR n.address = '[]' "
                    "OR n.address_label IS NULL RETURN count(n)"
                )
                + one(
                    "MATCH ()-[e]->() WHERE e.fact_ids IS NOT NULL "
                    "AND (e.address IS NULL OR e.address = '[]') RETURN count(e)"
                ),
            )
        )
        graph_rules = {
            r["id"]: r
            for r in s.run(
                "MATCH (r:LimitRule) OPTIONAL MATCH (r)-[:IN_ZONE]->(z:Zone) "
                "RETURN r.id AS id, r.document_id AS doc, r.column_key AS column_key, "
                "r.cell_raw AS cell_raw, r.trust AS trust, r.valid_from AS valid_from, "
                "r.address AS address, r.zone AS zone, r.zone_scheme AS scheme, "
                "collect(z.scheme + '|' + z.code) AS zones"
            )
        }
        out.append(_row("LimitRule nodes", len(rules), len(graph_rules)))
        wrong_cell = [
            code
            for code, r in rules.items()
            if code not in graph_rules
            or (graph_rules[code]["column_key"], graph_rules[code]["cell_raw"])
            != (r.column_key, r.cell_raw)
        ]
        out.append(_row("LimitRule whose cell differs from limit_rules", 0, len(wrong_cell)))
        wrong_trust = [
            code
            for code, r in rules.items()
            if code in graph_rules
            and (graph_rules[code]["trust"], graph_rules[code]["valid_from"])
            != (r.trust, r.valid_from.isoformat() if r.valid_from else None)
        ]
        out.append(
            _row("LimitRule whose trust / validity differs from limit_rules", 0, len(wrong_trust))
        )
        addresses = {(g["doc"], g["address"]) for g in graph_rules.values()}
        out.append(
            _row("cell addresses, unique within a document", len(graph_rules), len(addresses))
        )
        wrong_zone = [
            code
            for code, r in rules.items()
            if code in graph_rules
            and graph_rules[code]["zones"] != ([f"{r.zone_scheme}|{r.zone}"] if r.zone else [])
        ]
        out.append(_row("LimitRule whose IN_ZONE is not its own column scheme", 0, len(wrong_zone)))
        schemes = [r[0] for r in s.run("MATCH (z:Zone) RETURN DISTINCT z.scheme")]
        out.append(
            _row(
                "Zone nodes whose scheme is not a document",
                [],
                sorted(x for x in schemes if x not in documents),
            )
        )
        out.append(
            _row(
                "conditions as Condition nodes (rule_conditions on unlinked dimensions)",
                sum(v for k, v in cond.items() if k not in linked),
                one("MATCH (:LimitRule)-[e:APPLIES_WHEN]->(:Condition) RETURN count(e)"),
            )
        )
        out.append(
            _row(
                "Condition edges by dimension",
                dict(sorted((k, v) for k, v in cond.items() if k not in linked)),
                dict(
                    sorted(
                        table(
                            "MATCH (:LimitRule)-[e:APPLIES_WHEN]->(c:Condition) "
                            "RETURN c.dimension, count(e)"
                        ).items()
                    )
                ),
            )
        )
        out.append(
            _row(
                "zone conditions as IN_ZONE edges",
                cond.get("zone", 0),
                one("MATCH (:LimitRule)-[e:IN_ZONE]->(:Zone) RETURN count(e)"),
            )
        )
        out.append(
            _row(
                "vectors on EvidenceChunk",
                embedded,
                one("MATCH (c:EvidenceChunk) WHERE c.embedding IS NOT NULL RETURN count(c)"),
            )
        )
        out.append(
            _row(
                "nodes with a database sequence in their id",
                0,
                one("MATCH (n) WHERE n.id =~ '(fact|ver):[0-9]+' RETURN count(n)"),
            )
        )
    return out


def render(rows: list[dict[str, Any]]) -> str:
    lines = []
    for r in rows:
        mark = "OK  " if r["ok"] else "FAIL"
        lines.append(f"[{mark}] {r['check']}: {json.dumps(r['actual'], ensure_ascii=False)}")
        if not r["ok"]:
            lines.append(f"       expected {json.dumps(r['expected'], ensure_ascii=False)}")
    return "\n".join(lines)

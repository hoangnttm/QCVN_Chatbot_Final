"""Chunk embeddings inside the Neo4j projection: `EvidenceChunk.embedding` + a native vector index.

Vector search is part of the Neo4j kernel (no plugin): the embedding is a LIST<FLOAT> node property
written with `db.create.setNodeVectorProperty` and indexed by `CREATE VECTOR INDEX` (HNSW, cosine).
Servers from 2026.01 on answer with the `SEARCH` clause and filter by domain inside the index;
older 5.x servers (the compose image) use `db.index.vector.queryNodes` and filter afterwards.
"""

from __future__ import annotations

from typing import Any

INDEX_NAME = "evidence_embedding"
LABEL = "EvidenceChunk"
PROPERTY = "embedding"
FILTER_PROPERTY = "domain"
SEARCH_CLAUSE_SINCE = (2026, 1)  # SEARCH + filter properties in the index (`WITH [n.domain]`)
MIN_NEIGHBOURS = (
    100  # HNSW explores as many candidates as it is asked for: ask for ≥ this, cut to k
)
LEGACY_NEIGHBOURS = 1000  # queryNodes cannot filter: fetch this many, then keep the wanted domains
BATCH = 100


def server_version(run: Any) -> tuple[int, int]:
    """`run(cypher, **params) -> list[dict]`; (2026, 5) for 2026.05.0, (5, 26) for 5.26.x."""
    rows = run(
        "CALL dbms.components() YIELD name, versions WHERE name = 'Neo4j Kernel' "
        "RETURN versions[0] AS v"
    )
    major, minor, *_ = (rows[0]["v"].split("-")[0].split(".") + ["0"])[:3]
    return int(major), int(minor)


def supports_search_clause(version: tuple[int, int]) -> bool:
    return version >= SEARCH_CLAUSE_SINCE


def ensure_index(run: Any, dim: int, version: tuple[int, int]) -> str:
    """Create the cosine index for `dim` dimensions; an index of another size is replaced.
    Quantization is off: ~10³ chunks fit in memory and the ranking must match the stored vectors."""
    cur = run(
        "SHOW VECTOR INDEXES YIELD name, options WHERE name = $name RETURN options", name=INDEX_NAME
    )
    if cur and cur[0]["options"]["indexConfig"].get("vector.dimensions") != dim:
        run(f"DROP INDEX {INDEX_NAME}")
        cur = []
    if not cur:
        with_filter = f"WITH [n.{FILTER_PROPERTY}] " if supports_search_clause(version) else ""
        run(
            f"CREATE VECTOR INDEX {INDEX_NAME} IF NOT EXISTS FOR (n:{LABEL}) ON n.{PROPERTY} "
            f"{with_filter}OPTIONS {{indexConfig: {{`vector.dimensions`: $dim, "
            "`vector.similarity_function`: 'cosine', `vector.quantization.enabled`: false}}",
            dim=dim,
        )
    run("CALL db.awaitIndexes(120)")
    return INDEX_NAME


def write_vectors(run: Any, vectors: dict[str, list[float]]) -> int:
    """Set `embedding` on the chunk nodes; chunks without a vector lose a stale one."""
    ids = list(vectors)
    for i in range(0, len(ids), BATCH):
        run(
            f"UNWIND $rows AS r MATCH (n:{LABEL} {{id: r.id}}) "
            f"CALL db.create.setNodeVectorProperty(n, '{PROPERTY}', r.v)",
            rows=[{"id": c, "v": vectors[c]} for c in ids[i : i + BATCH]],
        )
    run(
        f"MATCH (n:{LABEL}) WHERE n.{PROPERTY} IS NOT NULL AND NOT n.id IN $ids "
        f"REMOVE n.{PROPERTY}, n.embedding_model",
        ids=ids,
    )
    return len(ids)


def search(
    run: Any,
    vector: list[float],
    k: int,
    domains: set[str] | None,
    version: tuple[int, int],
) -> list[tuple[str, float]]:
    """Top-k chunk ids with their cosine similarity (Neo4j scores cosine as (1 + cos) / 2)."""
    n = max(k, MIN_NEIGHBOURS)
    if supports_search_clause(version):
        # the in-index filter takes one exact value per property → one search per domain
        if domains:
            q = (
                f"UNWIND $domains AS d CALL (d) {{ MATCH (n:{LABEL}) SEARCH n IN "
                f"(VECTOR INDEX {INDEX_NAME} FOR $v WHERE n.{FILTER_PROPERTY} = d LIMIT $n) "
                "SCORE AS score RETURN n.id AS id, score } "
                "RETURN id, score ORDER BY score DESC, id LIMIT $k"
            )
            rows = run(q, v=vector, k=k, n=n, domains=sorted(domains))
        else:
            q = (
                f"MATCH (n:{LABEL}) SEARCH n IN (VECTOR INDEX {INDEX_NAME} FOR $v LIMIT $n) "
                "SCORE AS score RETURN n.id AS id, score ORDER BY score DESC, id LIMIT $k"
            )
            rows = run(q, v=vector, k=k, n=n)
    else:
        q = (
            f"CALL db.index.vector.queryNodes('{INDEX_NAME}', $n, $v) YIELD node, score "
            f"WHERE $domains IS NULL OR node.{FILTER_PROPERTY} IN $domains "
            "RETURN node.id AS id, score ORDER BY score DESC, id LIMIT $k"
        )
        rows = run(
            q,
            v=vector,
            k=k,
            n=max(n, LEGACY_NEIGHBOURS) if domains else n,
            domains=sorted(domains) if domains else None,
        )
    return [(r["id"], 2.0 * float(r["score"]) - 1.0) for r in rows]

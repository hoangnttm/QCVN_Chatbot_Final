"""Neo4j projection: idempotent rebuild and the same answers as the PostgreSQL GraphStore.
Runs in a separate database `qcvntest` (the projection is rebuilt from the qcvn_test data, so it
must never touch the dev database). Skipped when Neo4j is unreachable or cannot host a second
database (Community edition)."""

import pytest
from neo4j import GraphDatabase

from app.db import models as m
from app.db.loader import load_document
from app.db.session import get_sessionmaker
from app.graph.build_graph import build, graph_counts
from app.graph.store import Neo4jGraphStore, PostgresGraphStore
from app.ontology.loader import load_dictionaries
from app.registry.loader import load_registry

TEST_DB = "qcvntest"


@pytest.fixture(scope="module")
def neo4j_ready(db_settings):
    try:
        with GraphDatabase.driver(
            db_settings.neo4j_uri, auth=(db_settings.neo4j_user, db_settings.neo4j_password)
        ) as d:
            d.verify_connectivity()
            with d.session(database="system") as s:
                s.run(f"CREATE DATABASE {TEST_DB} IF NOT EXISTS WAIT").consume()
    except Exception as e:  # pragma: no cover - environment dependent
        pytest.skip(f"Neo4j test database unavailable: {e}")
    reg = load_registry(settings=db_settings)
    maker = get_sessionmaker(db_settings)
    with maker.begin() as session:
        for doc_id in ("TT_06_2025", "QCVN_40_2025"):
            load_document(doc_id, reg, db_settings, session)
    return db_settings


def test_graph_projection_is_idempotent(neo4j_ready):
    settings = neo4j_ready
    maker = get_sessionmaker(settings)
    with maker() as session:
        first = build(session, settings, TEST_DB)
        second = build(session, settings, TEST_DB)
    assert first["nodes"] == second["nodes"] and first["edges"] == second["edges"]
    counts = graph_counts(settings, TEST_DB)
    with maker() as session:  # whatever earlier test modules loaded into qcvn_test
        n_rules = session.query(m.LimitRule).count()
        n_rows = session.query(m.TableRow).count()
    assert n_rules >= 213 and n_rows >= 199  # at least TT06 + QCVN 40
    assert counts["labels"]["LimitRule"] == n_rules and counts["labels"]["TableRow"] == n_rows
    assert counts["relationships"]["DEFINED_BY_TABLE_ROW"] == n_rules
    # schema v1.2: one evidence edge type, conditions from a closed vocabulary, methods on rows
    assert not {"SOURCED_FROM", "SUPPORTED_BY"} & set(counts["relationships"])
    assert counts["relationships"]["SPECIFIES_METHOD"] > 0
    # schema v1.3: prose is tied to the semantic layer (QCVN 40 + TT 06 are loaded)
    for rel in ("MENTIONS", "REFERS_TO", "STATED_IN", "HAS_PROVISION", "TABLE_CONTEXT"):
        assert counts["relationships"].get(rel, 0) > 0, rel
    assert counts["relationships"]["EFFECTIVE_UNDER"] >= 12  # 2 limit tables x 6 clauses
    assert counts["relationships"]["REPLACES"] >= 11 and counts["labels"]["ApplicabilityRule"] >= 24

    with (
        GraphDatabase.driver(
            settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
        ) as driver,
        maker() as session,
    ):
        pg = PostgresGraphStore(session).path_for_chunk("chunk:QCVN40_2025.B2.r4.2")
        neo = Neo4jGraphStore(driver, TEST_DB).path_for_chunk("chunk:QCVN40_2025.B2.r4.2")
        assert [x["id"] for x in pg] == [x["id"] for x in neo]
        assert pg[-1]["kind"] == "TableRow" and pg[0]["kind"] == "Document"
        with driver.session(database=TEST_DB) as s:
            assert (
                s.run(
                    "MATCH ()-[r]->() WHERE r.method IS NULL OR r.source IS NULL RETURN count(r) AS n"
                ).single()["n"]
                == 0
            )
            roles = s.run("MATCH ()-[r:HAS_EVIDENCE]->() RETURN collect(DISTINCT r.role) AS r")
            assert set(roles.single()["r"]) >= {"limit", "document", "definition", "support"}
            types = s.run("MATCH (c:Condition) RETURN collect(DISTINCT c.type) AS t").single()["t"]
            declared = {
                x.name for x in load_dictionaries().condition_dimensions if not x.linked_entity
            }
            assert set(types) <= declared  # dimensions are data (condition_dimensions.yaml)
            assert (
                s.run(
                    "MATCH (c:EvidenceChunk)-[r:FOR_PARAMETER]->() RETURN count(r) AS n"
                ).single()["n"]
                == 0
            )
            assert (
                s.run("MATCH (m:Method) WHERE NOT (m)--() RETURN count(m) AS n").single()["n"] == 0
            )
            # derived shortcuts say so; the default-zone clause is one typed step from Bảng 1
            loose = s.run(
                "MATCH ()-[r:TABLE_CONTEXT|EFFECTIVE_UNDER]->() WHERE r.derived IS NULL "
                "RETURN count(r) AS n"
            )
            assert loose.single()["n"] == 0
            under = s.run(
                "MATCH (:Structure {id: $t})-[:EFFECTIVE_UNDER]->(c) "
                "RETURN collect(c.citation_label) AS l",
                t="QCVN_40_2025/Section2/Section2.1/Table1",
            )
            assert "Thông tư 06/2025/TT-BTNMT, Điều 2, Khoản 4" in under.single()["l"]
            provided = s.run(
                "MATCH (:LegalDocument {id: $d})-[r:HAS_PROVISION]->() "
                "RETURN collect(DISTINCT r.role) AS r",
                d="doc:QCVN_40_2025",
            )
            assert {"scope", "definitions", "effectivity", "transition"} <= set(
                provided.single()["r"]
            )


def test_runtime_reads_meta_and_guards_staleness(neo4j_ready):
    from app.graph.runtime import GraphRuntime

    settings = neo4j_ready.model_copy(update={"neo4j_database": TEST_DB, "graph_backend": "neo4j"})
    maker = get_sessionmaker(settings)
    with maker() as session:
        built = build(session, settings, TEST_DB)
    rt = GraphRuntime(settings)
    try:
        st = rt.status(force=True)
        assert st.ok and st.projection_version == built["version"]
        assert st.index_label == built["index_label"]
        assert st.usable_for(built["index_label"]) is None
        assert "stale" in (st.usable_for("some-other-index") or "")
    finally:
        rt.close()


def test_graph_first_search_in_neo4j_matches_the_in_memory_set(neo4j_ready):
    """The Cypher lookup (relations → candidates) returns the set `graph_candidates.candidates`
    computes in memory, for a threshold question and for an effectivity question."""
    from sqlalchemy import select

    from app.graph.runtime import GraphRuntime
    from app.retrieval import graph_candidates as gc
    from app.retrieval.query import QueryUnderstanding

    settings = neo4j_ready.model_copy(update={"neo4j_database": TEST_DB, "graph_backend": "neo4j"})
    maker = get_sessionmaker(settings)
    with maker() as session:
        build(session, settings, TEST_DB)
        chunk_ids = set(session.execute(select(m.EvidenceChunk.chunk_id)).scalars())
    rt = GraphRuntime(settings)
    try:
        view = gc.load_view(rt, chunk_ids)
        for qu in (
            QueryUnderstanding(
                question="?", domain="wastewater", intent="threshold", parameters=["COD"], zone="B"
            ),
            QueryUnderstanding(
                question="?",
                domain="wastewater",
                intent="effectivity",
                document_ids=["QCVN_40_2025"],
            ),
        ):
            found = gc.neo4j_search(rt, view, qu)
            expected = gc.union(gc.candidates(view, qu))
            assert {r["id"] for r in found} == expected and expected
            assert all(r["score"] is None for r in found)  # no query vector: candidates only
        parts = {p for r in found for p in r["parts"]}
        assert "provisions" in parts  # effectivity: provisions of the QCVN and its circular
    finally:
        rt.close()


def test_gds_ppr_variants(neo4j_ready):
    from app.graph import gds
    from app.graph.runtime import GraphRuntime

    settings = neo4j_ready.model_copy(update={"neo4j_database": TEST_DB})
    maker = get_sessionmaker(settings)
    with maker() as session:
        built = build(session, settings, TEST_DB)
    rt = GraphRuntime(settings)
    try:
        st = rt.status(force=True)
        if not st.gds_version:
            pytest.skip("Graph Data Science plugin not installed")
        counts = {}
        for variant in ("full", "struct", "shuffle"):
            name = gds.ensure_graph(rt, built["version"], variant)
            assert name == gds.ensure_graph(rt, built["version"], variant)  # idempotent
            info = {g["graphName"]: g for g in gds.graph_info(rt)}[name]
            counts[variant] = info["relationshipCount"]
        assert counts["shuffle"] == counts["full"] > counts["struct"] > 0
        name = gds.graph_name(built["version"], "full")
        scores = gds.personalized_pagerank(
            rt, name, {"param:COD": 0.5, "zone:QCVN_40_2025:B": 0.5}, damping=0.85, iterations=20
        )
        top = [c for c, _ in scores[:10]]
        assert any(c.startswith("chunk:QCVN40_2025.B") for c in top)
        # the ontology edge reaches the definition of Cột B (HAS_EVIDENCE role=definition, schema v1.2)
        assert "chunk:QCVN_40_2025/Section1/Section1.3/Definition1.3.3/Point1.3.3.2" in top
        # a rebuild drops the in-memory graphs of the old projection
        with maker() as session:
            build(session, settings, TEST_DB)
        assert not [g for g in gds.graph_info(rt) if built["version"] in g["graphName"]]
    finally:
        rt.close()


def test_chunk_embeddings_and_vector_search(neo4j_ready):
    """Embeddings travel with the projection and the Neo4j vector index ranks like the vectors say,
    through both query forms (SEARCH with the in-index domain filter, legacy queryNodes)."""
    import random

    from sqlalchemy import select, update

    from app.graph import vectors
    from app.graph.runtime import GraphRuntime
    from app.retrieval.vector import Neo4jVectorIndex

    settings = neo4j_ready.model_copy(update={"neo4j_database": TEST_DB, "graph_backend": "neo4j"})
    maker = get_sessionmaker(settings)
    rng = random.Random(7)
    with maker() as session:
        chunks = session.execute(
            select(m.EvidenceChunk.chunk_id, m.EvidenceChunk.domain)
            .where(m.EvidenceChunk.document_id == "QCVN_40_2025")
            .order_by(m.EvidenceChunk.chunk_id)
        ).all()
    vecs = {cid: [rng.gauss(0, 1) for _ in range(m.EMBEDDING_DIM)] for cid, _ in chunks}
    target, target_domain = chunks[0]

    class OneVector:
        def embed_query(self, _q: str) -> list[float]:
            return vecs[target]

    rt = GraphRuntime(settings)
    try:
        with maker.begin() as session:
            session.execute(
                update(m.EvidenceChunk),
                [
                    {"chunk_id": c, "embedding": v, "embedding_model": "fake"}
                    for c, v in vecs.items()
                ],
            )
        with maker() as session:
            built = build(session, settings, TEST_DB)
            # other modules may have left embedded chunks of other documents in qcvn_test
            n_vec = session.query(m.EvidenceChunk).filter(m.EvidenceChunk.embedding.is_not(None))
            n_vec = n_vec.count()
        assert built["vectors"] == n_vec >= len(vecs)
        st = rt.status(force=True)
        assert st.vectors == n_vec and st.server_version is not None
        assert st.usable_for(built["index_label"], need_vectors=True) is None
        stored = rt.run(
            "MATCH (c:EvidenceChunk) RETURN count(c.embedding) AS v, count(c.context_text) AS t, "
            "count(c) AS n"
        )[0]
        assert stored["v"] == n_vec and stored["t"] == stored["n"] >= n_vec

        index = Neo4jVectorIndex(rt, OneVector(), built["index_label"])
        hits = index.search("bất kỳ", k=5)
        assert hits[0][0] == target and abs(hits[0][1] - 1.0) < 1e-4 and len(hits) == 5
        only = index.search("bất kỳ", k=5, domains={target_domain})
        assert only[0][0] == target
        by_id = dict(chunks)
        assert {by_id[c] for c, _ in only} == {target_domain}
        legacy = vectors.search(rt.run, vecs[target], 5, {target_domain}, (5, 26))
        assert [c for c, _ in legacy] == [c for c, _ in only]

        # a chunk that lost its vector in PostgreSQL loses it in the graph on the next build
        with maker.begin() as session:
            session.execute(
                update(m.EvidenceChunk)
                .where(m.EvidenceChunk.chunk_id == target)
                .values(embedding=None, embedding_model=None)
            )
        with maker() as session:
            assert build(session, settings, TEST_DB)["vectors"] == n_vec - 1
        assert rt.run("MATCH (c:EvidenceChunk {id: $id}) RETURN c.embedding AS e", id=target) == [
            {"e": None}
        ]
    finally:
        rt.close()
        with maker.begin() as session:  # leave qcvn_test as other modules expect it
            session.execute(
                update(m.EvidenceChunk)
                .where(m.EvidenceChunk.embedding_model == "fake")
                .values(embedding=None, embedding_model=None, embedded_at=None)
            )


def test_schema_v14_fact_layer_in_the_graph(neo4j_ready):
    """The fact store is on the nodes / edges that already carry the statements, zones are scoped
    by their column scheme, and the projection passes its invariants against PostgreSQL."""
    from app.graph.check import verify
    from app.graph.runtime import GraphRuntime

    settings = neo4j_ready.model_copy(update={"neo4j_database": TEST_DB})
    maker = get_sessionmaker(settings)
    with maker() as session:
        built = build(session, settings, TEST_DB)
        failed = [r for r in verify(session, settings, TEST_DB) if not r["ok"]]
        n_facts = session.query(m.Fact).count()
    assert failed == []
    assert built["schema_version"] == "1.4" and built["source"]["database"] == "qcvn_test"
    assert built["facts"]["nodes"] + built["facts"]["edge_facts"] == n_facts
    assert built["facts"]["unplaced"] == 0
    # edges whose endpoint does not exist are reported, never dropped silently
    assert all(x.startswith("INHERITS_FROM ") for x in built["records"]["dangling"])

    rt = GraphRuntime(settings)
    try:
        rule = rt.run(
            "MATCH (r:LimitRule:Fact {id: $id})-[:IN_ZONE]->(z:Zone) "
            "MATCH (r)-[:FACT_STATED_IN]->(c:EvidenceChunk) "
            "RETURN r.cell_raw AS cell, r.column_key AS col, r.trust AS trust, "
            "r.valid_from AS since, r.address_label AS label, z.id AS zone, c.id AS chunk",
            id="QCVN40_2025.B1.r1.F>2000.B",
        )
        assert rule == [
            {
                "cell": "≤ 50",
                "col": "F>2000.B",
                "trust": rule[0]["trust"],
                "since": "2025-09-01",
                "label": "QCVN 40:2025/BTNMT, Mục 2.1, Bảng 1, TT 1, F > 2000, Cột B",
                "zone": "zone:QCVN_40_2025:B",
                "chunk": "chunk:QCVN40_2025.B1.r1",
            }
        ]
        assert rule[0]["trust"] in ("auto_verified", "pending")
        # ids come from natural keys: no database sequence, no zone keyed by its domain
        assert rt.run(
            "MATCH (n) WHERE n.id =~ '(fact|ver):[0-9]+' OR n.id STARTS WITH 'zone:wastewater' "
            "RETURN count(n) AS c"
        ) == [{"c": 0}]
        # a flow band is a Condition of a declared dimension
        band = rt.run(
            "MATCH (:LimitRule {id: $id})-[:APPLIES_WHEN]->(k:Condition) "
            "RETURN k.dimension AS d, k.op AS op, k.value AS v",
            id="QCVN40_2025.B1.r1.F>2000.B",
        )
        assert band == [{"d": "flow_regime", "op": ">", "v": 2000.0}]
    finally:
        rt.close()


def test_edges_stated_by_several_records_are_merged_in_a_fixed_order():
    from app.graph.build_graph import Projection

    def projection(order):
        p = Projection()
        p.node("Parameter", "param:X")
        p.node("Method", "method:M")
        for row in order:
            p.edge(
                "Parameter", "param:X", "HAS_METHOD", "Method", "method:M",
                row_id=row, method="table_parser", source=row,
            )  # fmt: skip
        p.edge("Parameter", "param:X", "HAS_METHOD", "Method", "method:gone",
               method="table_parser", source="r9")  # fmt: skip
        return p, p.finalize()

    a, stats = projection(["r2", "r1"])
    b, _ = projection(["r1", "r2"])
    assert a.edges == b.edges  # whatever order the database returned the rows in
    (edge,) = a.edges[("Parameter", "HAS_METHOD", "Method")]
    assert edge["props"]["row_id"] == "r1" and edge["props"]["sources"] == ["r1", "r2"]
    assert stats["merged"] == 1 and len(stats["dangling"]) == 1


def test_a_graph_built_from_another_database_is_not_used():
    from app.graph.runtime import GraphStatus

    st = GraphStatus(
        enabled=True, ok=True, projection_version="v", index_label="idx",
        source_database="qcvn_v2", current_database="qcvn_v3",
    )  # fmt: skip
    assert "another database" in st.usable_for("idx")
    assert st.model_copy(update={"current_database": "qcvn_v2"}).usable_for("idx") is None
    # a projection older than schema v1.4 does not name its source: the index label decides
    assert st.model_copy(update={"source_database": None}).usable_for("idx") is None

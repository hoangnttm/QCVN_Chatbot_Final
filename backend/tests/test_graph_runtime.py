"""GraphRuntime without a server: disabled backend, unreachable server, usability reasons."""

from app.core.config import Settings
from app.graph.runtime import GraphRuntime, GraphStatus


def test_disabled_backend_never_connects():
    rt = GraphRuntime(Settings(_env_file=None, graph_backend="off"))
    st = rt.status()
    assert not st.enabled and "disabled" in (st.usable_for("x") or "")
    assert rt._driver is None


def test_unreachable_server_is_reported_not_raised():
    rt = GraphRuntime(Settings(_env_file=None, neo4j_uri="bolt://127.0.0.1:1", neo4j_timeout_s=0.5))
    st = rt.status()
    assert st.enabled and not st.ok and st.error
    assert "unavailable" in (st.usable_for("x") or "")
    rt.close()


def test_usable_for_reasons():
    ok = GraphStatus(enabled=True, ok=True, projection_version="v1", index_label="idx-1")
    assert ok.usable_for("idx-1") is None
    assert "stale" in (ok.usable_for("idx-2") or "")
    assert "Graph Data Science" in (ok.usable_for("idx-1", need_gds=True) or "")
    assert ok.model_copy(update={"gds_version": "2026.05.0"}).usable_for("idx-1", True) is None
    assert "no chunk embeddings" in (ok.usable_for("idx-1", need_vectors=True) or "")
    assert ok.model_copy(update={"vectors": 3}).usable_for("idx-1", need_vectors=True) is None
    missing = GraphStatus(enabled=True, ok=True)
    assert "missing" in (missing.usable_for("idx-1") or "")


def test_every_edge_needs_provenance():
    import pytest

    from app.graph.build_graph import Projection

    p = Projection()
    p.edge("A", "a", "REL", "B", "b", method="dictionary", source="dictionaries/x.yaml", w=None)
    assert p.edges[("A", "REL", "B")][0]["props"] == {
        "method": "dictionary",
        "source": "dictionaries/x.yaml",
    }
    with pytest.raises(ValueError):
        p.edge("A", "a", "REL", "B", "b", method="guess", source="x")
    with pytest.raises(ValueError):
        p.edge("A", "a", "REL", "B", "b", method="dictionary", source="")
    with pytest.raises(TypeError):
        p.edge("A", "a", "REL", "B", "b")  # type: ignore[call-arg]

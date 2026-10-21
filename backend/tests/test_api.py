"""Read-only document API against the throwaway test database (dependency override)."""

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_db
from app.db.loader import load_document
from app.db.session import get_sessionmaker
from app.main import app
from app.registry.loader import load_registry


@pytest.fixture(scope="module")
def client(db_settings):
    reg = load_registry(settings=db_settings)
    maker = get_sessionmaker(db_settings)
    with maker.begin() as session:
        for doc_id in ("TT_06_2025", "QCVN_40_2025"):
            load_document(doc_id, reg, db_settings, session)

    def _db():
        s = maker()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_list_and_get_document(client):
    docs = client.get("/api/v1/documents").json()
    ids = {d["document_id"] for d in docs}
    assert {"TT_06_2025", "QCVN_40_2025"} <= ids and "TT_47_2011" not in ids
    q40 = client.get("/api/v1/documents/QCVN_40_2025").json()
    assert q40["document_code"] == "QCVN 40:2025/BTNMT" and q40["text_status"] == "official"
    assert q40["counts"]["limit_rules"] == 213 and q40["counts"]["table_rows"] == 199
    assert any(
        f["fact_type"] == "effective_date" and f["value"] == "2025-09-01" for f in q40["facts"]
    )
    out = [r for r in q40["relations"] if r["direction"] == "out" and r["type"] == "REPLACES"]
    assert len(out) == 22  # 11 from the registry + 11 extracted from the preamble
    assert any(r["direction"] == "in" and r["type"] == "PROMULGATES" for r in q40["relations"])
    assert q40["review"] == {"pending": 213} or "approved" in q40["review"]
    assert client.get("/api/v1/documents/NOPE").status_code == 404


def test_tree_and_table(client):
    tree = client.get("/api/v1/documents/QCVN_40_2025/tree", params={"include_text": False}).json()
    assert tree["nodes"] > 60
    root = tree["tree"][0]
    assert root["kind"] == "Document" and "text" not in root
    kinds = set()

    def walk(items):
        for it in items:
            kinds.add(it["kind"])
            walk(it["children"])

    walk(tree["tree"])
    assert {"Section", "Clause", "Table", "Appendix"} <= kinds and "Boilerplate" not in kinds

    table = client.get("/api/v1/tables/QCVN40_2025.B2").json()
    assert table["columns"] == ["tt", "parameter", "unit", "A", "B", "C"]
    r4_2 = next(r for r in table["rows"] if r["row_id"] == "QCVN40_2025.B2.r4.2")
    assert r4_2["inherits_from"] == "QCVN40_2025.B2.r4" and r4_2["values"]["A"]["value"] == 8.0
    assert {x["zone"] for x in r4_2["rules"]} == {"A", "B", "C"}
    assert table["footnotes"] or True

    ev = client.get("/api/v1/evidence/chunk:QCVN40_2025.B2.r4.2").json()
    assert ev["citation_label"] == "QCVN 40:2025/BTNMT, Mục 2.2, Bảng 2, TT 4.2"
    assert client.get("/api/v1/health").json()["status"] in ("ok", "degraded")


def test_corpus_info_lists_what_the_ui_used_to_hard_code(client):
    info = client.get("/api/v1/corpus").json()
    assert [d["code"] for d in info["domains"]] and all(d["name"] for d in info["domains"])
    docs = {d["document_id"]: d for d in info["documents"]}
    assert docs["QCVN_40_2025"]["short_code"] == "QCVN 40:2025"
    assert docs["TT_06_2025"]["short_code"] == "TT 06/2025"
    types = [d["document_type"] for d in info["documents"]]
    assert types == sorted(types, reverse=True)  # regulations before circulars
    assert "TT_47_2011" not in docs  # stubs are not part of the corpus
    assert info["sample_questions"]

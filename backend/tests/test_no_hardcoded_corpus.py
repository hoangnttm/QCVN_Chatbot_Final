"""Gate of plan 2026-10-06 G1: runtime code names no document, table or domain of the corpus.

The forbidden names are read from the data themselves (domains.yaml, documents.yaml), so adding a
document or a domain extends the gate without touching this test. Docstrings and comments may still
give examples; only string literals the program can act on are checked.
"""

import ast
import re
from pathlib import Path

import yaml

BACKEND = Path(__file__).resolve().parents[1]
DATA = BACKEND.parent / "data"

# Modules that are about this particular corpus on purpose; each needs a reason.
EXEMPT = {
    "app/evaluation/": "gold-set tooling reads the raw tables of the documents under evaluation",
    "app/applicability/": "rule engine written for QCVN 40 / QCVN 19 (ADR-0010); replaced by the "
    "applicable-document resolution of plan step G4",
    "app/api/applicability.py": "API of that rule engine",
}


def _forbidden() -> list[re.Pattern[str]]:
    domains = yaml.safe_load((DATA / "dictionaries" / "domains.yaml").read_text("utf-8"))
    registry = yaml.safe_load((DATA / "registry" / "documents.yaml").read_text("utf-8"))
    names: set[str] = {d["code"] for d in domains["domains"]}
    for doc in registry["documents"]:
        names.add(doc["document_id"])
        code = doc.get("document_code") or ""
        names.add(code)
        names.add(code.split("/")[0])  # "QCVN 40:2025", "06/2025"
        names.add(doc["document_id"].replace("_", "", 1))  # table codes: QCVN40_2025.B1
    return [
        re.compile(r"(?<![\w])" + re.escape(n) + r"(?![\w])") for n in sorted(names) if len(n) >= 6
    ]


def _literals(tree: ast.AST) -> list[tuple[int, str]]:
    doc_nodes = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                doc_nodes.add(id(body[0].value))
    return [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in doc_nodes
    ]


def test_runtime_code_names_no_document_or_domain():
    patterns = _forbidden()
    hits: list[str] = []
    for path in sorted((BACKEND / "app").rglob("*.py")):
        rel = path.relative_to(BACKEND).as_posix()
        if any(rel.startswith(prefix) for prefix in EXEMPT):
            continue
        for lineno, text in _literals(ast.parse(path.read_text("utf-8"))):
            for pattern in patterns:
                found = pattern.search(text)
                if found:
                    hits.append(f"{rel}:{lineno}: {found.group(0)!r}")
                    break
    assert not hits, "corpus names in runtime code:\n" + "\n".join(hits)

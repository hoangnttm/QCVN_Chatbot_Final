"""Apply an ingest-scope partition (data/grammars/<doc>.scope.yaml) to a parsed structure (ADR-0008).

Every node gets `attrs.scope` (in_scope | structure_only) and `attrs.domain` from the partition of
its enclosing Chapter / Appendix; article-level overrides refine the domain. Chunks are later built
only from in-scope nodes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from app.ingestion.models import ParsedStructure


def load_scope(path: Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def apply_scope(st: ParsedStructure, scope_cfg: dict[str, Any]) -> dict[str, int]:
    """Annotate nodes in place; return counts per scope value."""
    default = scope_cfg.get("default", "structure_only")
    parts = {(p["unit"], str(p["number"])): p for p in scope_cfg.get("partitions", [])}
    by_id = st.by_id()
    counts: dict[str, int] = {}
    for n in st.nodes:
        part = None
        article_no = None
        appendix_sub = None
        cur = n
        while cur is not None:
            if cur.kind == "Article" and article_no is None:
                article_no = cur.number
            if cur.kind == "Appendix" and cur.number and "." in cur.number and appendix_sub is None:
                appendix_sub = cur.number
            if cur.kind in ("Chapter", "Appendix") and cur.number:
                key = (cur.kind, str(cur.number).split(".")[0])
                if key in parts:
                    part = parts[key]
                    break
            cur = by_id.get(cur.parent_id) if cur.parent_id else None
        scope = part.get("scope", default) if part else default
        domain = part.get("domain", "other") if part else "other"
        if part and article_no and str(article_no).isdigit():
            domain = (part.get("articles") or {}).get(int(article_no), domain)
        if part and appendix_sub:
            domain = (part.get("sub") or {}).get(appendix_sub, domain)
        n.attrs["scope"] = scope
        n.attrs["domain"] = domain
        counts[scope] = counts.get(scope, 0) + 1
    return counts

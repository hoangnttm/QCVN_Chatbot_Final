"""Step 7b: MonitoringRequirement from a curated mapping file (PLAN.md §4.1 C, ontology v1).

Prose requirements (sampling conditions, stack ports, direct measurement, method precedence,
frequency pointers) cannot be derived by grammar alone, so a reviewed YAML lists, per node id,
the requirement type and the structured conditions. The extractor only *anchors* each entry to
the parsed node: the node must exist, be in scope, and contain the quoted `anchor` text — so the
verbatim `text` always comes from the source and a changed source fails loudly.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from app.ingestion.models import Condition, MonitoringRequirement, ParsedStructure
from app.ontology.schema import Dictionaries

REQUIREMENT_TYPES = {
    "sampling_condition",
    "sampling_port",
    "direct_measurement",
    "method_precedence",
    "cems",
    "frequency_pointer",
}


def _squash(s: str) -> str:
    return " ".join(s.split())


def load_monitoring_map(path: Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def extract_monitoring(
    st: ParsedStructure, mapping: dict[str, Any], dictionaries: Dictionaries | None = None
) -> tuple[list[MonitoringRequirement], list[str]]:
    by_id = st.by_id()
    known_params = {p.parameter_id for p in dictionaries.parameters} if dictionaries else None
    out: list[MonitoringRequirement] = []
    warnings: list[str] = []
    seen: set[str] = set()
    for e in mapping.get("requirements", []):
        rid = e["id"]
        if rid in seen:
            warnings.append(f"monitoring: duplicate id {rid}")
            continue
        seen.add(rid)
        node = by_id.get(e["node"])
        if node is None:
            warnings.append(f"monitoring {rid}: node {e['node']} not found")
            continue
        if node.attrs.get("scope") == "structure_only":
            warnings.append(f"monitoring {rid}: node {e['node']} is structure_only")
            continue
        if e["type"] not in REQUIREMENT_TYPES:
            warnings.append(f"monitoring {rid}: unknown type {e['type']}")
            continue
        text = node.text or node.title or ""
        if _squash(e["anchor"]) not in _squash(text):
            warnings.append(
                f"monitoring {rid}: anchor not found in {e['node']}: {e['anchor'][:60]!r}"
            )
            continue
        params = list(e.get("parameters") or [])
        if known_params is not None:
            bad = [p for p in params if p not in known_params]
            if bad:
                warnings.append(f"monitoring {rid}: unknown parameter ids {bad}")
                continue
        out.append(
            MonitoringRequirement(
                requirement_id=rid,
                document_id=st.document_id,
                requirement_type=e["type"],
                domain=e.get("domain") or node.attrs.get("domain") or "other",
                summary=e["summary"],
                text=text,
                conditions=[Condition(**c) for c in e.get("conditions") or []],
                parameters=params,
                references=list(e.get("references") or []),
                source_node_id=node.node_id,
                evidence_chunk_id=f"chunk:{node.node_id}",
                citation_label=node.citation_label or "",
                span=node.span,
            )
        )
    return out, warnings

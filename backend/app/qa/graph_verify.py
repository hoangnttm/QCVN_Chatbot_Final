"""Validator rule 8 — graph-grounded citations (plan 2026-09-29 §3).

Rule 3 only checks that a number of the answer occurs in a cited chunk. A table-row chunk holds
every column of its row (Cột A/B/C, both flow bands), so a value read from the wrong column or
band still passes. Rule 8 checks at the level of the LimitRule values behind the row:

- table_row chunk: the LimitRules of the row are filtered by the anchors of the question (zone,
  flow, parameter, fuel, capacity, receiving body). Numbers of the answer that equal a value of a
  filtered-out sibling rule and none of the kept rules' values → `contradicted` (wrong column /
  band). Siblings mentioned next to the right value → `extra_sibling` (warning only).
- any other chunk: a path of <= 4 edges must join it to an anchor (query entity or a cited row);
  none → `weak`.

Anchors that are missing are never guessed: without a zone in the question no column is ruled
out. Modes (settings.graph_verify_mode / EvalOptions.graph_verify): off | observe (D31: record
only) | enforce (evaluation configs: `contradicted` rejects like any other rule).
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.graph.gds import RELS
from app.graph.runtime import GraphRuntime
from app.qa.models import Answer
from app.qa.validator import numbers
from app.retrieval.graph_rank import entity_nodes
from app.retrieval.query import QueryUnderstanding
from app.retrieval.retriever import EvidenceItem

# edges a path from the question to a cited clause may use: the retrieval edges plus the
# prose / provision / shortcut edges of schema v1.3 (app.graph.enrich)
PATH_RELS = [
    *RELS,
    "MENTIONS",
    "STATED_IN",
    "REFERS_TO",
    "USES_TERM",
    "INHERITS_FROM",
    "TABLE_CONTEXT",
    "EFFECTIVE_UNDER",
    "HAS_PROVISION",
]

Status = Literal["connected", "weak", "contradicted", "extra_sibling", "unchecked"]
MAX_HOPS = 4
RB_KEYWORDS = {"lake": "hồ", "river": "sông", "sea": "biển", "saline_brackish": "lợ"}

# references inside an answer ("QCVN 40:2025/BTNMT", "Thông tư 06/2025/TT-BTNMT", "Mục 2.1",
# "Bảng 2", "TT 3", "Điều 4 Khoản 2") carry numbers that are locators, not values
_REFERENCE = re.compile(
    r"(?:QCVN|TCVN)\s*[\w\-]*\d[\w\-]*:\d{4}(?:/[A-Z\-]+)?"
    r"|(?:Thông tư|Nghị định|Quyết định|TT|NĐ)\s*(?:số\s*)?\d+/\d{4}/[\wĐ\-]+"
    r"|\b(?:Mục|Bảng|TT|Điều|Khoản|Điểm|Phụ lục|Chương|Thiết bị|Cột)\s+[\dIVXLC]+(?:\.\d+)*[a-zđ]?\b",
    re.IGNORECASE,
)


def claimed_numbers(texts: list[str]) -> set[str]:
    """Numbers an answer asserts, after removing document / clause references."""
    return {n for t in texts for n in numbers(_REFERENCE.sub(" ", t or ""))}


_RULES = """
UNWIND $ids AS cid
MATCH (c:EvidenceChunk {id: cid})-[:EXTRACTED_FROM]->(row:TableRow)<-[:DEFINED_BY_TABLE_ROW]-(r:LimitRule)
OPTIONAL MATCH (r)-[:APPLIES_WHEN]->(k:Condition)
RETURN cid, row.id AS row_id, r.id AS rule, r.zone AS zone, r.parameter_id AS parameter_id,
       r.value AS value, r.min AS min, r.max AS max, r.ref_o2 AS ref_o2,
       collect({type: k.type, op: k.op, value: k.value, unit: k.unit}) AS conds
"""

_PATHS = """
UNWIND $pairs AS pr
MATCH (a {id: pr[0]}), (c:EvidenceChunk {id: pr[1]})
MATCH p = shortestPath((a)-[*..4]-(c))
WHERE all(r IN relationships(p) WHERE type(r) IN $types
          AND NOT (startNode(r):LegalDocument AND type(r) = 'HAS_EVIDENCE'))
RETURN pr[1] AS cid, pr[0] AS anchor, length(p) AS hops,
       [n IN nodes(p) | {id: n.id, label: [l IN labels(n) WHERE l <> 'Fact'][0],
                         name: coalesce(n.citation_label, n.name, n.title, n.code, n.id)}] AS nodes,
       [r IN relationships(p) | type(r)] AS rels
ORDER BY hops
"""  # 4 = MAX_HOPS (Cypher needs a literal bound)


class PathNode(BaseModel):
    id: str | None = None
    label: str | None = None
    name: str | None = None


class EvidencePath(BaseModel):
    chunk_id: str
    citation_label: str
    status: Status
    reason: str | None = None
    hops: int | None = None
    nodes: list[PathNode] = Field(default_factory=list)
    rels: list[str] = Field(default_factory=list)
    kept_rules: list[str] = Field(default_factory=list)
    allowed_values: list[str] = Field(default_factory=list)
    sibling_values: list[str] = Field(default_factory=list)


class GraphCheck(BaseModel):
    mode: Literal["off", "observe", "enforce"]
    ok: bool = True
    skipped: str | None = None
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    paths: list[EvidencePath] = Field(default_factory=list)
    anchors: list[str] = Field(default_factory=list)
    latency_ms: int = 0


def _num(v: Any) -> str | None:
    """A rule value in the canonical form of validator.numbers (0.05 → "0.05", 3000.0 → "3000")."""
    if v is None:
        return None
    text = format(float(v), "f") if isinstance(v, int | float) else str(v)
    return next(iter(numbers(text)), None)


def _cond_value(c: dict[str, Any]) -> Any:
    v = c.get("value")
    if isinstance(v, str) and v[:1] in "[{":
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


def _cmp(x: float, op: str | None, y: Any) -> bool | None:
    """None (= cannot tell) when the question's number is the threshold itself: "dưới 2 tấn/giờ"
    is understood as capacity 2, which only restates the row's condition."""
    if not isinstance(y, int | float) or x == y:
        return None
    return {
        "<": x < y,
        "<=": x <= y,
        ">": x > y,
        ">=": x >= y,
        "=": x == y,
    }.get(op or "", None)


def rule_matches(rule: dict[str, Any], qu: QueryUnderstanding) -> tuple[bool, str | None]:
    """Whether a LimitRule is compatible with the anchors the question states. Unknown anchors
    and condition types the question says nothing about never exclude a rule."""
    if qu.zone in ("A", "B", "C") and rule.get("zone") and rule["zone"] != qu.zone:
        return False, f"Cột {rule['zone']} ≠ Cột {qu.zone} của câu hỏi"
    if qu.parameters and rule.get("parameter_id") and rule["parameter_id"] not in qu.parameters:
        return False, f"thông số {rule['parameter_id']} không phải thông số được hỏi"
    for c in rule.get("conds") or []:
        t = c.get("type")
        if t is None:
            continue
        v = _cond_value(c)
        if t == "flow_regime" and qu.flow_m3_day is not None:
            if _cmp(qu.flow_m3_day, c.get("op"), v) is False:
                return (
                    False,
                    f"dải lưu lượng {c.get('op')} {v} không khớp {qu.flow_m3_day:g} m³/ngày",
                )
        elif t == "fuel_type" and qu.fuel_type and isinstance(v, str) and v != qu.fuel_type:
            return False, f"nhiên liệu {v} ≠ {qu.fuel_type}"
        elif (
            t == "capacity"
            and qu.capacity_value is not None
            and qu.capacity_unit
            and c.get("unit") == qu.capacity_unit
            and _cmp(qu.capacity_value, c.get("op"), v) is False
        ):
            return False, f"công suất {c.get('op')} {v} {c.get('unit')} không khớp"
        elif t == "receiving_body" and qu.receiving_body and isinstance(v, str):
            kw = RB_KEYWORDS.get(qu.receiving_body)
            if kw and kw not in v.lower():
                return False, f"nguồn tiếp nhận '{v[:40]}' không khớp"
    return True, None


def _values(rule: dict[str, Any], limits_only: bool = False) -> set[str]:
    """Canonical numbers of a rule. `limits_only` leaves out the O₂ reference, which is the same
    in every column and so says nothing about which column the answer read."""
    out = set()
    for k in ("value", "min", "max") if limits_only else ("value", "min", "max", "ref_o2"):
        n = _num(rule.get(k))
        if n is not None:
            out.add(n)
    return out


def check(
    ans: Answer,
    evidence: list[EvidenceItem],
    qu: QueryUnderstanding,
    question: str,
    rt: GraphRuntime,
    *,
    mode: Literal["off", "observe", "enforce"],
    active_label: str | None,
) -> GraphCheck:
    t0 = time.perf_counter()
    gc = GraphCheck(mode=mode)
    if mode == "off":
        gc.skipped = "off"
        return gc
    cited = [c for c in ans.all_citations()]
    by_id = {e.chunk_id: e for e in evidence}
    cited_items = [by_id[c] for c in dict.fromkeys(cited) if c in by_id]
    if ans.abstained or not cited_items:
        gc.skipped = "no citations"
        return gc
    reason = rt.status().usable_for(active_label)
    if reason:
        gc.skipped = reason
        return gc
    claimed = claimed_numbers([ans.conclusion, *(b.text for b in ans.basis)])
    claimed -= numbers(question)
    rows = [e for e in cited_items if e.chunk_type == "table_row"]
    others = [e for e in cited_items if e.chunk_type != "table_row"]
    # numbers that a cited non-row chunk (footnote, clause) grounds are not row values
    other_nums = {n for e in others for n in numbers(e.text)}
    try:
        rule_rows = rt.run(_RULES, ids=[e.chunk_id for e in rows]) if rows else []
        by_chunk: dict[str, list[dict[str, Any]]] = {}
        for r in rule_rows:
            by_chunk.setdefault(r["cid"], []).append(r)
        for e in rows:
            rules = by_chunk.get(e.chunk_id, [])
            if not rules:
                gc.paths.append(
                    EvidencePath(
                        chunk_id=e.chunk_id,
                        citation_label=e.citation_label,
                        status="unchecked",
                        reason="dòng bảng không có LimitRule",
                    )
                )
                continue
            kept, dropped = [], []
            for r in rules:
                ok, why = rule_matches(r, qu)
                (kept if ok else dropped).append((r, why))
            allowed = {v for r, _ in kept for v in _values(r)}
            sibling = {v for r, _ in dropped for v in _values(r, limits_only=True)} - allowed
            wrong = (claimed & sibling) - other_nums
            right = claimed & {v for r, _ in kept for v in _values(r, limits_only=True)}
            path = EvidencePath(
                chunk_id=e.chunk_id,
                citation_label=e.citation_label,
                status="connected",
                kept_rules=[r["rule"] for r, _ in kept],
                allowed_values=sorted(allowed),
                sibling_values=sorted(sibling),
                nodes=[
                    *(PathNode(id=f"param:{p}", label="Parameter", name=p) for p in qu.parameters),
                    *(
                        PathNode(
                            id=r["rule"],
                            label="LimitRule",
                            name=f"Cột {r['zone']}" if r.get("zone") else r["rule"],
                        )
                        for r, _ in kept
                    ),
                    PathNode(id=rules[0]["row_id"], label="TableRow", name=e.citation_label),
                    PathNode(id=e.chunk_id, label="EvidenceChunk", name=e.citation_label),
                ],
                rels=["FOR_PARAMETER", "DEFINED_BY_TABLE_ROW", "EXTRACTED_FROM"],
            )
            if wrong and not right:
                path.status = "contradicted"
                why = next(
                    (w for r, w in dropped if w and _values(r, limits_only=True) & wrong), None
                )
                path.reason = f"số {', '.join(sorted(wrong))} thuộc cột/dải khác ({why})"
                gc.errors.append(f"{e.citation_label}: {path.reason}")
            elif wrong:
                path.status = "extra_sibling"
                path.reason = f"nêu thêm giá trị của cột/dải khác: {', '.join(sorted(wrong))}"
                gc.warnings.append(f"{e.citation_label}: {path.reason}")
            elif not kept:
                path.status = "contradicted"
                path.reason = "; ".join(sorted({w for _, w in dropped if w}))[:200]
                gc.errors.append(
                    f"{e.citation_label}: không có giá trị nào khớp câu hỏi ({path.reason})"
                )
            gc.paths.append(path)

        anchors = entity_nodes(qu) + [e.chunk_id for e in rows]
        gc.anchors = anchors
        if others and anchors:
            pairs = [[a, e.chunk_id] for e in others for a in anchors if a != e.chunk_id]
            found = rt.run(_PATHS, pairs=pairs, types=PATH_RELS, timeout=max(rt.timeout, 3.0))
            best: dict[str, dict[str, Any]] = {}
            for f in found:
                best.setdefault(f["cid"], f)
            for e in others:
                f = best.get(e.chunk_id)
                if f is None:
                    gc.paths.append(
                        EvidencePath(
                            chunk_id=e.chunk_id,
                            citation_label=e.citation_label,
                            status="weak",
                            reason=f"không nối được với câu hỏi trong {MAX_HOPS} bước",
                        )
                    )
                    gc.warnings.append(f"{e.citation_label}: không có đường nối với câu hỏi")
                else:
                    gc.paths.append(
                        EvidencePath(
                            chunk_id=e.chunk_id,
                            citation_label=e.citation_label,
                            status="connected",
                            hops=f["hops"],
                            nodes=[PathNode(**n) for n in f["nodes"]],
                            rels=f["rels"],
                        )
                    )
        elif others:
            for e in others:
                gc.paths.append(
                    EvidencePath(
                        chunk_id=e.chunk_id,
                        citation_label=e.citation_label,
                        status="unchecked",
                        reason="câu hỏi không có thực thể để neo",
                    )
                )
    except Exception as ex:  # noqa: BLE001 - rule 8 must never break an answer
        rt.mark_failed(ex)
        gc.skipped = f"{type(ex).__name__}: {str(ex)[:160]}"
        gc.errors, gc.warnings = [], []
    gc.ok = not gc.errors
    gc.latency_ms = int((time.perf_counter() - t0) * 1000)
    return gc

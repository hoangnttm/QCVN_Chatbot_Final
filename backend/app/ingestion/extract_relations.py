"""Step 7: pull legal relations and transition rules out of the parsed text and cross-check them
against the human-declared registry (PLAN.md §1.5, §5 step 7).

Regex-only. Codes found in the text but missing from the registry (or vice versa) are reported as
mismatches — they become review tasks, never silent edits of the registry.
"""

from __future__ import annotations

import re

from app.ingestion.models import (
    ExtractedRelation,
    ExtractedRelations,
    Node,
    ParsedStructure,
    TransitionRule,
)
from app.registry.schema import Document, Registry

_QCVN = re.compile(r"QCVN\s*\d+(?:-MT)?:\d{4}/BTNMT")
_CIRC = re.compile(r"Thông tư số\s*(\d+/\d{4}/TT-BTNMT)")
_DATE = re.compile(r"ngày\s*(\d{1,2})\s*tháng\s*(\d{1,2})\s*năm\s*(\d{4})")
# "Bảng 1 và Bảng 4 QCVN 41:2011/BTNMT", "Bảng 2 QCVN 02:2012/BTNMT"
_PARTIAL = re.compile(
    r"(Bảng\s*\d+(?:\s*(?:,|và)\s*Bảng\s*\d+)*)\s*(QCVN\s*\d+(?:-MT)?:\d{4}/BTNMT)"
)
# "Chương III Thông tư số 43/2015/TT-BTNMT"
_PARTIAL_CIRC = re.compile(r"(Chương\s+[IVX]+)\s+Thông tư số\s*(\d+/\d{4}/TT-BTNMT)")
# REPEALS ("hết hiệu lực") and REPLACES ("thay thế") are the same supersession family when
# cross-checking the registry: a circular repeals the older circular it replaces.
_FAMILY = {"REPEALS": "SUPERSEDES", "REPLACES": "SUPERSEDES"}
_RELATIVE = [
    "ngày Thông tư này có hiệu lực thi hành",
    "ngày Thông tư này có hiệu lực",
]


def _iso(d: str, m: str, y: str) -> str:
    return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"


def _code_index(reg: Registry) -> dict[str, str]:
    idx: dict[str, str] = {}
    for d in reg.documents + reg.external_stubs:
        idx[re.sub(r"\s+", "", d.document_code)] = d.document_id
    return idx


def extract_relations(st: ParsedStructure, doc: Document, reg: Registry) -> ExtractedRelations:
    out = ExtractedRelations(document_id=doc.document_id)
    idx = _code_index(reg)
    by_id = st.by_id()

    def target_id(code: str) -> str | None:
        key = re.sub(r"\s+", "", code)
        return idx.get(key) or idx.get(key.replace("Thôngtưsố", ""))

    def rel(
        type_: str,
        code: str,
        node: Node,
        scope: str = "full",
        part: str | None = None,
        cond: str | None = None,
    ) -> None:
        out.relations.append(
            ExtractedRelation(
                type=type_,
                target_code=code,
                target_document_id=target_id(code),
                scope=scope,
                target_part=part,
                condition=cond,
                source_node_id=node.node_id,
                citation_label=node.citation_label or node.node_id,
                span=node.span,
            )
        )

    # -- QCVN preamble: "thay thế các Quy chuẩn:" (full) + "Bảng X … QCVN …" (partial) --------
    for n in st.nodes:
        if n.kind != "Preamble" or not n.text:
            continue
        text = n.text
        if "thay thế" in text:
            full_part, _, partial_part = text.partition("thay thế giá trị")
            partial_codes = {m.group(2) for m in _PARTIAL.finditer(partial_part)}
            for m in _PARTIAL.finditer(partial_part):
                rel("REPLACES", m.group(2), n, scope="partial", part=m.group(1).strip())
            for code in _QCVN.findall(full_part):
                if code == doc.document_code or code in partial_codes:
                    continue
                rel("REPLACES", code, n)

    # -- Circular: "hết hiệu lực" points, transition/roadmap articles, effective date -----------
    for n in st.nodes:
        if n.kind != "Article":
            continue
        title = (n.title or "").lower()
        children = [c for c in st.nodes if c.parent_id == n.node_id]
        if "hiệu lực" in title:
            for cl in children:
                cl_text = cl.text or ""
                m = re.search(r"có hiệu lực thi hành kể từ " + _DATE.pattern, cl_text)
                if m and doc.text_status.value == "official":
                    out.effective_date_from_text = _iso(m.group(1), m.group(2), m.group(3))
                if "hết hiệu lực" in cl_text:
                    cond = None
                    cm = re.search(r"theo quy định về (.+?) Thông tư này", cl_text)
                    if cm:
                        cond = cm.group(0)
                    points = [p for p in st.nodes if p.parent_id == cl.node_id] or [cl]
                    partial = "Bảng" in cl_text
                    for p in points:
                        # "(ban hành kèm theo Thông tư số …)" names the promulgating circular of the
                        # repealed QCVN, not a repeal target — drop parentheticals before matching
                        ptxt = re.sub(r"\([^)]*\)", " ", p.text or "")
                        if partial:
                            for pm in _PARTIAL.finditer(ptxt):
                                rel(
                                    "REPEALS",
                                    pm.group(2),
                                    p,
                                    scope="partial",
                                    part=pm.group(1).strip(),
                                    cond=cond,
                                )
                        else:
                            qcvn_codes = _QCVN.findall(ptxt)
                            for code in qcvn_codes:
                                rel("REPEALS", code, p, cond=cond)
                            if not qcvn_codes:
                                partial_circ = {
                                    pm.group(2): pm.group(1) for pm in _PARTIAL_CIRC.finditer(ptxt)
                                }
                                for code in _CIRC.findall(ptxt):
                                    if code in partial_circ:
                                        rel(
                                            "REPEALS",
                                            code,
                                            p,
                                            scope="partial",
                                            part=partial_circ[code],
                                            cond=cond,
                                        )
                                    else:
                                        rel("REPEALS", code, p, cond=cond)
                if "chưa xác định được phân vùng" in cl_text:
                    out.transition_rules.append(_tr(doc, cl, "default_zone"))
        if "chuyển tiếp" in title or "lộ trình" in title:
            for cl in children:
                out.transition_rules.append(
                    _tr(doc, cl, "transition" if "chuyển tiếp" in title else "roadmap")
                )

    # -- cross-check with the registry ---------------------------------------------------------
    declared = {
        (_FAMILY[r.type], r.target) for r in doc.relations if r.type in ("REPLACES", "REPEALS")
    }
    found = {
        (_FAMILY[r.type], r.target_document_id)
        for r in out.relations
        if r.target_document_id and r.type in ("REPLACES", "REPEALS")
    }
    for t, code in sorted(found - declared):
        out.mismatches.append(f"text has {t} -> {code} but registry does not")
    for t, code in sorted(declared - found):
        out.mismatches.append(
            f"registry declares {t} -> {code} but text extraction did not find it"
        )
    for r in out.relations:
        if r.target_document_id is None:
            out.mismatches.append(f"unknown target code {r.target_code!r} at {r.citation_label}")
    if out.effective_date_from_text:
        fact = doc.fact("effective_date")
        if fact and fact.value != out.effective_date_from_text:
            out.mismatches.append(
                f"effective_date text {out.effective_date_from_text} != registry {fact.value}"
            )
    _ = by_id
    return out


def _tr(doc: Document, cl: Node, kind: str) -> TransitionRule:
    text = cl.text or ""
    dates = [_iso(d, m, y) for d, m, y in _DATE.findall(text)]
    rel_phrases = [p for p in _RELATIVE if p in text]
    return TransitionRule(
        rule_id=f"tr:{cl.node_id}",
        document_id=doc.document_id,
        kind=kind,
        text=text,
        dates=dates,
        relative_dates=rel_phrases,
        source_node_id=cl.node_id,
        citation_label=cl.citation_label or cl.node_id,
        span=cl.span,
    )

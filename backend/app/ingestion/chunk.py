"""Step 8: build EvidenceChunks — the citable retrieval units (PLAN.md §5 step 8, §4.1 H).

One chunk per legal unit (clause / point / definition / footnote / preamble / formula / table row /
registry fact). `text` is the unit itself; `context_text` prepends the citation label, the parent
headings, table headers and inherited qualifiers so a row like "≤ 350(6)" is retrievable by
"CO lò hơi đốt than cột B". Chunks are never cut by token count.
"""

from __future__ import annotations

from app.ingestion.axes import ColumnSchema, tier_label
from app.ingestion.models import EvidenceChunk, Node, ParsedStructure, ParsedTables, Span, TableRow
from app.ingestion.textnorm import plain
from app.registry.schema import Document, FactSource

CHUNK_KINDS = {
    "Clause": "clause",
    "Point": "point",
    "Definition": "definition",
    "Paragraph": "paragraph",
    "Footnote": "footnote",
    "Preamble": "preamble",
    "Formula": "formula",
    "FormulaVariables": "formula",
}
HEADING_KINDS = {
    "Section",
    "Article",
    "Chapter",
    "Appendix",
    "AppendixSection",
    "Table",
    "AppendixTable",
    "Definition",
    "Clause",
}


def _heading_chain(node: Node, by_id: dict[str, Node]) -> list[str]:
    parts: list[str] = []
    cur = by_id.get(node.parent_id) if node.parent_id else None
    while cur is not None:
        if cur.kind in HEADING_KINDS:
            head = cur.title or cur.term or (cur.text or "")[:80]
            if head:
                parts.append(f"{cur.kind} {cur.number or ''}: {head}".strip())
        cur = by_id.get(cur.parent_id) if cur.parent_id else None
    return list(reversed(parts))


def chunks_from_structure(st: ParsedStructure, doc: Document, domain: str) -> list[EvidenceChunk]:
    by_id = st.by_id()
    out: list[EvidenceChunk] = []
    for n in st.nodes:
        ctype = CHUNK_KINDS.get(n.kind)
        if not ctype or not n.text:
            continue
        if n.attrs.get("scope") == "structure_only":  # ADR-0008: parsed but never retrievable
            continue
        # a Definition/Clause with children still gets its own chunk (its own sentence matters)
        label = n.citation_label or f"{doc.document_code}, {n.kind} {n.number or ''}".strip()
        parents = " > ".join(_heading_chain(n, by_id))
        body = n.text if not n.term else f"{n.term}: {n.text}"
        out.append(
            EvidenceChunk(
                chunk_id=f"chunk:{n.node_id}",
                document_id=doc.document_id,
                chunk_type=ctype,
                domain=n.attrs.get("domain") or domain,
                scope=n.attrs.get("scope", "in_scope"),
                source_id=n.node_id,
                text=body,
                context_text=f"[{label}] {parents} | {body}".strip(),
                citation_label=label,
                span=n.span,
                text_status=doc.text_status.value,
            )
        )
    return out


def _column_label(schema: ColumnSchema, key: str) -> str:
    """A value column as the regulation names it — "F ≤ 2000, Cột A" — not as the parser keys it
    ("F<=2000.A"). The same parts make the citation label of the cell."""
    meaning = schema.meaning(key)
    parts = [tier_label(axis, tier) for axis, tier in meaning.tiers]
    if meaning.zone is not None:
        parts.append(schema.zone_text(meaning.zone))
    return ", ".join(parts) or key


def _row_text(r: TableRow, t_title: str | None, schema: ColumnSchema) -> str:
    bits: list[str] = []
    if r.cells.get("parameter"):
        bits.append(r.cells["parameter"])
    if r.cells.get("unit"):
        bits.append(f"đơn vị {r.cells['unit']}")
    if r.cells.get("equipment_label"):
        eq_chain = [c.text for c in r.conditions if c.type == "equipment_path" and c.text]
        inherited = [
            c.text
            for c in r.conditions
            if c.inherited_from and c.text and c.type != "equipment_path"
        ]
        bits.append("thiết bị: " + "; ".join([*inherited, *eq_chain]))
    if r.cells.get("condition_label"):
        bits.append(r.cells["condition_label"])
    if r.values:
        vals = []
        for k, v in r.values.items():
            # the cell as printed ("≤ 8,0", "≤ 130(6)"): what the reader sees is what the document
            # says, never a value rewritten by the parser ("<= 8.0")
            cell = r.cells.get(k) or plain(v.raw or "")
            s = f"{_column_label(schema, k)}: {cell}"
            if v.ref_o2 is not None:
                s += f" (O2 tham chiếu {v.ref_o2:g} %)"
            vals.append(s)
        bits.append("giới hạn " + "; ".join(vals))
    if r.methods:
        bits.append(
            "phương pháp: "
            + "; ".join(f"{m.code} ({m.stage}, thứ tự {m.order})" for m in r.methods)
        )
    if r.cells.get("sector"):
        bits.append(r.cells["sector"])
        if r.vsic_codes:
            bits.append("mã ngành " + ", ".join(r.vsic_codes))
        if r.cells.get("characteristic_parameters"):
            bits.append("thông số đặc trưng: " + r.cells["characteristic_parameters"])
        if r.conditional_parameters:
            bits.append(
                "có thể phát sinh: "
                + "; ".join(
                    f"{c['condition_text']}: {c['parameters_raw']}"
                    if c.get("condition_text")
                    else c["parameters_raw"]
                    for c in r.conditional_parameters
                )
            )
    if r.coefficient:
        bits.append(f"{r.coefficient.get('name')}: TEF {r.coefficient.get('tef')}")
    if r.exceptions:
        bits.append("ngoại lệ: " + "; ".join(r.exceptions))
    return " | ".join(bits)


def chunks_from_tables(
    pt: ParsedTables, st: ParsedStructure, doc: Document, domain: str
) -> list[EvidenceChunk]:
    by_id = st.by_id()
    out: list[EvidenceChunk] = []
    for t in pt.tables:
        node = by_id[t.node_id]
        if node.attrs.get("scope") == "structure_only":
            continue
        footnotes = [by_id[f].text or "" for r in t.rows[:1] for f in r.footnote_refs if f in by_id]
        # header cells without their markup: "**$F \le 2000$**" is indexed as "F ≤ 2000"
        header = " | ".join(" ".join(plain(h) for h in row if h) for row in t.header_rows)
        schema = ColumnSchema.from_config(t.config)
        for r in t.rows:
            if r.is_group_header:
                continue
            text = _row_text(r, t.title, schema)
            ctx = f"[{r.citation_label}] {t.title or node.kind} | cột: {header} | {text}"
            if footnotes:
                ctx += " | chú thích: " + " ".join(footnotes)
            out.append(
                EvidenceChunk(
                    chunk_id=f"chunk:{r.row_id}",
                    document_id=doc.document_id,
                    chunk_type="table_row",
                    domain=node.attrs.get("domain") or domain,
                    scope=node.attrs.get("scope", "in_scope"),
                    source_id=r.row_id,
                    text=text,
                    context_text=ctx,
                    citation_label=r.citation_label,
                    span=r.span,
                    entities=dict(r.entities),
                    footnote_refs=list(r.footnote_refs),
                    text_status=doc.text_status.value,
                )
            )
    return out


def chunks_from_registry_facts(doc: Document, domain: str) -> list[EvidenceChunk]:
    """ADR-0004: dates declared by the admin are citable only through a registry_fact chunk."""
    out: list[EvidenceChunk] = []
    names = {
        "document_number": "số hiệu",
        "issue_date": "ngày ký",
        "signing_place": "nơi ký",
        "effective_date": "ngày hiệu lực",
        "partial_effective_date": "ngày hiệu lực một phần",
        "signer": "người ký",
    }
    for i, f in enumerate(doc.facts):
        if f.source != FactSource.admin_declared:
            continue
        label = (
            f"Sổ đăng ký văn bản: {doc.document_code} — {names.get(f.fact_type, f.fact_type)} {f.value} "
            f"(do quản trị viên khai báo ngày {f.declared_at})"
        )
        text = (
            f"{doc.document_code} ({doc.title}): {names.get(f.fact_type, f.fact_type)} {f.value}."
        )
        out.append(
            EvidenceChunk(
                chunk_id=f"chunk:{doc.document_id}/registry_fact/{f.fact_type}/{i}",
                document_id=doc.document_id,
                chunk_type="registry_fact",
                domain=domain,
                source_id=f"{doc.document_id}:registry_fact:{f.fact_type}",
                text=text,
                context_text=f"[{label}] {text} Khai báo bởi {f.declared_by}.",
                citation_label=label,
                span=Span(
                    file_hash=doc.source.sha256 if doc.source else "", line_start=0, line_end=0
                ),
                entities={"fact_type": f.fact_type, "source": f.source.value},
                text_status=doc.text_status.value,
            )
        )
    return out

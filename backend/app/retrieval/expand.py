"""Graph expansion (PLAN.md §6.5): 1–2 hops from the top hits through the PostgreSQL structure —
footnotes of the same table, the parent clause, definitions of terms in the question, transition
rules / registry facts of the documents involved, and the M-01 pointer clauses for frequency."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models as m
from app.ontology import corpus as corpus_mod
from app.retrieval.query import CROSS_DOMAIN, QueryUnderstanding
from app.retrieval.tokenize import normalize


def _in_document_order(stmt):
    """Rows in the order of the legal text. Without it PostgreSQL returns them in physical order,
    which differs from one load of the same corpus to the next — and, because the number of
    additions is capped, so would the evidence pack."""
    return stmt.order_by(
        m.EvidenceChunk.document_id, m.EvidenceChunk.line_start, m.EvidenceChunk.chunk_id
    )


def expand(
    session: Session,
    top_ids: list[str],
    qu: QueryUnderstanding,
    max_add: int = 6,
    corpus: corpus_mod.Corpus | None = None,
) -> list[tuple[str, str]]:
    """Return [(chunk_id, reason)] to append after the top hits (deduped, capped)."""
    corpus = corpus or corpus_mod.current(session)
    added: list[tuple[str, str]] = []
    seen = set(top_ids)

    def add(cid: str | None, why: str) -> None:
        if cid and cid not in seen and len(added) < max_add:
            if session.get(m.EvidenceChunk, cid) is not None:
                seen.add(cid)
                added.append((cid, why))

    chunks = [c for c in (session.get(m.EvidenceChunk, cid) for cid in top_ids) if c]
    docs_involved = {c.document_id for c in chunks} | set(qu.document_ids)

    # frequency → the pointer clauses (D10 / M-01) must be citable
    # the clauses that refer to "pháp luật hiện hành" for the frequency — keyed by structural
    # chunk id (monitoring requirements of type frequency_pointer), never by citation label
    if qu.policy == "M-01":
        for cid in corpus.frequency_pointers:
            add(cid, "M-01 pointer")

    # resolved column/zone → its definition point in the regulation of the domain (e.g. Mục
    # 1.3.3.x / 1.3.13.x), so the LLM can cite why "vùng hạn chế phát thải" means Cột B
    if qu.zone in corpus.zones() and qu.intent in ("threshold", "applicability", "scope"):
        for dom in corpus.domains:
            if qu.domain not in (dom, CROSS_DOMAIN):
                continue
            for doc_id in corpus.regulations(dom):
                cid = session.execute(
                    select(m.EvidenceChunk.chunk_id)
                    .where(
                        m.EvidenceChunk.document_id == doc_id,
                        m.EvidenceChunk.chunk_type == "point",
                        m.EvidenceChunk.text.like(f"Cột {qu.zone}:%"),
                    )
                    .order_by(m.EvidenceChunk.line_start, m.EvidenceChunk.chunk_id)
                    .limit(1)
                ).scalar_one_or_none()
                add(cid, f"zone definition Cột {qu.zone}")

    for c in chunks[:5]:
        if c.chunk_type == "table_row":
            row = session.get(m.TableRow, c.source_id)
            for ref in row.footnote_refs if row else []:
                add(f"chunk:{ref}", f"footnote of {row.table_code}")
        elif c.chunk_type in ("clause", "point", "paragraph"):
            node = session.get(m.StructureNode, c.source_id)
            if node and node.parent_id:
                add(f"chunk:{node.parent_id}", "parent clause")

    # definitions of terms present in the question
    q = normalize(qu.question)
    for d in session.execute(
        _in_document_order(
            select(m.EvidenceChunk).where(m.EvidenceChunk.chunk_type == "definition")
        )
    ).scalars():
        term = normalize((d.text or "").split(":")[0])
        if term and len(term) > 3 and term in q:
            add(d.chunk_id, f"definition '{term}'")

    if qu.intent in ("effectivity", "applicability"):
        # documents that replace / repeal the ones asked about (e.g. QCVN 40:2011 → QCVN 40:2025),
        # then the circulars that promulgate them — their preamble and transition rules are the
        # citable evidence for "còn hiệu lực không?"
        for _ in range(2):
            rel = session.execute(
                select(m.DocumentRelation)
                .where(
                    m.DocumentRelation.type.in_(
                        ["PROMULGATES", "PROMULGATED_BY", "REPLACES", "REPEALS"]
                    ),
                    (m.DocumentRelation.source_document_id.in_(docs_involved))
                    | (m.DocumentRelation.target_document_id.in_(docs_involved)),
                )
                .order_by(
                    m.DocumentRelation.source_document_id,
                    m.DocumentRelation.type,
                    m.DocumentRelation.target_document_id,
                )
            ).scalars()
            for r in rel:
                if r.type in ("REPLACES", "REPEALS"):
                    docs_involved.add(r.source_document_id)  # the newer document
                else:
                    docs_involved |= {
                        r.source_document_id,
                        r.target_document_id or r.source_document_id,
                    }
        for pre in session.execute(
            _in_document_order(
                select(m.EvidenceChunk).where(
                    m.EvidenceChunk.chunk_type == "preamble",
                    m.EvidenceChunk.document_id.in_(docs_involved),
                )
            )
        ).scalars():
            add(pre.chunk_id, f"preamble of {pre.document_id}")
        for t in session.execute(
            select(m.TransitionRule)
            .where(m.TransitionRule.document_id.in_(docs_involved))
            .order_by(
                m.TransitionRule.document_id, m.TransitionRule.line_start, m.TransitionRule.rule_id
            )
        ).scalars():
            add(f"chunk:{t.source_node_id}", f"transition rule of {t.document_id}")
        for f in session.execute(
            _in_document_order(
                select(m.EvidenceChunk).where(
                    m.EvidenceChunk.chunk_type == "registry_fact",
                    m.EvidenceChunk.document_id.in_(docs_involved),
                )
            )
        ).scalars():
            add(f.chunk_id, "registry fact")
    return added

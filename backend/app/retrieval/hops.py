"""Hops of a multi-hop question (plan docs/plans/2026-10-12-g3-tach-cau-va-du-kien-thieu.md,
part G): the second basis an answer needs besides the table cell, looked up in the structured
tables of the fact store instead of being left to the ranking of a text search.

`FrameResolver.resolve` completes a frame built by the rules:

1. hops that change the question run first — a replaced document is switched to the one that
   replaces it, the column of an unzoned facility is filled from the clause that sets the default;
2. the candidate cells are counted on the slots as they now stand (`candidates.py`);
3. hops that explain run last — the definition of a described column, the roadmap clause, the
   footnote of the cells that are left;
4. a hop the question calls for but the tables cannot resolve is kept as `unresolved` and makes
   the frame unsure; it is never dropped silently.

Every sub-question of a split question is resolved on its own. Read only; no LLM.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models as m
from app.ontology.corpus import Corpus
from app.ontology.schema import Dictionaries, HopSpec
from app.retrieval.candidates import CandidateStore, Clarification
from app.retrieval.frame import OUT_OF_SCOPE, FrameBuilder, Hop, QueryFrame, Slot, SubQuestion
from app.retrieval.tokenize import normalize

MAX_CHAIN = 2  # a hop may follow one reference of the clause it resolves to (D67)
REPLACING = ("REPLACES", "REPEALS")
_ZONE_IN_TEXT = re.compile(r"(?<!\w)cột\s*([a-z0-9])(?!\w)")
_REFERENCE = re.compile(r"khoản\s+(\d+)\s+điều\s+(\d+)")
THRESHOLD = "threshold"


class FrameResolver:
    """Built once per process: the rule builder, the candidate store and the hop tables."""

    def __init__(
        self, session: Session, d: Dictionaries, builder: FrameBuilder, corpus: Corpus
    ) -> None:
        self.s = session
        self.builder = builder
        self.corpus = corpus
        self.store = CandidateStore(session, d)
        self.specs = [h for h in d.hops if h.enabled]
        self.zone_sources = {(z.domain, z.code): list(z.sources) for z in d.zones}
        docs = list(session.execute(select(m.Document)).scalars())
        self.doc_domain = {x.document_id: x.domain for x in docs}
        self.doc_code = {x.document_id: x.document_code for x in docs}
        self.transitions = list(
            session.execute(select(m.TransitionRule).order_by(m.TransitionRule.rule_id)).scalars()
        )
        self.relations = list(
            session.execute(
                select(m.DocumentRelation)
                .where(m.DocumentRelation.type.in_(REPLACING))
                .order_by(m.DocumentRelation.id)
            ).scalars()
        )

    # ------------------------------------------------------------------ public

    def understand(self, question: str) -> QueryFrame:
        return self.resolve(self.builder.build(question))

    def resolve(self, frame: QueryFrame) -> QueryFrame:
        q = normalize(frame.question)
        if frame.sub_questions:
            for sub in frame.sub_questions:
                hops, clar = self._part(normalize(sub.text), sub)
                frame.hops += hops
                if clar is not None and frame.clarification is None and clar.mode != "answer":
                    frame.clarification = clar.model_dump()
                    frame.missing = clar.missing
        else:
            frame.hops, clar = self._part(q, frame)
            if clar is not None:
                frame.clarification = clar.model_dump()
                frame.missing = clar.missing
        if any(h.status == "unresolved" for h in frame.hops):
            frame.unsure.append("hop:unresolved")
        return frame

    # ------------------------------------------------------------------ one question

    def _part(
        self, q: str, part: QueryFrame | SubQuestion
    ) -> tuple[list[Hop], Clarification | None]:
        hops: list[Hop] = []
        documents = [s.value for s in part.slots if s.dimension == "document"]
        for spec in self.specs:
            if spec.effect == "explain":
                continue
            hop = getattr(self, f"_{spec.lookup}")(spec, q, part, None)
            if hop is None:
                continue
            hops.append(hop)
            if hop.status != "resolved":
                continue
            if spec.effect == "switch_document":
                documents = [hop.effect["to"] if x == hop.effect["from"] else x for x in documents]
            elif spec.effect == "fill_slot":
                part.slots.append(
                    Slot(
                        dimension=hop.effect["slot"],
                        value=hop.effect["value"],
                        source="hop",
                        stated_as="hop",
                        text=hop.trigger,
                    )
                )
        clar = None
        # a question outside the corpus has no candidate cell to count
        if THRESHOLD in part.intents and part.domain != OUT_OF_SCOPE:
            domain = part.domain if self.corpus.is_regulated(part.domain) else None
            clar = self.store.resolve(part.slots, domain, documents)
        for spec in self.specs:
            if spec.effect != "explain":
                continue
            hop = getattr(self, f"_{spec.lookup}")(spec, q, part, clar)
            if hop is not None:
                hops.append(hop)
        return hops, clar

    @staticmethod
    def _cue(spec: HopSpec, q: str) -> str | None:
        return next((c for c in spec.cues if c in q), None)

    def _chunk(self, node_id: str | None) -> str | None:
        cid = f"chunk:{node_id}" if node_id else None
        return cid if cid and self.s.get(m.EvidenceChunk, cid) is not None else None

    def _by_label(self, label: str) -> m.EvidenceChunk | None:
        return self.s.execute(
            select(m.EvidenceChunk)
            .where(m.EvidenceChunk.citation_label == label)
            .order_by(m.EvidenceChunk.chunk_id)
            .limit(1)
        ).scalar_one_or_none()

    # ------------------------------------------------------------------ lookups

    def _replacement(self, spec: HopSpec, q: str, part: Any, clar: Any) -> Hop | None:
        """A document of the question that another document of the corpus replaces or repeals."""
        for s in part.slots:
            if s.dimension != "document":
                continue
            rels = [r for r in self.relations if r.target_document_id == s.value]
            rels = [r for r in rels if r.source_document_id in self.store.documents] or rels
            if not rels:
                continue
            rels.sort(key=lambda r: (r.source_node_id is None, r.type != "REPLACES", r.id))
            rel = rels[0]
            hop = Hop(type=spec.type, trigger=s.text, role=spec.role)
            cid = self._chunk(rel.source_node_id)
            if cid is None:
                return hop
            hop.status = "resolved"
            hop.chunk_ids = [cid]
            hop.legal_addresses = [rel.citation_label or ""]
            hop.effect = {
                "slot": "document",
                "from": s.value,
                "to": rel.source_document_id,
                "scope": rel.scope,
            }
            return hop
        return None

    def _default_zone(self, spec: HopSpec, q: str, part: Any, clar: Any) -> Hop | None:
        """The question says the zoning is not determined and states no column: the clause that
        sets the default column of the domain fills it."""
        cue = self._cue(spec, q)
        if cue is None or any(s.dimension == spec.dimension and not s.negated for s in part.slots):
            return None
        hop = Hop(type=spec.type, trigger=cue, role=spec.role)
        for t in self.transitions:
            if t.kind != "default_zone" or self.doc_domain.get(t.document_id) != part.domain:
                continue
            code = _ZONE_IN_TEXT.search(normalize(t.text))
            cid = self._chunk(t.source_node_id)
            if code is None or cid is None:
                continue
            hop.status = "resolved"
            hop.chunk_ids = [cid]
            hop.legal_addresses = [t.citation_label]
            hop.effect = {"slot": spec.dimension or "zone", "value": code.group(1).upper()}
            break
        return hop

    def _zone_definition(self, spec: HopSpec, q: str, part: Any, clar: Any) -> Hop | None:
        """A column the question describes instead of naming: its definition point."""
        for s in part.slots:
            if s.dimension != "zone" or s.negated or s.stated_as != "description":
                continue
            hop = Hop(type=spec.type, trigger=s.text, role=spec.role)
            for label in self.zone_sources.get((part.domain, s.value), []):
                chunk = self._by_label(label)
                if chunk is not None:
                    hop.status = "resolved"
                    hop.chunk_ids = [chunk.chunk_id]
                    hop.legal_addresses = [label]
                    break
            return hop
        return None

    def _roadmap(self, spec: HopSpec, q: str, part: Any, clar: Any) -> Hop | None:
        """A date of the question that a roadmap clause of the domain names, or the wording of a
        facility already in operation: the roadmap clause and the clause it refers to."""
        dates = {s.value for s in part.slots if s.dimension == "date"}
        rules = [
            t
            for t in self.transitions
            if t.kind == "roadmap" and t.dates and self.doc_domain.get(t.document_id) == part.domain
        ]
        dated = [t for t in rules if dates & set(t.dates)]
        cue = self._cue(spec, q)
        if not dated and cue is None:
            return None
        hop = Hop(type=spec.type, trigger=cue or sorted(dates)[0], role=spec.role)
        for t in dated or rules:
            cid = self._chunk(t.source_node_id)
            if cid is None:
                continue
            hop.status = "resolved"
            hop.chunk_ids = [cid]
            hop.legal_addresses = [t.citation_label]
            hop.supporting_chunk_ids = self._referred(t)
            break
        return hop

    def _referred(self, t: m.TransitionRule) -> list[str]:
        """Clauses of the same document the rule refers to ("khoản 1 Điều 3 Thông tư này")."""
        out: list[str] = []
        head = t.citation_label.split(", Điều")[0]
        for clause, article in _REFERENCE.findall(normalize(t.text))[: MAX_CHAIN - 1]:
            chunk = self._by_label(f"{head}, Điều {article}, Khoản {clause}")
            if chunk is None:  # labels of PDF documents end with the page
                chunk = self.s.execute(
                    select(m.EvidenceChunk)
                    .where(
                        m.EvidenceChunk.citation_label.like(
                            f"{head}, Điều {article}, Khoản {clause}, tr.%"
                        )
                    )
                    .order_by(m.EvidenceChunk.chunk_id)
                    .limit(1)
                ).scalar_one_or_none()
            if chunk is not None and chunk.chunk_id not in out:
                out.append(chunk.chunk_id)
        return out

    def _footnote(self, spec: HopSpec, q: str, part: Any, clar: Clarification | None) -> Hop | None:
        """The question asks about what a footnote explains: the footnotes of the cells left."""
        cue = self._cue(spec, q)
        if cue is None or clar is None or not clar.rule_codes:
            return None
        codes = set(clar.rule_codes)
        refs = list(
            dict.fromkeys(
                ref for c in self.store.rules if c.rule_code in codes for ref in c.footnote_refs
            )
        )
        hop = Hop(type=spec.type, trigger=cue, role=spec.role)
        chunks = [cid for cid in (self._chunk(ref) for ref in refs) if cid]
        if chunks:
            hop.status = "resolved"
            hop.chunk_ids = chunks
            hop.legal_addresses = [self.s.get(m.EvidenceChunk, c).citation_label for c in chunks]
        return hop

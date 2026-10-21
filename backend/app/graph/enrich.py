"""Second pass over the projection (schema v1.3): edges that tie prose to the semantic layer.

The first pass (`build_graph.collect`) links table rows to entities; clauses, points and preambles
were reachable through the document structure only. Everything here is deterministic (alias index,
citation labels, section headings, existing relations) and every edge keeps how it was made.

Asserted from the text
  MENTIONS        chunk → Parameter | Zone | FuelType | ReceivingBodyType | IndustrySector |
                  LegalDocument (another document), matched by the alias index of the question side
  REFERS_TO       chunk → Structure, explicit references inside one document ("Bảng 1", "Mục 2.1",
                  "khoản 2 Điều 3", "Phụ lục 4.1")
  USES_TERM       chunk → Definition of the same document whose term the chunk uses
  STATED_IN       LegalDocument → chunk, the clause that states a replace / repeal relation
                  (both documents of the relation)
  INHERITS_FROM   TableRow → TableRow whose value it takes
  HAS_PROVISION   LegalDocument → chunk, `role` from the heading it sits under (scope, subjects,
                  definitions, effectivity, transition, roadmap, implementation, …); a QCVN also
                  gets the effectivity-type provisions of the circular that promulgates it
  ABOUT_TOPIC     MonitoringRequirement → Topic (domain × requirement type)
Derived shortcuts (property `derived: true`), so a typed two-step query replaces a five-hop walk
  TABLE_CONTEXT   Table → chunk: its footnotes and the clauses that refer to it or its appendix
  EFFECTIVE_UNDER Table with limit rules → chunk: transition / roadmap / default-zone clauses and
                  effective-date facts of its document and of the promulgating circular
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db import models as m
from app.graph.ids import zone_id
from app.retrieval.query import AliasIndex

# alias entity type -> (node label, id prefix); units and domains are hubs and say little
MENTION_TARGETS = {
    "parameter": ("Parameter", "param"),
    "fuel": ("FuelType", "fuel"),
    "receiving_body": ("ReceivingBodyType", "rbt"),
    "sector": ("IndustrySector", "sector"),
    "document": ("LegalDocument", "doc"),
}
EFFECTIVE_FACTS = {"effective_date", "partial_effective_date"}
MIN_TERM_CHARS = 8  # shorter defined terms ("Bụi") match everywhere

# kind, the word that opens a reference of that kind, the pattern
_REFS = [
    ("clause", "Điều", re.compile(r"[Kk]hoản\s+(\d+)\s+Điều\s+(\d+)")),
    ("article", "Điều", re.compile(r"Điều\s+(\d+)")),
    ("table", "Bảng", re.compile(r"Bảng\s+(\d+)")),
    ("appendix", "Phụ lục", re.compile(r"Phụ lục\s+(\d+(?:\.\d+)?)")),
    ("section", "[Mm]ục", re.compile(r"[Mm]ục\s+(\d+(?:\.\d+)+)")),
]
_OTHER_TEXT = r"(?:Luật|Nghị định|Thông tư số|Thông tư \d|QCVN \d|TCVN \d)"


def _foreign(after: str, word: str) -> bool:
    """The reference belongs to another legal text: "Điều 111 Luật Bảo vệ môi trường", also as
    the head of a list of the same kind ("Điều 3, Điều 5 Nghị định số 08/2022/NĐ-CP")."""
    more = rf"(?:\s*(?:,|và|hoặc)\s*(?:[Kk]hoản\s+\d+\s+)?{word}\s+[\d.]+)*"
    return re.match(rf"^{more}\s*(?:của\s+)?{_OTHER_TEXT}", after) is not None


_SECTION_KINDS = ("Section", "Clause", "Point", "Definition")


def _ambiguous(surface: str) -> bool:
    """A one-word Vietnamese alias is not evidence of a mention in running text: "hồ" (lake) also
    matches "hồ sơ", "đồng" (copper) "đồng xử lý", "màu" (colour) "hoa màu". Symbols and
    abbreviations (cod, so2, ph) are plain ASCII and stay; so does every multi-word alias."""
    return " " not in surface.strip() and not surface.isascii()


COMMON_VOCABULARY = "grammars/_common.yaml"


def load_vocabulary(data_dir: Path) -> tuple[list[tuple[str, str]], set[str]]:
    """(role, heading) pairs and the roles a regulation inherits from its promulgating circular,
    from `data/grammars/_common.yaml`."""
    raw = yaml.safe_load((data_dir / COMMON_VOCABULARY).read_text("utf-8")) or {}
    roles = [(r["role"], r["heading"].lower()) for r in raw.get("provision_roles") or []]
    return roles, set(raw.get("inherited_roles") or [])


def document_roles(grammar_path: Path | None) -> list[tuple[str, str]]:
    """Role headings a document declares for itself (`provision_roles` in its grammar)."""
    if grammar_path is None or not grammar_path.exists():
        return []
    raw = yaml.safe_load(grammar_path.read_text("utf-8")) or {}
    return [(r["role"], r["heading"].lower()) for r in raw.get("provision_roles") or []]


def _role_of(
    node_id: str, nodes: dict[str, m.StructureNode], patterns: list[tuple[str, str]]
) -> str | None:
    cur = nodes.get(node_id)
    while cur is not None:
        title = (cur.title or "").lower()
        for role, pattern in patterns:
            if pattern in title:
                return role
        cur = nodes.get(cur.parent_id) if cur.parent_id else None
    return None


def _ancestors(node_id: str, nodes: dict[str, m.StructureNode]) -> list[str]:
    out = []
    cur = nodes.get(node_id)
    while cur is not None:
        out.append(cur.node_id)
        cur = nodes.get(cur.parent_id) if cur.parent_id else None
    return out


def reference_targets(
    text: str, document_id: str, index: dict[tuple[str, str, str], str]
) -> list[tuple[str, str]]:
    """(target node id, matched text) for the in-document references of one chunk. `index` maps
    (document, kind, number) to a node id; kind is table | appendix | article | clause | section."""
    out: list[tuple[str, str]] = []
    taken: list[tuple[int, int]] = []
    for kind, word, pattern in _REFS:
        for mt in pattern.finditer(text):
            if any(a <= mt.start() < b for a, b in taken):  # "Điều 3" inside "khoản 2 Điều 3"
                continue
            if _foreign(text[mt.end() :], word):
                continue
            number = f"{mt.group(2)}.{mt.group(1)}" if kind == "clause" else mt.group(1)
            target = index.get((document_id, kind, number))
            if target:
                out.append((target, mt.group(0)))
                taken.append((mt.start(), mt.end()))
    return out


def reference_index(nodes: dict[str, m.StructureNode]) -> dict[tuple[str, str, str], str]:
    index: dict[tuple[str, str, str], str] = {}
    for n in nodes.values():
        if not n.number:
            continue
        if n.kind == "Table":
            index.setdefault((n.document_id, "table", n.number), n.node_id)
        elif n.kind == "Appendix":
            index.setdefault((n.document_id, "appendix", n.number), n.node_id)
        elif n.kind == "Article":
            index.setdefault((n.document_id, "article", n.number), n.node_id)
        elif n.kind in _SECTION_KINDS:
            index.setdefault((n.document_id, "section", n.number), n.node_id)
        if n.kind == "Clause":  # "khoản 2 Điều 3" -> the clause numbered 2 under Article 3
            parent = nodes.get(n.parent_id) if n.parent_id else None
            if parent is not None and parent.kind == "Article" and parent.number:
                key = (n.document_id, "clause", f"{parent.number}.{n.number}")
                index.setdefault(key, n.node_id)
    return index


def enrich(
    p: Any, session: Session, dictionaries: Any, settings: Settings | None = None
) -> dict[str, int]:
    """Add the v1.3 edges to projection `p`; returns the number of edges per type."""
    before = {k: len(v) for k, v in p.edges.items()}
    regulated = [x.code for x in dictionaries.domains]
    data_dir = (settings or get_settings()).data_dir
    common_roles, inherited_roles = load_vocabulary(data_dir)
    docs = {d.document_id: d for d in session.execute(select(m.Document)).scalars()}
    nodes = {n.node_id: n for n in session.execute(select(m.StructureNode)).scalars()}
    chunks = list(session.execute(select(m.EvidenceChunk)).scalars())
    chunk_ids = {c.chunk_id for c in chunks}
    prose = [c for c in chunks if c.chunk_type not in ("table_row", "registry_fact")]
    relations = list(session.execute(select(m.DocumentRelation)).scalars())
    promulgators: dict[str, set[str]] = defaultdict(set)  # QCVN -> circulars that promulgate it
    for r in relations:
        if r.type == "PROMULGATES" and r.target_document_id:
            promulgators[r.target_document_id].add(r.source_document_id)

    # ---- MENTIONS: the alias matcher of the question side, run over the prose
    aliases = AliasIndex(dictionaries, {d.document_id: d.document_code for d in docs.values()})
    for c in prose:
        seen: set[tuple[str, str]] = set()
        for h in aliases.find(c.text):
            if _ambiguous(h.matched):
                continue
            targets: list[tuple[str, str]] = []
            if h.entity_type in MENTION_TARGETS:
                label, prefix = MENTION_TARGETS[h.entity_type]
                if h.entity_type == "document" and h.entity_id == c.document_id:
                    continue  # a document naming itself links every one of its chunks
                targets.append((label, f"{prefix}:{h.entity_id}"))
            elif h.entity_type == "zone":
                # alias ids are "<domain>.<code>" or a bare code read in the domain of the chunk
                dom, _, code = h.entity_id.rpartition(".")
                doms = [dom] if dom else ([c.domain] if c.domain in regulated else [])
                targets += [
                    ("Zone", zone_id(z.scheme_id, z.code))
                    for z in dictionaries.zones
                    if z.domain in doms and z.code == code
                ]
            for label, node_id in targets:
                if (label, node_id) not in seen:
                    seen.add((label, node_id))
                    p.edge(
                        "EvidenceChunk",
                        c.chunk_id,
                        "MENTIONS",
                        label,
                        node_id,
                        method="alias_match",
                        source=c.chunk_id,
                        surface=h.matched,
                    )

    # ---- REFERS_TO: explicit references inside the same document
    ref_index = reference_index(nodes)
    referrers: dict[str, set[str]] = defaultdict(set)  # target node -> chunks that refer to it
    for c in prose:
        own = set(_ancestors(c.source_id, nodes))
        for target, surface in reference_targets(c.text, c.document_id, ref_index):
            if target in own:
                continue
            referrers[target].add(c.chunk_id)
            p.edge(
                "EvidenceChunk",
                c.chunk_id,
                "REFERS_TO",
                "Structure",
                target,
                method="reference_pattern",
                source=c.chunk_id,
                surface=surface,
            )

    # ---- USES_TERM: a chunk using a term its own document defines
    for d in (n for n in nodes.values() if n.kind == "Definition" and n.term):
        term = re.sub(r"\s*\(.*?\)", "", d.term).strip().lower()
        if len(term) < MIN_TERM_CHARS:
            continue
        for c in prose:
            if c.document_id != d.document_id or d.node_id in _ancestors(c.source_id, nodes):
                continue
            if term in c.text.lower():
                p.edge(
                    "EvidenceChunk",
                    c.chunk_id,
                    "USES_TERM",
                    "Structure",
                    d.node_id,
                    method="term_match",
                    source=c.chunk_id,
                    surface=term,
                )

    # ---- STATED_IN: the clause that states a relation, from both documents of the relation
    for r in relations:
        cid = f"chunk:{r.source_node_id}" if r.source_node_id else None
        if cid not in chunk_ids:
            continue
        for doc_id, side in ((r.source_document_id, "source"), (r.target_document_id, "target")):
            if doc_id:
                p.edge(
                    "LegalDocument",
                    f"doc:{doc_id}",
                    "STATED_IN",
                    "EvidenceChunk",
                    cid,
                    method="relation_extraction",
                    source=r.source_node_id,
                    relation=r.type,
                    side=side,
                )

    # ---- INHERITS_FROM, and tables with their rows
    rows = list(session.execute(select(m.TableRow)).scalars())
    for row in rows:
        if row.inherits_from:
            p.edge(
                "TableRow",
                row.row_id,
                "INHERITS_FROM",
                "TableRow",
                row.inherits_from,
                method="table_parser",
                source=row.row_id,
            )

    # ---- HAS_PROVISION: what a document provides, by the heading the chunk sits under
    roles_of = {
        d.document_id: document_roles(data_dir / d.grammar if d.grammar else None) + common_roles
        for d in docs.values()
    }
    provisions: dict[str, list[tuple[str, str]]] = defaultdict(list)  # doc -> (chunk, role)
    for c in chunks:
        if c.chunk_type == "table_row":
            continue
        role = (
            "registry"
            if c.chunk_type == "registry_fact"
            else "preamble"
            if c.chunk_type == "preamble"
            else _role_of(c.source_id, nodes, roles_of[c.document_id])
        )
        if role:
            provisions[c.document_id].append((c.chunk_id, role))
            p.edge(
                "LegalDocument",
                f"doc:{c.document_id}",
                "HAS_PROVISION",
                "EvidenceChunk",
                c.chunk_id,
                method="heading_role",
                source=c.source_id,
                role=role,
            )
    for qcvn, circulars in promulgators.items():
        for circ in sorted(circulars):
            for cid, role in provisions.get(circ, []):
                if role in inherited_roles:
                    p.edge(
                        "LegalDocument",
                        f"doc:{qcvn}",
                        "HAS_PROVISION",
                        "EvidenceChunk",
                        cid,
                        method="derived",
                        source=f"doc:{circ}",
                        role=role,
                        via=circ,
                        derived=True,
                    )

    # ---- ABOUT_TOPIC: monitoring requirements by domain × kind
    for q in session.execute(select(m.MonitoringRequirement)).scalars():
        doms = regulated if q.domain == "both" else [q.domain]
        for dom in doms:
            tid = p.node(
                "Topic", f"topic:{dom}:{q.requirement_type}", domain=dom, kind=q.requirement_type
            )
            p.edge(
                "MonitoringRequirement",
                q.requirement_id,
                "ABOUT_TOPIC",
                "Topic",
                tid,
                method="curated",
                source=q.source_node_id,
            )

    # ---- derived shortcuts on tables
    tables = list(session.execute(select(m.TableDef)).scalars())
    footnotes: dict[str, set[str]] = defaultdict(set)  # table node -> footnote nodes
    for n in nodes.values():
        if n.kind == "Footnote" and n.parent_id:
            footnotes[n.parent_id].add(n.node_id)
    table_node = {t.table_code: t.node_id for t in tables}
    for row in rows:
        footnotes[table_node[row.table_code]].update(row.footnote_refs)
    limit_tables = set(session.execute(select(m.LimitRule.table_code).distinct()).scalars())
    transitions: dict[str, list[tuple[str, str]]] = defaultdict(list)  # doc -> (chunk, kind)
    for t in session.execute(select(m.TransitionRule)).scalars():
        if f"chunk:{t.source_node_id}" in chunk_ids:
            transitions[t.document_id].append((f"chunk:{t.source_node_id}", t.kind))
    for c in chunks:
        if c.chunk_type == "registry_fact" and c.source_id.rsplit(":", 1)[-1] in EFFECTIVE_FACTS:
            transitions[c.document_id].append((c.chunk_id, "registry_fact"))
    for t in tables:
        for fn in sorted(footnotes.get(t.node_id, ())):
            if f"chunk:{fn}" in chunk_ids:
                p.edge(
                    "Structure",
                    t.node_id,
                    "TABLE_CONTEXT",
                    "EvidenceChunk",
                    f"chunk:{fn}",
                    method="derived",
                    source=fn,
                    kind="footnote",
                    derived=True,
                )
        scope = [a for a in _ancestors(t.node_id, nodes) if nodes[a].kind in ("Table", "Appendix")]
        for cid in sorted({c for a in scope for c in referrers.get(a, ())}):
            p.edge(
                "Structure",
                t.node_id,
                "TABLE_CONTEXT",
                "EvidenceChunk",
                cid,
                method="derived",
                source=cid,
                kind="reference",
                derived=True,
            )
        if t.table_code in limit_tables:
            for doc_id in [t.document_id, *sorted(promulgators.get(t.document_id, ()))]:
                for cid, kind in transitions.get(doc_id, []):
                    p.edge(
                        "Structure",
                        t.node_id,
                        "EFFECTIVE_UNDER",
                        "EvidenceChunk",
                        cid,
                        method="derived",
                        source=f"doc:{doc_id}",
                        kind=kind,
                        derived=True,
                    )
    added: dict[str, int] = defaultdict(int)
    for key, edges in p.edges.items():
        if key[1] in NEW_RELS:
            added[key[1]] += len(edges) - before.get(key, 0)
    return dict(added)


NEW_RELS = (
    "MENTIONS",
    "REFERS_TO",
    "USES_TERM",
    "STATED_IN",
    "INHERITS_FROM",
    "HAS_PROVISION",
    "ABOUT_TOPIC",
    "TABLE_CONTEXT",
    "EFFECTIVE_UNDER",
)

"""Neo4j projection of the PostgreSQL system of record (ADR-0002, PLAN.md §4.2, ontology v1).

Idempotent: every node/edge is MERGEd on a unique `id`, stamped with `projection_version`, and
anything left with an older stamp is deleted afterwards. Rebuild any time with
`python backend/scripts/build_graph.py`.

Labels (all nodes also carry `id`):
  LegalDocument(+QCVN|Circular|ExternalStub), DocumentVersion, RegistryFact,
  Structure(+kind: Chapter, Section, Article, Clause, Point, Appendix, Table, Footnote, Definition …),
  TableRow, EvidenceChunk, LimitRule, TransitionRule, MonitoringRequirement, Condition, Exception,
  Parameter, Unit, Zone, IndustrySector, EquipmentType, FuelType, ReceivingBodyType,
  EnvironmentalDomain, Method, ApplicabilityRule.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections import defaultdict
from typing import Any

from neo4j import GraphDatabase
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.applicability.rules import load_rules, resolve_evidence
from app.core.config import Settings, get_settings
from app.db import models as m
from app.graph import vectors
from app.graph.enrich import enrich
from app.graph.ids import document_version_id, registry_fact_id, zone_id
from app.ontology.loader import load_dictionaries

LABELS = [
    "LegalDocument",
    "DocumentVersion",
    "RegistryFact",
    "Structure",
    "TableRow",
    "EvidenceChunk",
    "LimitRule",
    "TransitionRule",
    "MonitoringRequirement",
    "Condition",
    "MonitoringCondition",
    "Exception",
    "Parameter",
    "Unit",
    "Zone",
    "IndustrySector",
    "EquipmentType",
    "FuelType",
    "ReceivingBodyType",
    "EnvironmentalDomain",
    "Method",
    "ApplicabilityRule",
    "Topic",
]
SCHEMA_VERSION = "1.4"
# schema v1.4: second label of every node that is a citable statement of the fact store
FACT_LABEL = "Fact"
# fact store table -> label of the node that already carries the statement
FACT_NODE_LABELS = {
    "limit_rules": "LimitRule",
    "structure_nodes": "Structure",
    "transition_rules": "TransitionRule",
    "monitoring_requirements": "MonitoringRequirement",
    "registry_facts": "RegistryFact",
}
BATCH = 500
# conditions of a LimitRule come from `rule_conditions`, on the dimensions declared in
# data/dictionaries/condition_dimensions.yaml (an undeclared dimension stops the build). A dimension
# with a `linked_entity` (zone, equipment) is not a Condition node: it is the IN_ZONE / APPLIES_TO
# edge to its dictionary entity
# how an edge was produced (every edge carries `method` and `source`, the record it was read from)
EDGE_METHODS = {
    "dictionary",  # data/dictionaries/*.yaml
    "registry",  # data/registry/documents.yaml
    "structure_parser",
    "table_parser",
    "entity_resolution",  # row cell -> dictionary id
    "rule_extraction",  # TableRow × column -> LimitRule
    "relation_extraction",  # replace / repeal / transition clauses
    "chunker",
    "citation_label",  # dictionary `sources` label -> chunk
    "curated",  # hand-written YAML (monitoring map, applicability rules)
    # schema v1.3 (app.graph.enrich)
    "alias_match",  # alias index over the chunk text
    "reference_pattern",  # "Bảng 1", "khoản 2 Điều 3" resolved inside the document
    "term_match",  # defined term used by a chunk
    "heading_role",  # role of a provision from the heading above it
    "derived",  # shortcut computed from other edges (also carries derived=true)
    # schema v1.4
    "fact_layer",  # the `facts` table: statement -> the chunk it is cited from
}


def _h(obj: Any) -> str:
    return hashlib.sha1(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[
        :16
    ]


class Projection:
    """Collects nodes/edges in memory, then writes them in batches."""

    def __init__(self) -> None:
        self.nodes: dict[str, list[dict[str, Any]]] = defaultdict(list)  # label -> rows
        self.edges: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
        self.vectors: dict[str, list[float]] = {}  # chunk_id -> embedding (app.graph.vectors)
        self._seen: set[str] = set()
        self._rows: dict[str, dict[str, Any]] = {}  # node id -> its row
        self._label: dict[str, str] = {}  # node id -> primary label
        # (a, rel, b) -> facts stated by that edge (a method of a row, a relation of two documents)
        self.edge_facts: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
        self.fact_stats: dict[str, Any] = {}
        self.edge_stats: dict[str, Any] = {}

    def node(
        self, _label: str, _id: str, extra_labels: list[str] | None = None, **props: Any
    ) -> str:
        # positional names are underscored so a property called `label` (structure point letter)
        # or `id` never collides with them
        if _id not in self._seen:
            self._seen.add(_id)
            clean = {k: v for k, v in props.items() if v is not None}
            row = {"id": _id, "labels": list(extra_labels or []), "props": clean}
            self.nodes[_label].append(row)
            self._rows[_id] = row
            self._label[_id] = _label
        return _id

    def mark(self, _id: str, _label: str, **props: Any) -> None:
        """Add a label and properties to a node that already exists."""
        row = self._rows[_id]
        if _label not in row["labels"]:
            row["labels"].append(_label)
        row["props"].update({k: v for k, v in props.items() if v is not None})

    def has(self, _id: str, _label: str | None = None) -> bool:
        return _id in self._rows and (_label is None or self._label[_id] == _label)

    def finalize(self) -> dict[str, int]:
        """Make the edge lists writable as one relationship per (source, type, target).

        Several records may state the same edge (two tables give the same method for a
        parameter). The relationship keeps the properties of the first record in a fixed order,
        plus `sources` (every record) — so the result does not depend on the order the database
        returns rows in and no provenance is dropped. An edge whose endpoint does not exist cannot
        be written (MATCH would skip it silently): it is left out and listed in `dangling`."""
        stats: dict[str, Any] = {"records": 0, "edges": 0, "merged": 0, "dangling": []}
        for (la, rel, lb), rows in self.edges.items():
            groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
            for r in rows:
                missing = [
                    f"{label} {node_id}"
                    for label, node_id in ((la, r["a"]), (lb, r["b"]))
                    if not self.has(node_id, label)
                ]
                if missing:
                    stats["dangling"].append(f"{rel} {r['a']} -> {r['b']}: no {missing[0]}")
                    continue
                groups[(r["a"], r["b"])].append(r["props"])
            out = []
            for (a, b), props in sorted(groups.items()):
                props.sort(key=lambda x: json.dumps(x, sort_keys=True, ensure_ascii=False))
                first = dict(props[0])
                sources = sorted({x["source"] for x in props})
                if len(props) > 1:
                    first["sources"] = sources
                    first["n_records"] = len(props)
                    stats["merged"] += len(props) - 1
                facts = sorted(self.edge_facts.get((a, rel, b), ()), key=lambda f: f["fact_id"])
                if facts:
                    first.update(_edge_fact_props(facts))
                out.append({"a": a, "b": b, "props": first})
            stats["records"] += len(rows)
            stats["edges"] += len(out)
            self.edges[(la, rel, lb)] = out
        return stats

    def edge(
        self,
        a_label: str,
        a: str,
        rel: str,
        b_label: str,
        b: str,
        *,
        method: str,
        source: str,
        **props: Any,
    ) -> None:
        """`method` (one of EDGE_METHODS) and `source` (id of the row / node / file the edge was
        read from) are mandatory: no edge without provenance."""
        if method not in EDGE_METHODS or not source:
            raise ValueError(f"{rel} {a} -> {b}: edge needs a known method and a source")
        clean = {k: v for k, v in props.items() if v is not None}
        self.edges[(a_label, rel, b_label)].append(
            {"a": a, "b": b, "props": clean | {"method": method, "source": source}}
        )


TRUST_ORDER = ("pending", "auto_verified", "approved")  # weakest first


def _fact_props(f: m.Fact) -> dict[str, Any]:
    """Properties every statement of the fact store carries in the graph, node or edge."""
    return {
        "fact_id": f.fact_id,
        # `fact_kind`, not fact_type: a RegistryFact node already has a fact_type of its own
        "fact_kind": f.fact_type,
        "trust": f.trust,
        "valid_from": f.valid_from.isoformat() if f.valid_from else None,
        "valid_to": f.valid_to.isoformat() if f.valid_to else None,
        # the legal address is the location of the statement; the label reads it out (and may go
        # on with the page, which is additional information)
        "address": json.dumps(f.address, ensure_ascii=False),
        "address_label": f.address_label,
        "fact_chunk_id": f.chunk_id,
        "fact_method": f.method,
    }


def _edge_fact_props(facts: list[dict[str, Any]]) -> dict[str, Any]:
    """One relationship may state several facts (a method given for two stages of the same row):
    it lists them all and carries the weakest trust and the common validity."""
    trust = min((f["trust"] for f in facts), key=TRUST_ORDER.index)
    starts = sorted({f["valid_from"] for f in facts if f["valid_from"]})
    ends = sorted({f["valid_to"] for f in facts if f["valid_to"]})
    out = {
        "fact_ids": [f["fact_id"] for f in facts],
        "fact_kind": facts[0]["fact_kind"],
        "trust": trust,
        "valid_from": starts[-1] if starts else None,
        "valid_to": ends[0] if ends else None,
        "address": facts[0]["address"],
        "address_label": facts[0]["address_label"],
        "fact_chunk_id": facts[0]["fact_chunk_id"],
    }
    return {k: v for k, v in out.items() if v is not None}


def collect(session: Session, settings: Settings) -> Projection:
    p = Projection()
    d = load_dictionaries(settings=settings)

    # ---- dictionaries
    for dom in d.domains:
        p.node("EnvironmentalDomain", f"domain:{dom.code}", code=dom.code, name=dom.name)
    for u in d.units:
        p.node(
            "Unit",
            f"unit:{u.unit_id}",
            unit_id=u.unit_id,
            name=u.name,
            symbol=u.symbol,
            kind=u.kind,
        )
    for z in d.zones:
        p.node(
            "Zone",
            zone_id(z.scheme_id, z.code),
            domain=z.domain,
            scheme=z.scheme_id,
            code=z.code,
            name=z.name,
            definition=z.definition,
        )
    for r in d.receiving_body_types:
        p.node("ReceivingBodyType", f"rbt:{r.code}", code=r.code, name=r.name)
    for f in d.fuel_types:
        p.node("FuelType", f"fuel:{f.code}", code=f.code, name=f.name)
    for pa in d.parameters:
        p.node(
            "Parameter",
            f"param:{pa.parameter_id}",
            parameter_id=pa.parameter_id,
            name=pa.name,
            symbol=pa.symbol,
            formula=pa.formula,
            domains=list(pa.domains),
            group_id=pa.group_id,
            phase=pa.phase,
        )
        if pa.default_unit_id:
            p.edge(
                "Parameter",
                f"param:{pa.parameter_id}",
                "HAS_UNIT",
                "Unit",
                f"unit:{pa.default_unit_id}",
                default=True,
                method="dictionary",
                source="dictionaries/parameters.yaml",
            )
    for s in d.industry_sectors:
        p.node(
            "IndustrySector",
            f"sector:{s.sector_id}",
            sector_id=s.sector_id,
            tt=s.tt,
            name=s.name,
            vsic_codes=list(s.vsic_codes),
            group_roman=s.group_roman,
        )
    for e in d.equipment_types:
        p.node(
            "EquipmentType",
            f"equip:{e.equipment_id}",
            equipment_id=e.equipment_id,
            name=e.name,
            path=".".join(e.path),
            fuel_type=e.fuel_type,
            capacity=e.capacity_condition_raw,
            is_group_header=e.is_group_header,
        )
        if e.parent_id:
            p.edge(
                "EquipmentType",
                f"equip:{e.parent_id}",
                "PARENT_OF",
                "EquipmentType",
                f"equip:{e.equipment_id}",
                method="dictionary",
                source="dictionaries/equipment_types.yaml",
            )
        if e.fuel_type:
            p.edge(
                "EquipmentType",
                f"equip:{e.equipment_id}",
                "USES_FUEL",
                "FuelType",
                f"fuel:{e.fuel_type}",
                method="dictionary",
                source="dictionaries/equipment_types.yaml",
            )

    # ---- documents, versions, facts, relations
    regulated = {x.code for x in d.domains}
    docs = list(session.execute(select(m.Document)).scalars())
    for doc in docs:
        label2 = {"qcvn": "QCVN", "circular": "Circular", "external_stub": "ExternalStub"}.get(
            doc.document_type, "Other"
        )
        p.node(
            "LegalDocument",
            f"doc:{doc.document_id}",
            [label2],
            document_id=doc.document_id,
            document_code=doc.document_code,
            title=doc.title,
            domain=doc.domain,
            text_status=doc.text_status,
            document_type=doc.document_type,
        )
        if doc.document_type == "qcvn" and doc.domain in regulated:
            p.edge(
                "LegalDocument",
                f"doc:{doc.document_id}",
                "GOVERNS_DOMAIN",
                "EnvironmentalDomain",
                f"domain:{doc.domain}",
                method="registry",
                source="registry/documents.yaml",
            )
    for v in session.execute(select(m.DocumentVersion)).scalars():
        p.node(
            "DocumentVersion",
            document_version_id(v.document_id, v.sha256),
            file_name=v.file_name,
            format=v.format,
            sha256=v.sha256,
            page_count=v.page_count,
            is_current=v.is_current,
        )
        p.edge(
            "LegalDocument",
            f"doc:{v.document_id}",
            "HAS_VERSION",
            "DocumentVersion",
            document_version_id(v.document_id, v.sha256),
            method="registry",
            source="registry/documents.yaml",
        )
    for f in session.execute(select(m.RegistryFact)).scalars():
        if p.has(registry_fact_id(f.document_id, f.fact_type)):
            raise ValueError(f"registry: two {f.fact_type} facts for {f.document_id}")
        p.node(
            "RegistryFact",
            registry_fact_id(f.document_id, f.fact_type),
            document_id=f.document_id,
            fact_type=f.fact_type,
            value=f.value,
            source=f.source,
            evidence=f.evidence,
            scope=f.scope,
            declared_by=f.declared_by,
            declared_at=f.declared_at.isoformat() if f.declared_at else None,
        )
        p.edge(
            "LegalDocument",
            f"doc:{f.document_id}",
            "HAS_REGISTRY_FACT",
            "RegistryFact",
            registry_fact_id(f.document_id, f.fact_type),
            method="registry",
            source="registry/documents.yaml",
        )
    for r in session.execute(select(m.DocumentRelation)).scalars():
        if r.target_document_id:
            p.edge(
                "LegalDocument",
                f"doc:{r.source_document_id}",
                r.type,
                "LegalDocument",
                f"doc:{r.target_document_id}",
                scope=r.scope,
                target_part=r.target_part,
                condition=r.condition,
                origin=r.origin,
                evidence=r.evidence,
                citation_label=r.citation_label,
                method="relation_extraction" if r.origin == "text" else "registry",
                source=r.source_node_id or "registry/documents.yaml",
            )

    # ---- structure
    nodes = list(session.execute(select(m.StructureNode).order_by(m.StructureNode.order)).scalars())
    for n in nodes:
        if n.kind == "Boilerplate":
            continue
        p.node(
            "Structure",
            n.node_id,
            [n.kind],
            kind=n.kind,
            document_id=n.document_id,
            number=n.number,
            label=n.label,
            title=n.title,
            term=n.term,
            citation_label=n.citation_label,
            scope=n.scope,
            domain=n.domain,
            order=n.order,
            depth=n.depth,
            line_start=n.line_start,
            line_end=n.line_end,
            pdf_page_start=n.pdf_page_start,
            printed_page=n.printed_page,
            text=(n.text[:2000] if n.text else None),
        )
        if n.parent_id:
            p.edge(
                "Structure",
                n.parent_id,
                "CONTAINS",
                "Structure",
                n.node_id,
                order=n.order,
                method="structure_parser",
                source=n.node_id,
            )
        else:
            p.edge(
                "LegalDocument",
                f"doc:{n.document_id}",
                "CONTAINS",
                "Structure",
                n.node_id,
                order=n.order,
                method="structure_parser",
                source=n.node_id,
            )
        if n.kind == "Definition":
            p.edge(
                "LegalDocument",
                f"doc:{n.document_id}",
                "HAS_DEFINITION",
                "Structure",
                n.node_id,
                method="structure_parser",
                source=n.node_id,
            )

    # ---- tables & rows
    tables = {t.table_code: t for t in session.execute(select(m.TableDef)).scalars()}
    for row in session.execute(select(m.TableRow)).scalars():
        t = tables[row.table_code]
        p.node(
            "TableRow",
            row.row_id,
            table_code=row.table_code,
            document_id=row.document_id,
            row_no=row.row_no,
            row_path=list(row.row_path),
            is_group_header=row.is_group_header,
            inherits_from=row.inherits_from,
            parameter=row.cells.get("parameter"),
            citation_label=row.citation_label,
            cells=json.dumps(row.cells, ensure_ascii=False),
            line_start=row.line_start,
            printed_page=row.printed_page,
        )
        p.edge(
            "Structure",
            t.node_id,
            "CONTAINS",
            "TableRow",
            row.row_id,
            method="table_parser",
            source=row.row_id,
        )
        pid = row.entities.get("parameter_id") if isinstance(row.entities, dict) else None
        if pid:
            p.edge(
                "TableRow",
                row.row_id,
                "FOR_PARAMETER",
                "Parameter",
                f"param:{pid}",
                method="entity_resolution",
                source=row.row_id,
            )
        for fn in row.footnote_refs:
            p.edge(
                "TableRow",
                row.row_id,
                "QUALIFIED_BY",
                "Structure",
                fn,
                method="table_parser",
                source=row.row_id,
            )
        for meth in row.methods:
            mid = p.node("Method", f"method:{meth['code']}", code=meth["code"])
            p.edge(
                "TableRow",
                row.row_id,
                "SPECIFIES_METHOD",
                "Method",
                mid,
                order=meth["order"],
                stage=meth["stage"],
                method="table_parser",
                source=row.row_id,
            )
            if pid:
                p.edge(
                    "Parameter",
                    f"param:{pid}",
                    "HAS_METHOD",
                    "Method",
                    mid,
                    order=meth["order"],
                    stage=meth["stage"],
                    document_id=row.document_id,
                    row_id=row.row_id,
                    method="table_parser",
                    source=row.row_id,
                )
        sid = row.entities.get("sector_id") if isinstance(row.entities, dict) else None
        if sid:
            for cp in row.entities.get("characteristic_parameter_ids") or []:
                p.edge(
                    "IndustrySector",
                    f"sector:{sid}",
                    "HAS_CHARACTERISTIC_PARAMETER",
                    "Parameter",
                    f"param:{cp}",
                    row_id=row.row_id,
                    method="entity_resolution",
                    source=row.row_id,
                )
            for cond in row.entities.get("conditional_parameter_ids") or []:
                for cp in cond.get("parameter_ids") or []:
                    p.edge(
                        "IndustrySector",
                        f"sector:{sid}",
                        "HAS_CHARACTERISTIC_PARAMETER",
                        "Parameter",
                        f"param:{cp}",
                        condition_text=cond.get("condition_text") or "theo thực tế",
                        row_id=row.row_id,
                        method="entity_resolution",
                        source=row.row_id,
                    )

    # ---- limit rules
    dimensions = {x.name: x for x in d.condition_dimensions}
    conditions: dict[str, list[m.RuleCondition]] = defaultdict(list)
    for rc in session.execute(
        select(m.RuleCondition).order_by(m.RuleCondition.rule_code, m.RuleCondition.position)
    ).scalars():
        if rc.dimension not in dimensions:
            raise ValueError(
                f"{rc.rule_code}: condition dimension {rc.dimension!r} is not declared in "
                "data/dictionaries/condition_dimensions.yaml"
            )
        conditions[rc.rule_code].append(rc)
    for r in session.execute(select(m.LimitRule)).scalars():
        p.node(
            "LimitRule",
            r.rule_code,
            document_id=r.document_id,
            table_code=r.table_code,
            row_id=r.row_id,
            parameter_id=r.parameter_id,
            zone=r.zone,
            zone_scheme=r.zone_scheme,
            column_key=r.column_key,
            cell_raw=r.cell_raw,
            op=r.op,
            value=r.value,
            min=r.min,
            max=r.max,
            unit_id=r.unit_id,
            ref_o2=r.ref_o2,
            citation_label=r.citation_label,
            review_status=r.review_status,
            domain=r.domain,
        )
        p.edge(
            "LegalDocument",
            f"doc:{r.document_id}",
            "HAS_LIMIT_RULE",
            "LimitRule",
            r.rule_code,
            method="rule_extraction",
            source=r.row_id,
        )
        p.edge(
            "LimitRule",
            r.rule_code,
            "DEFINED_BY_TABLE_ROW",
            "TableRow",
            r.row_id,
            method="rule_extraction",
            source=r.row_id,
        )
        p.edge(
            "LimitRule",
            r.rule_code,
            "HAS_EVIDENCE",
            "EvidenceChunk",
            f"chunk:{r.row_id}",
            role="limit",
            method="rule_extraction",
            source=r.row_id,
        )
        if r.parameter_id:
            p.edge(
                "LimitRule",
                r.rule_code,
                "FOR_PARAMETER",
                "Parameter",
                f"param:{r.parameter_id}",
                method="rule_extraction",
                source=r.row_id,
            )
        if r.unit_id:
            p.edge(
                "LimitRule",
                r.rule_code,
                "IN_UNIT",
                "Unit",
                f"unit:{r.unit_id}",
                method="rule_extraction",
                source=r.row_id,
            )
        if r.zone:  # tables without a zone axis have no zone
            p.edge(
                "LimitRule",
                r.rule_code,
                "IN_ZONE",
                "Zone",
                zone_id(r.zone_scheme or r.domain, r.zone),
                method="rule_extraction",
                source=r.row_id,
            )
        if r.equipment_type_id:
            p.edge(
                "LimitRule",
                r.rule_code,
                "APPLIES_TO",
                "EquipmentType",
                f"equip:{r.equipment_type_id}",
                method="rule_extraction",
                source=r.row_id,
            )
        for rc in conditions.get(r.rule_code, ()):
            if dimensions[rc.dimension].linked_entity:
                continue  # the edge to the dictionary entity above
            value: Any = rc.value_num if rc.value_num is not None else rc.value_text
            if dimensions[rc.dimension].value_kind == "flag":
                value = rc.value_text == "true"
            key = {
                "dimension": rc.dimension,
                "op": rc.op,
                "value": value,
                "unit": rc.unit,
                "text": rc.raw_text,
                "scope": rc.scope,
            }
            cid = p.node(
                "Condition",
                f"cond:{rc.dimension}:{_h(key)}",
                dimension=rc.dimension,
                type=rc.dimension,
                op=rc.op,
                value=value,
                unit=rc.unit,
                text=rc.raw_text,
                scope=rc.scope,
            )
            p.edge(
                "LimitRule",
                r.rule_code,
                "APPLIES_WHEN",
                "Condition",
                cid,
                inherited_from=rc.inherited_from,
                method="rule_extraction",
                source=r.row_id,
            )
        for ex in r.exceptions:
            eid = p.node("Exception", f"exc:{_h(ex)}", text=ex)
            p.edge(
                "LimitRule",
                r.rule_code,
                "EXCEPT_WHEN",
                "Exception",
                eid,
                method="rule_extraction",
                source=r.row_id,
            )

    # ---- transition rules, monitoring requirements
    for t in session.execute(select(m.TransitionRule)).scalars():
        p.node(
            "TransitionRule",
            t.rule_id,
            document_id=t.document_id,
            kind=t.kind,
            text=t.text,
            dates=list(t.dates),
            relative_dates=list(t.relative_dates),
            citation_label=t.citation_label,
        )
        p.edge(
            "LegalDocument",
            f"doc:{t.document_id}",
            "HAS_TRANSITION_RULE",
            "TransitionRule",
            t.rule_id,
            method="relation_extraction",
            source=t.source_node_id,
        )
        p.edge(
            "TransitionRule",
            t.rule_id,
            "HAS_EVIDENCE",
            "EvidenceChunk",
            f"chunk:{t.source_node_id}",
            role="transition",
            method="relation_extraction",
            source=t.source_node_id,
        )
    for q in session.execute(select(m.MonitoringRequirement)).scalars():
        p.node(
            "MonitoringRequirement",
            q.requirement_id,
            document_id=q.document_id,
            requirement_type=q.requirement_type,
            domain=q.domain,
            summary=q.summary,
            text=q.text,
            parameters=list(q.parameters),
            references=list(q.references),
            citation_label=q.citation_label,
            review_status=q.review_status,
        )
        p.edge(
            "LegalDocument",
            f"doc:{q.document_id}",
            "HAS_MONITORING_REQUIREMENT",
            "MonitoringRequirement",
            q.requirement_id,
            method="curated",
            source=q.source_node_id,
        )
        p.edge(
            "MonitoringRequirement",
            q.requirement_id,
            "HAS_EVIDENCE",
            "EvidenceChunk",
            q.evidence_chunk_id,
            role="monitoring",
            method="curated",
            source=q.source_node_id,
        )
        for pid in q.parameters:
            p.edge(
                "MonitoringRequirement",
                q.requirement_id,
                "FOR_PARAMETER",
                "Parameter",
                f"param:{pid}",
                method="curated",
                source=q.source_node_id,
            )
        for c in q.conditions:
            key = {k: c.get(k) for k in ("type", "op", "value", "unit", "text")}
            cid = p.node(
                "MonitoringCondition",
                f"mcond:{_h(key)}",
                type=key["type"],
                op=key["op"],
                value=json.dumps(key["value"], ensure_ascii=False)
                if not isinstance(key["value"], str | int | float | bool) or key["value"] is None
                else key["value"],
                unit=key["unit"],
                text=key["text"],
            )
            p.edge(
                "MonitoringRequirement",
                q.requirement_id,
                "APPLIES_WHEN",
                "MonitoringCondition",
                cid,
                method="curated",
                source=q.source_node_id,
            )

    # ---- evidence chunks: full text + embedding, so the graph can serve vector search itself
    chunk_labels: list[tuple[str, str]] = []
    for c in session.execute(select(m.EvidenceChunk)).scalars():
        chunk_labels.append((c.citation_label, c.chunk_id))
        p.node(
            "EvidenceChunk",
            c.chunk_id,
            document_id=c.document_id,
            chunk_type=c.chunk_type,
            domain=c.domain,
            scope=c.scope,
            citation_label=c.citation_label,
            text=c.text,
            context_text=c.context_text,
            text_status=c.text_status,
            line_start=c.line_start,
            printed_page=c.printed_page,
            embedding_model=c.embedding_model if c.embedding is not None else None,
        )
        if c.embedding is not None:
            p.vectors[c.chunk_id] = [float(x) for x in c.embedding]
        p.edge(
            "LegalDocument",
            f"doc:{c.document_id}",
            "HAS_EVIDENCE",
            "EvidenceChunk",
            c.chunk_id,
            role="document",
            method="chunker",
            source=c.source_id,
        )
        if c.chunk_type == "table_row":
            p.edge(
                "EvidenceChunk",
                c.chunk_id,
                "EXTRACTED_FROM",
                "TableRow",
                c.source_id,
                method="chunker",
                source=c.source_id,
            )
        elif c.chunk_type == "registry_fact":
            pass
        else:
            p.edge(
                "EvidenceChunk",
                c.chunk_id,
                "EXTRACTED_FROM",
                "Structure",
                c.source_id,
                method="chunker",
                source=c.source_id,
            )
    for f in session.execute(select(m.RegistryFact)).scalars():  # registry fact -> its chunk
        for c in session.execute(
            select(m.EvidenceChunk).where(
                m.EvidenceChunk.chunk_type == "registry_fact",
                m.EvidenceChunk.source_id == f"{f.document_id}:registry_fact:{f.fact_type}",
            )
        ).scalars():
            p.edge(
                "RegistryFact",
                registry_fact_id(f.document_id, f.fact_type),
                "HAS_EVIDENCE",
                "EvidenceChunk",
                c.chunk_id,
                role="registry_fact",
                method="chunker",
                source=c.source_id,
            )

    # ---- ontology grounding (schema v1.1, plan 2026-09-29): every dictionary record names the
    # clauses its definition comes from (`sources`); link it to those evidence chunks so graph
    # walks reach e.g. the definition of "Cột B" or the default-zone clause from the Zone node
    for label, key, recs in (
        ("Zone", lambda x: zone_id(x.scheme_id, x.code), d.zones),
        ("Parameter", lambda x: f"param:{x.parameter_id}", d.parameters),
        ("IndustrySector", lambda x: f"sector:{x.sector_id}", d.industry_sectors),
        ("EquipmentType", lambda x: f"equip:{x.equipment_id}", d.equipment_types),
        ("FuelType", lambda x: f"fuel:{x.code}", d.fuel_types),
        ("ReceivingBodyType", lambda x: f"rbt:{x.code}", d.receiving_body_types),
        ("Unit", lambda x: f"unit:{x.unit_id}", d.units),
    ):
        for rec in recs:
            for cid in {cid for src in rec.sources for cid in chunks_for_label(src, chunk_labels)}:
                p.edge(
                    label,
                    key(rec),
                    "HAS_EVIDENCE",
                    "EvidenceChunk",
                    cid,
                    role="definition",
                    method="citation_label",
                    source=f"dictionaries:{label}",
                )

    # ---- applicability rules (YAML) with their evidence
    rules_path = settings.data_dir / "rules" / "applicability_v1.yaml"
    if rules_path.exists():
        rs = load_rules(rules_path)
        resolve_evidence(rs, session)
        for r in rs.rules:
            p.node(
                "ApplicabilityRule",
                f"arule:{r.id}",
                rule_code=r.id,
                domain=r.domain,
                title=r.title,
                action=r.action,
                message=r.message,
                version=rs.version,
            )
            for ev in r.evidence_resolved:
                p.edge(
                    "ApplicabilityRule",
                    f"arule:{r.id}",
                    "HAS_EVIDENCE",
                    "EvidenceChunk",
                    ev.chunk_id,
                    role="support",
                    method="curated",
                    source="rules/applicability_v1.yaml",
                )
    enrich(p, session, d, settings)  # schema v1.3: prose ↔ entities, provisions, derived shortcuts
    p.fact_stats = project_facts(p, session)  # schema v1.4
    p.edge_stats = p.finalize()
    return p


def project_facts(p: Projection, session: Session) -> dict[str, Any]:
    """Schema v1.4: the statements of the fact store on the nodes and edges that already carry
    them. A statement is never a second node: the LimitRule *is* the limit fact. Node statements
    get the label `Fact`, the fact properties and one FACT_STATED_IN edge to the chunk they are
    cited from; a method of a row and a relation between two documents are edges, and the edge
    lists the facts it states."""
    stats: dict[str, Any] = {"nodes": 0, "edges": 0, "edge_facts": 0, "unplaced": []}
    edges_with_facts: set[tuple[str, str, str]] = set()
    for f in session.execute(select(m.Fact).order_by(m.Fact.fact_id)).scalars():
        props = _fact_props(f)
        label = FACT_NODE_LABELS.get(f.ref_table)
        if label:
            node_id = (
                registry_fact_id(*f.ref_id.split(":", 1)) if label == "RegistryFact" else f.ref_id
            )
            if not p.has(node_id, label) or not p.has(f.chunk_id, "EvidenceChunk"):
                stats["unplaced"].append(f.fact_id)
                continue
            if p._rows[node_id]["props"].get("fact_id"):
                raise ValueError(f"{node_id}: two facts on one node ({f.fact_id})")
            p.mark(node_id, FACT_LABEL, **props)
            p.edge(
                label,
                node_id,
                "FACT_STATED_IN",
                "EvidenceChunk",
                f.chunk_id,
                fact_kind=f.fact_type,
                method="fact_layer",
                source=f.fact_id,
            )
            stats["nodes"] += 1
            continue
        if f.ref_table == "table_rows":  # a method of a table row
            key = (f.ref_id, "SPECIFIES_METHOD", f"method:{f.locator}")
        else:  # document_relations: "<source>:<type>:<target>"
            source, rel, target = f.ref_id.split(":", 2)
            key = (f"doc:{source}", rel, f"doc:{target}")
        if not (p.has(key[0]) and p.has(key[2])):
            stats["unplaced"].append(f.fact_id)
            continue
        p.edge_facts[key].append(props)
        edges_with_facts.add(key)
        stats["edge_facts"] += 1
    # a fact on an edge needs that edge to be in the projection
    present = {(r["a"], rel, r["b"]) for (_, rel, _), rows in p.edges.items() for r in rows}
    for key in sorted(edges_with_facts - present):
        for props in p.edge_facts.pop(key):
            stats["unplaced"].append(props["fact_id"])
            stats["edge_facts"] -= 1
    stats["edges"] = len(edges_with_facts & present)
    return stats


def chunks_for_label(source: str, chunk_labels: list[tuple[str, str]]) -> list[str]:
    """Chunks whose citation label is `source` or extends it by further locators (", tr. 2",
    ", điểm a") — never a sibling number ("Mục 1.3.1" must not match "Mục 1.3.13")."""
    src = source.strip()
    return [cid for lbl, cid in chunk_labels if lbl == src or lbl.startswith(src + ",")]


def write(
    p: Projection,
    settings: Settings,
    version: str,
    database: str = "neo4j",
    index_label: str | None = None,
    source_database: str | None = None,
    source_revision: str | None = None,
) -> dict[str, int]:
    stats: dict[str, int] = {"nodes": 0, "edges": 0, "deleted": 0, "vectors": 0}
    with GraphDatabase.driver(
        settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
    ) as driver:
        with driver.session(database=database) as s:
            for label in LABELS:
                s.run(
                    f"CREATE CONSTRAINT {label.lower()}_id IF NOT EXISTS FOR (n:`{label}`) REQUIRE n.id IS UNIQUE"
                )
            s.run(
                "CREATE INDEX evidence_label IF NOT EXISTS FOR (n:EvidenceChunk) ON (n.citation_label)"
            )
            s.run("CREATE INDEX parameter_pid IF NOT EXISTS FOR (n:Parameter) ON (n.parameter_id)")
            s.run(
                "CREATE INDEX limitrule_param IF NOT EXISTS FOR (n:LimitRule) ON (n.parameter_id)"
            )
            s.run("CREATE INDEX fact_id IF NOT EXISTS FOR (n:Fact) ON (n.fact_id)")
            for label, rows in p.nodes.items():
                # group by extra labels so the MERGE can set them statically
                by_extra: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
                for r in rows:
                    by_extra[tuple(r["labels"])].append(r)
                for extra, group in by_extra.items():
                    set_labels = "".join(f":`{x}`" for x in extra)
                    q = (
                        # `=`, not `+=`: a property (or the Fact label) the source no longer has
                        # must not survive the rebuild; the vector is written again below
                        f"UNWIND $rows AS r MERGE (n:`{label}` {{id: r.id}}) "
                        f"REMOVE n:`{FACT_LABEL}` SET n = r.props, n.id = r.id, "
                        f"n.projection_version = $v" + (f", n{set_labels}" if extra else "")
                    )
                    for i in range(0, len(group), BATCH):
                        s.run(q, rows=group[i : i + BATCH], v=version)
                    stats["nodes"] += len(group)
            for (la, rel, lb), rows in p.edges.items():
                q = (
                    f"UNWIND $rows AS r MATCH (a:`{la}` {{id: r.a}}) MATCH (b:`{lb}` {{id: r.b}}) "
                    f"MERGE (a)-[e:`{rel}`]->(b) SET e = r.props, e.projection_version = $v"
                )
                for i in range(0, len(rows), BATCH):
                    s.run(q, rows=rows[i : i + BATCH], v=version)
                stats["edges"] += len(rows)
            while True:
                n = s.run(
                    "MATCH (n) WHERE n.projection_version IS NULL OR n.projection_version <> $v "
                    "WITH n LIMIT 2000 DETACH DELETE n RETURN count(*) AS c",
                    v=version,
                ).single()["c"]
                stats["deleted"] += n
                if n == 0:
                    break
            s.run("MATCH ()-[e]->() WHERE e.projection_version <> $v DELETE e", v=version)

            def run(cypher: str, **params: Any) -> list[dict[str, Any]]:
                return s.run(cypher, **params).data()

            stats["vectors"] = vectors.write_vectors(run, p.vectors)
            dim = len(next(iter(p.vectors.values()))) if p.vectors else None
            if dim:
                vectors.ensure_index(run, dim, vectors.server_version(run))
            # runtime guard: the retriever uses the graph only while Meta.index_label equals the
            # active index_version in PostgreSQL (app.graph.runtime)
            s.run(
                "MERGE (m:Meta {id: 'projection'}) SET m.projection_version = $v, "
                "m.index_label = $label, m.built_at = datetime(), m.nodes = $nodes, m.edges = $edges, "
                "m.vectors = $vectors, m.embedding_dim = $dim, m.schema_version = $schema, "
                "m.source_database = $source, m.source_revision = $revision, "
                "m.fact_nodes = $fact_nodes, m.fact_edges = $fact_edges, "
                "m.edge_facts = $edge_facts, m.dangling_edges = $dangling",
                dangling=p.edge_stats.get("dangling", []),
                schema=SCHEMA_VERSION,
                source=source_database,
                revision=source_revision,
                fact_nodes=p.fact_stats.get("nodes"),
                fact_edges=p.fact_stats.get("edges"),
                edge_facts=p.fact_stats.get("edge_facts"),
                v=version,
                label=index_label,
                nodes=stats["nodes"],
                edges=stats["edges"],
                vectors=stats["vectors"],
                dim=dim,
            )
            drop_gds_graphs(s, database)
    return stats


def drop_gds_graphs(s: Any, database: str, keep: str | None = None) -> list[str]:
    """Drop the in-memory GDS graphs of older projections (named `qcvn_*`); no-op without GDS."""
    try:
        names = [
            r["graphName"]
            for r in s.run(
                "CALL gds.graph.list() YIELD graphName, database "
                "WHERE graphName STARTS WITH 'qcvn_' AND database = $db RETURN graphName",
                db=database,
            )
        ]
    except Exception:  # noqa: BLE001 - GDS not installed
        return []
    dropped = []
    for n in names:
        if n != keep:
            s.run("CALL gds.graph.drop($n, false) YIELD graphName RETURN graphName", n=n).consume()
            dropped.append(n)
    return dropped


def source_revision(session: Session) -> str | None:
    """Alembic revision of the source schema; None for a database created without migrations."""
    if session.execute(text("select to_regclass('alembic_version')")).scalar_one() is None:
        return None
    return session.execute(text("select version_num from alembic_version")).scalar_one_or_none()


def build(
    session: Session, settings: Settings | None = None, database: str | None = None
) -> dict[str, Any]:
    settings = settings or get_settings()
    database = database or settings.neo4j_database
    version = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    p = collect(session, settings)
    label = session.execute(
        select(m.IndexVersion.label).where(m.IndexVersion.status == "active")
    ).scalar_one_or_none()
    revision = source_revision(session)
    stats = write(
        p,
        settings,
        version,
        database,
        index_label=label,
        source_database=session.get_bind().url.database,
        source_revision=revision,
    )
    stats["version"] = version
    stats["schema_version"] = SCHEMA_VERSION
    stats["source"] = {"database": session.get_bind().url.database, "alembic": revision}
    stats["records"] = p.edge_stats
    stats["facts"] = {
        k: (len(v) if isinstance(v, list) else v) for k, v in p.fact_stats.items()
    } | {"unplaced_ids": p.fact_stats["unplaced"][:20]}
    stats["index_label"] = label
    stats["labels"] = {label: len(rows) for label, rows in p.nodes.items()}
    stats["edge_types"] = {rel: 0 for (_, rel, _) in p.edges}
    for (_, rel, _), rows in p.edges.items():
        stats["edge_types"][rel] += len(rows)
    return stats


def graph_counts(settings: Settings | None = None, database: str = "neo4j") -> dict[str, Any]:
    settings = settings or get_settings()
    with GraphDatabase.driver(
        settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
    ) as driver:
        with driver.session(database=database) as s:
            labels = {
                r["label"]: r["n"]
                for r in s.run(
                    "MATCH (n) UNWIND labels(n) AS label RETURN label, count(*) AS n ORDER BY label"
                )
            }
            rels = {
                r["t"]: r["n"]
                for r in s.run("MATCH ()-[e]->() RETURN type(e) AS t, count(*) AS n ORDER BY t")
            }
    return {"labels": labels, "relationships": rels}

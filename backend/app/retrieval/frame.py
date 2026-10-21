"""Query understanding v2, rule path (plan docs/plans/2026-10-12-g3-hieu-cau-hoi-v2.md §2).

`FrameBuilder.build` reads a question into a `QueryFrame`: the intents it carries (several, the
first one is the main intent), typed slots with the span of the question each one was read from,
and the reasons the rules are unsure. Everything it knows about the corpus comes from the
dictionaries (`intents.yaml`, `routing.yaml`, the entity dictionaries, `units.yaml`) and from the
`Corpus`; nothing here names a document, a table or a domain.

The frame runs beside the understanding in production (`app.retrieval.query.understand`), which
still decides retrieval: shadow mode until the gate of plan step G3 is passed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from app.ontology.corpus import SHARED, Corpus
from app.ontology.schema import Dictionaries, EquipmentType
from app.retrieval.tokenize import AliasIndex, normalize

CROSS_DOMAIN = "cross_domain"
OUT_OF_SCOPE = "out_of_scope"
UNKNOWN = "unknown"
FALLBACK_INTENT = "other"
COMPARISON = "comparison"
# slots that say what a question is about: two questions about different ones are two questions
SUBJECT = {"parameter", "document"}
# conditions that single out a cell of a table: with a parameter they make a value question
VALUE_CONDITIONS = {"zone", "flow", "equipment"}

# unit kind (units.yaml) -> slot a number with that unit fills
NUMBER_SLOTS = {
    "flow": "flow",
    "power": "capacity",
    "heat": "capacity",
    "mass_rate": "capacity",
    "concentration": "measured_value",
}
_NUMBER = r"(\d[\d.,]*\d|\d)"
ZONE_AFTER_CUE = 15  # characters between a negation cue and the zone it excludes
GENERIC_LABEL_WORDS = 4  # a child label shorter than this needs an ancestor beside it
# dimensions a negation cue can exclude a value of
NEGATABLE = {"zone", "parameter", "fuel", "sector", "receiving_body"}
_SENTENCE_END = re.compile(r"[.;?!]|(?<!\w)(?:mà|nhưng|thì|phải)(?!\w)")
_DATE = re.compile(
    r"(?<![\d/])(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})(?!\d)"
    r"|ngày\s+(\d{1,2})\s+tháng\s+(\d{1,2})\s+năm\s+(\d{4})"
    r"|tháng\s+(\d{1,2})[/\s]+(?:năm\s+)?(\d{4})(?!\d)"
)
_ZONE_WORD = re.compile(r"(?<!\w)(?:cột|loại|mức)\s*([a-z0-9])(?!\w)")
# the regulation's own name of a column; "mức B" of another regulation only describes it
_ZONE_NAME = re.compile(r"(?<!\w)(?:cột|loại)\s*([a-z0-9])(?!\w)")
_CLAUSE_END = re.compile(r"(?<=[.;?!])\s+|,\s+(?=còn\s)")
MAX_SUB_QUESTIONS = 3


class Slot(BaseModel):
    dimension: str
    value: str
    unit: str | None = None
    negated: bool = False
    start: int = -1  # span in the normalized question; -1 when the value is derived
    end: int = -1
    text: str = ""
    source: str = "rule"  # rule | hop (filled by a hop, with the clause it comes from)
    # a zone: "name" when the question says "cột B", "description" when it describes the zone
    stated_as: str = "name"


class SubQuestion(BaseModel):
    """One independent part of a question: its own domain, intents and slots (shared context
    inherited). `text` is cut from the question, never rewritten."""

    text: str
    start: int
    end: int
    domain: str
    intents: list[str]
    slots: list[Slot] = Field(default_factory=list)


class Hop(BaseModel):
    """A second basis the answer needs: what in the question calls for it, the clause it resolves
    to, and what it does to the question."""

    type: str
    trigger: str = ""
    status: str = "unresolved"  # resolved | unresolved
    role: str = ""
    chunk_ids: list[str] = Field(default_factory=list)
    legal_addresses: list[str] = Field(default_factory=list)
    supporting_chunk_ids: list[str] = Field(default_factory=list)  # reached by a reference
    effect: dict[str, str] = Field(default_factory=dict)


class QueryFrame(BaseModel):
    question: str
    domain: str
    intents: list[str]
    slots: list[Slot] = Field(default_factory=list)
    relation: str = "none"  # none | compare | sequence
    sub_questions: list[SubQuestion] = Field(default_factory=list)
    hops: list[Hop] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    clarification: dict | None = None  # app.retrieval.candidates.Clarification, dumped
    unsure: list[str] = Field(default_factory=list, description="why the rules want a second look")
    source: str = "rule"

    def values(self, dimension: str, *, negated: bool = False) -> list[str]:
        return [s.value for s in self.slots if s.dimension == dimension and s.negated == negated]


def parse_number(s: str) -> float | None:
    """Vietnamese number formats: '3.000' → 3000, '2,5' → 2.5, '12.380.000' → 12380000."""
    s = s.strip()
    if re.fullmatch(r"\d{1,3}(\.\d{3})+", s):
        s = s.replace(".", "")
    elif "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


@dataclass
class _Equipment:
    spec: EquipmentType
    variants: list[str]  # normalized ways the label may be written, longest first
    depth: int
    domains: set[str] = field(default_factory=set)


class FrameBuilder:
    """Built once per process from the dictionaries, the alias index and the corpus."""

    def __init__(
        self,
        d: Dictionaries,
        aliases: AliasIndex,
        corpus: Corpus,
        doc_domains: dict[str, str] | None = None,
    ) -> None:
        self.aliases = aliases
        self.corpus = corpus
        self.doc_domains = doc_domains or {}
        self.intents = list(d.intents)
        self.negation = self._alternation(d.negation_cues)
        self.split_cues = [normalize(c) for c in d.split_cues]
        self.zone_descriptions = [
            (re.compile(r"(?<!\w)" + re.escape(normalize(z.pattern)) + r"(?!\w)"), z.domain, z.code)
            for z in sorted(d.zone_descriptions, key=lambda z: -len(z.pattern))
        ]
        self.param_domains = {p.parameter_id: set(p.domains) for p in d.parameters}
        self.unit_kind = {u.unit_id: u.kind for u in d.units}
        self.unit_domain = {
            u: code for code, spec in corpus.domains.items() for u in spec.unit_hints
        }
        units: dict[str, str] = {}
        for u in d.units:
            for name in (u.symbol, *u.aliases):
                units.setdefault(normalize(name), u.unit_id)
        self.units = sorted(units.items(), key=lambda kv: -len(kv[0]))
        self.number_unit = re.compile(
            _NUMBER + r"\s*(" + "|".join(re.escape(n) for n, _ in self.units) + r")(?![\w/])"
        )
        self.unit_of = dict(self.units)
        self.equipment = self._equipment(d)
        self._equipment_rx = [
            (
                eid,
                re.compile(
                    r"(?<!\w)(?:" + "|".join(re.escape(v) for v in eq.variants) + r")(?!\w)"
                ),
            )
            for eid, eq in self.equipment.items()
            if eq.variants
        ]

    @staticmethod
    def _alternation(cues: list[str]) -> re.Pattern[str] | None:
        cues = sorted({normalize(c) for c in cues if c}, key=len, reverse=True)
        if not cues:
            return None
        body = "|".join(re.escape(c).replace(r"\ ", r"\s+") for c in cues)
        return re.compile(r"(?<!\w)(?:" + body + r")(?!\w)")

    # ------------------------------------------------------------------ equipment

    def _equipment(self, d: Dictionaries) -> dict[str, _Equipment]:
        prefixes = sorted((normalize(p) for p in d.equipment_label_prefixes), key=len, reverse=True)
        code_domain = {
            normalize(doc.document_code): doc.domain for doc in self.corpus.documents.values()
        }
        by_id = {e.equipment_id: e for e in d.equipment_types}
        out: dict[str, _Equipment] = {}
        for e in d.equipment_types:
            name = normalize(e.name)
            variants = {name, re.sub(r"\s*\([^)]*\)", "", name).strip()}
            for v in list(variants):
                for p in prefixes:
                    if v.startswith(p + " ") or v.startswith(p + ","):
                        variants.add(v[len(p) :].lstrip(" ,"))
            for al in e.aliases:
                variants.add(normalize(al))
            depth, cur = 1, e
            while cur.parent_id and cur.parent_id in by_id:
                depth, cur = depth + 1, by_id[cur.parent_id]
            domains = set()
            for label in e.sources:
                dom = code_domain.get(normalize(label.split(",")[0]))
                if self.corpus.is_regulated(dom):
                    domains.add(dom)
            out[e.equipment_id] = _Equipment(
                spec=e,
                variants=sorted((v for v in variants if len(v) >= 3), key=len, reverse=True),
                depth=depth,
                domains=domains,  # type: ignore[arg-type]
            )
        # a shortened variant shared by several labels belongs to the label it is the full
        # name of ("các thiết bị xả thải khác" is not "các thiết bị xả thải khác (trừ …)")
        owners: dict[str, list[str]] = {}
        for eid, eq in out.items():
            for v in eq.variants:
                owners.setdefault(v, []).append(eid)
        for v, eids in owners.items():
            if len(eids) < 2:
                continue
            for eid in eids:
                if normalize(out[eid].spec.name) != v:
                    out[eid].variants.remove(v)
        return out

    def _find_equipment(self, q: str) -> tuple[_Equipment, list[tuple[int, int]]] | None:
        """The equipment label the question names, with the spans of every label found.

        A question lists a row of the table from the group down to the row ("thiết bị sản xuất
        cốc, sản xuất cốc dầu mỏ, đốt"), so the label asked about is the last one. A label that
        lies inside a longer label found in the same place is that longer label. A short label
        ("đốt", "sấy") only counts after another label. The parent recorded in the dictionary
        is not used: one label occurs under several groups."""
        found: list[tuple[int, int, str]] = []
        for eid, rx in self._equipment_rx:
            found += [(m.start(), m.end(), eid) for m in rx.finditer(q)]
        kept = [
            f
            for f in found
            if not any(
                g[0] <= f[0] and f[1] <= g[1] and (g[1] - g[0]) > (f[1] - f[0]) for g in found
            )
        ]
        kept.sort()
        if not kept:
            return None
        a, b, eid = kept[-1]
        # "thiết bị đốt thuộc hệ thống sản xuất …": the group named last, its row named before
        below = [f for f in kept[:-1] if eid in self._ancestors(f[2])]
        if below:
            a, b, eid = max(below, key=lambda f: self.equipment[f[2]].depth)
        generic = self.equipment[eid].depth > 1 and len(q[a:b].split()) < GENERIC_LABEL_WORDS
        if generic and len(kept) == 1:
            return None
        spans = [(x, y) for x, y, _ in kept if (x, y) != (a, b)] + [(a, b)]
        return self.equipment[eid], spans

    def _ancestors(self, eid: str) -> set[str]:
        out: set[str] = set()
        cur = self.equipment[eid].spec.parent_id
        while cur in self.equipment and cur not in out:
            out.add(cur)
            cur = self.equipment[cur].spec.parent_id
        return out

    # ------------------------------------------------------------------ helpers

    def _negated_spans(self, q: str) -> list[tuple[int, int]]:
        if self.negation is None:
            return []
        out = []
        for m in self.negation.finditer(q):
            end = _SENTENCE_END.search(q, m.end())
            out.append((m.end(), end.start() if end else len(q)))
        return out

    def _intents(self, q: str, dimensions: set[str]) -> list[str]:
        hit = [i for i in self.intents if any(k in q for k in i.keywords)]
        if not hit:  # "COD cột A?" asks the value even without a cue word
            hit = [i for i in self.intents if i.main_with_slot in dimensions]
        if not hit:
            return [FALLBACK_INTENT]
        main = next((i for i in hit if i.always_main), None)
        if main is None:
            main = next((i for i in hit if i.main_with_slot in dimensions), None)
        if main is None:
            main = next((i for i in hit if not i.secondary_only), hit[0])
        out = [main.code] + [i.code for i in hit if i is not main]
        # "… từ 01/01/2032 As cột C?": a parameter with a condition asks its value too, even
        # when the only cue words are about something else
        if dimensions & VALUE_CONDITIONS:
            out += [
                i.code for i in self.intents if i.main_with_slot in dimensions and i.code not in out
            ]
        return out

    def _domain(self, q: str, strong: dict[str, int]) -> str:
        """Slots that belong to one domain decide; routing keywords only speak when no slot does."""
        corpus = self.corpus
        domains = [d for d in corpus.domains if strong.get(d)]
        if not domains:
            domains = [
                d for d, spec in corpus.domains.items() if any(k in q for k in spec.keywords)
            ]
            if not domains and any(k in q for k in corpus.out_of_scope_keywords):
                return OUT_OF_SCOPE
        if len(domains) > 1:
            return CROSS_DOMAIN
        return domains[0] if domains else UNKNOWN

    # ------------------------------------------------------------------ build

    def build(self, question: str) -> QueryFrame:
        q = normalize(question).replace("^", "")  # "ClO4^-" is the symbol "ClO4-"
        slots: list[Slot] = []
        # tier 1: a document, an equipment, the domain named; tier 2: a parameter, a unit, a zone
        strong: dict[str, int] = {}
        weak: dict[str, int] = {}
        unsure: list[str] = []
        masked = q

        labels: list[tuple[int, int]] = []  # equipment labels: never cut by a clause boundary

        def mask(a: int, b: int) -> None:
            nonlocal masked
            masked = masked[:a] + "#" * (b - a) + masked[b:]
            labels.append((a, b))

        evidence_at: list[tuple[int, str, int]] = []  # (position, domain, tier)

        def vote(domain: str | None, tier: int = 1, pos: int = -1) -> None:
            if self.corpus.is_regulated(domain):
                evidence = strong if tier == 1 else weak
                evidence[domain] = evidence.get(domain, 0) + 1  # type: ignore[index]
                evidence_at.append((pos, domain, tier))  # type: ignore[arg-type]

        negated = self._negated_spans(q)

        def is_negated(pos: int) -> bool:
            return any(a <= pos < b for a, b in negated)

        # dates first: "01/01/2032" must not be read as numbers or document codes
        for m in _DATE.finditer(q):
            g = m.groups()
            if g[0]:
                value = f"{g[2]}-{int(g[1]):02d}-{int(g[0]):02d}"
            elif g[3]:
                value = f"{g[5]}-{int(g[4]):02d}-{int(g[3]):02d}"
            else:
                value = f"{g[7]}-{int(g[6]):02d}"
            slots.append(
                Slot(dimension="date", value=value, start=m.start(), end=m.end(), text=m.group(0))
            )

        # equipment before the aliases: a label holds words that are aliases of other things
        eq = self._find_equipment(q)
        if eq:
            spec, spans = eq[0].spec, eq[1]
            a, b = spans[-1]
            slots.append(
                Slot(dimension="equipment", value=spec.equipment_id, start=a, end=b, text=q[a:b])
            )
            for dom in eq[0].domains:
                vote(dom, 1, a)
            for a, b in spans:
                mask(a, b)

        # numbers with a unit: the unit kind says what the number is
        for m in self.number_unit.finditer(masked):
            unit = self.unit_of[m.group(2)]
            dim = NUMBER_SLOTS.get(self.unit_kind.get(unit, ""))
            value = parse_number(m.group(1))
            if dim is None or value is None:
                continue
            slots.append(
                Slot(
                    dimension=dim,
                    value=f"{value:g}",
                    unit=unit,
                    start=m.start(),
                    end=m.end(),
                    text=m.group(0),
                )
            )
            vote(self.unit_domain.get(unit), 2, m.start())

        zone_mentions: list[tuple[int, int, str]] = []
        hits = self.aliases.find_spans(masked)
        # a label often names its fuel ("sử dụng nhiên liệu khí"): fuels are read under the mask too
        under = [
            h for h in self.aliases.find_spans(q) if h[0].entity_type == "fuel" and h not in hits
        ]
        if under:  # one label, one fuel: "nhiên liệu khí hóa than" is gas, not gas and coal
            hits.append(max(under, key=lambda h: h[2] - h[1]))
        for hit, a, b in hits:
            kind, eid = hit.entity_type, hit.entity_id
            neg = kind in NEGATABLE and is_negated(a)
            if kind == "zone":
                if "." in eid:
                    dom, eid = eid.split(".", 1)
                    vote(dom, 2, a)
                zone_mentions.append((a, b, eid))
                continue
            if kind == "domain":
                vote(eid, 1, a)
                continue
            if kind == "unit":
                vote(self.unit_domain.get(eid), 2, a)
                continue
            if kind == "document":
                vote(self.doc_domains.get(eid), 1, a)
            if kind == "parameter" and not neg:
                own = self.param_domains.get(eid, set())
                if len(own) == 1 and SHARED not in own:
                    vote(next(iter(own)), 2, a)
            dim = {"fuel": "fuel_type"}.get(kind, kind)
            if dim == "fuel_type" and any(s.dimension == dim and s.value == eid for s in slots):
                continue
            slots.append(Slot(dimension=dim, value=eid, negated=neg, start=a, end=b, text=q[a:b]))

        for rx, dom, code in self.zone_descriptions:
            for m in rx.finditer(masked):
                if not any(a <= m.start() < b for a, b, _ in zone_mentions):
                    zone_mentions.append((m.start(), m.end(), code))
                    vote(dom, 2, m.start())
        for m in _ZONE_WORD.finditer(masked):
            code = m.group(1).upper()
            if code in self.corpus.zones():
                zone_mentions.append((m.start(), m.end(), code))
        # a negation is about zones when a zone follows the cue at once ("không thuộc Mức A,
        # Mức B"); "không thuộc nhóm thủy sản … , Cột B" excludes a sector and asks column B
        zone_scopes = [
            (a, b)
            for a, b in negated
            if any(a <= m[0] <= a + ZONE_AFTER_CUE for m in zone_mentions)
        ]
        named = {m.group(1).upper() for m in _ZONE_NAME.finditer(masked)}
        for s in self._zones(
            q, sorted(zone_mentions), lambda pos: any(a <= pos < b for a, b in zone_scopes)
        ):
            s.stated_as = "name" if s.value in named else "description"
            slots.append(s)

        dimensions = {s.dimension for s in slots if not s.negated}
        intents = self._intents(q, dimensions)
        domain = self._domain(q, strong or weak)
        if domain in (UNKNOWN, CROSS_DOMAIN):
            unsure.append(f"domain:{domain}")
        if intents == [FALLBACK_INTENT]:
            unsure.append("intent:none")
        slots.sort(key=lambda s: (s.start, s.dimension))
        cue = any(c in q for c in self.split_cues)
        subs = self._split(q, slots, evidence_at, intents, cue, labels)
        if cue and not subs:
            unsure.append("multi_part")
        relation = "none"
        if subs:
            relation = "compare" if COMPARISON in intents else "sequence"
            # two domains that became two sub-questions are understood, not a doubt
            unsure = [u for u in unsure if u != f"domain:{CROSS_DOMAIN}"]
        return QueryFrame(
            question=question,
            domain=domain,
            intents=intents,
            slots=slots,
            relation=relation,
            sub_questions=subs,
            unsure=unsure,
        )

    # ------------------------------------------------------------------ sub-questions

    @staticmethod
    def _clauses(q: str, keep: list[tuple[int, int]]) -> list[tuple[int, int]]:
        out, start = [], 0
        for m in _CLAUSE_END.finditer(q):
            if any(a < m.end() and m.start() < b for a, b in keep):
                continue  # inside a label ("…, tái chế nhựa; thiết bị xả thải khác")
            if m.start() > start:
                out.append((start, m.start()))
            start = m.end()
        if start < len(q):
            out.append((start, len(q)))
        return out

    def _slot_domains(self, s: Slot) -> set[str] | None:
        """Domains a slot can belong to; None = any (a column letter, a date)."""
        if s.dimension == "parameter":
            own = self.param_domains.get(s.value, set())
            return None if SHARED in own or not own else set(own)
        if s.dimension == "document":
            dom = self.doc_domains.get(s.value)
            return {dom} if self.corpus.is_regulated(dom) else None
        if s.dimension == "equipment":
            return set(self.equipment[s.value].domains) or None
        if s.unit in self.unit_domain:
            return {self.unit_domain[s.unit]}
        return None

    def _split(
        self,
        q: str,
        slots: list[Slot],
        evidence: list[tuple[int, str, int]],
        intents: list[str],
        cue: bool,
        labels: list[tuple[int, int]],
    ) -> list[SubQuestion]:
        """Sub-questions of a question whose parts differ in domain, or of two questions written
        one after the other. A question is only split when every part ends up with a domain and
        an intent; otherwise it stays whole."""
        clauses = self._clauses(q, labels)

        def inside(pos: int, c: tuple[int, int]) -> bool:
            return c[0] <= pos < c[1]

        strong_domains = list(dict.fromkeys(d for _, d, tier in sorted(evidence) if tier == 1))
        any_domains = list(dict.fromkeys(d for _, d, _ in sorted(evidence)))
        domains = strong_domains if len(strong_domains) > 1 else (any_domains if cue else [])
        groups: list[tuple[str, list[tuple[int, int]]]] = []
        of_clause = [{d for pos, d, _ in evidence if inside(pos, c)} for c in clauses]
        if cue:  # a part without any slot of one domain: its routing keywords speak
            for i, c in enumerate(clauses):
                if not of_clause[i]:
                    text = q[c[0] : c[1]]
                    of_clause[i] = {
                        d
                        for d, spec in self.corpus.domains.items()
                        if any(k in text for k in spec.keywords)
                    }
            domains = list(dict.fromkeys([*domains, *(d for ds in of_clause for d in sorted(ds))]))
        if len(domains) > 1:
            for dom in domains:
                mine = [c for c, ds in zip(clauses, of_clause, strict=True) if dom in ds or not ds]
                groups.append((dom, mine))
        else:
            asked = [c for c in clauses if q[c[1] - 1 : c[1]] == "?"]
            own = [
                {
                    (s.dimension, s.value)
                    for s in slots
                    if inside(s.start, c) and s.dimension in SUBJECT
                }
                for c in asked
            ]
            if len(asked) > 1 and all(o and o != own[0] for o in own[1:]) and own[0]:
                dom = any_domains[0] if len(any_domains) == 1 else None
                if dom:
                    first = asked[0][0]
                    lead = [
                        c for c in clauses if c[1] <= first
                    ]  # context before the first question
                    groups = [(dom, [*lead, c]) for c in asked]
        if not 1 < len(groups) <= MAX_SUB_QUESTIONS:
            return []
        subs: list[SubQuestion] = []
        for dom, mine in groups:
            text = " ".join(q[a:b] for a, b in mine)
            own_slots = []
            own_zone = any(
                s.dimension == "zone" and any(inside(s.start, c) for c in mine) for s in slots
            )
            for s in slots:
                here = any(inside(s.start, c) for c in mine)
                # a column letter said once holds for every part that names none of its own
                if not here and (s.dimension != "zone" or own_zone):
                    continue
                allowed = self._slot_domains(s)
                if allowed is None or dom in allowed:
                    own_slots.append(s)
            found = self._intents(text, {s.dimension for s in own_slots if not s.negated})
            found = [i for i in found if i != COMPARISON]
            if not found or found == [FALLBACK_INTENT]:
                found = [i for i in intents if i != COMPARISON]
            if not found or found == [FALLBACK_INTENT]:
                return []
            subs.append(
                SubQuestion(
                    text=text,
                    start=min(a for a, _ in mine),
                    end=max(b for _, b in mine),
                    domain=dom,
                    intents=found,
                    slots=own_slots,
                )
            )
        return subs

    def _zones(
        self, q: str, mentions: list[tuple[int, int, str]], is_negated: object
    ) -> list[Slot]:
        """Zones the question asks about. A zone named inside a negation is excluded; when the
        exclusions leave one zone of the scheme, the question describes that one."""
        out: list[Slot] = []
        seen: set[str] = set()
        excluded: set[str] = set()
        for a, b, code in mentions:
            if is_negated(a):  # type: ignore[operator]
                excluded.add(code)
            elif code not in seen:
                seen.add(code)
                out.append(Slot(dimension="zone", value=code, start=a, end=b, text=q[a:b]))
        if out or not excluded:
            return out
        rest = [z for z in self.corpus.zones() if z not in excluded]
        if len(rest) == 1:
            a = min(m[0] for m in mentions)
            b = max(m[1] for m in mentions)
            return [Slot(dimension="zone", value=rest[0], start=a, end=b, text=q[a:b])]
        return []

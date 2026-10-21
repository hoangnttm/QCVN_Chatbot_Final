"""Rule-based query understanding (PLAN.md §6.1–6.2, §8): domain routing, intent, entities.

Runs before any retrieval and before any LLM call; the LLM (Phase 4) only fills gaps when
`needs_llm` is true. Frequency questions are routed to policy M-01 (abstain with pointer, D10).
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Literal

from pydantic import BaseModel, Field

from app.ontology import corpus as corpus_mod
from app.retrieval.tokenize import AliasIndex, canonical_token, normalize

# a regulated domain code (domains.yaml) or one of the routing outcomes below
Domain = str
CROSS_DOMAIN = "cross_domain"
OUT_OF_SCOPE = "out_of_scope"
UNKNOWN = "unknown"
Intent = Literal[
    "threshold",
    "method",
    "frequency",
    "effectivity",
    "sampling",
    "definition",
    "applicability",
    "scope",
    "other",
]

_INTENT_RULES: list[tuple[Intent, list[str]]] = [
    ("frequency", ["tần suất", "bao lâu một lần", "mấy lần", "định kỳ bao", "bao nhiêu lần"]),
    (
        "effectivity",
        [
            "hiệu lực",
            "thay thế",
            "bãi bỏ",
            "còn áp dụng",
            "hết hiệu lực",
            "ngày ban hành",
            "ngày ký",
            "có hiệu lực từ",
        ],
    ),
    (
        "method",
        ["phương pháp", "trọng tài", "tcvn", "smeww", "iso ", "xác định bằng", "phân tích bằng"],
    ),
    (
        "sampling",
        [
            "lấy mẫu",
            "điểm lấy mẫu",
            "công suất thiết kế",
            "đo trực tiếp",
            "vị trí lấy mẫu",
            "khí chuẩn",
        ],
    ),
    ("definition", [" là gì", "định nghĩa", "được hiểu", "khái niệm"]),
    (
        "applicability",
        [
            "áp dụng cột nào",
            "áp dụng quy chuẩn nào",
            "cơ sở đã vận hành",
            "dự án mới",
            "chuyển tiếp",
            "đấu nối",
            "lộ trình",
            "phải đáp ứng",
        ],
    ),
    (
        "threshold",
        ["giới hạn", "ngưỡng", "bao nhiêu", "tối đa", "cho phép", "mg/", "nồng độ", "giá trị"],
    ),
    ("scope", ["phạm vi", "áp dụng cho", "có áp dụng", "đối tượng"]),
]
_FLOW = re.compile(r"(\d[\d.,]*)\s*m3\s*/\s*ngày")
_CAPACITY = re.compile(r"(\d[\d.,]*)\s*(tấn hơi\s*/\s*giờ|tấn\s*/\s*giờ|mw|kcal\s*/\s*giờ)")
_ZONE_WORD = re.compile(r"\b(?:cột|loại|mức)\s*([abc])\b")


# "không thuộc Mức A, Mức B …", "không thuộc vùng bảo vệ nghiêm ngặt hay vùng hạn chế phát thải":
# the zones named after the cue are excluded, up to the end of the sentence or a contrast word
_NEGATION = re.compile(r"không\s+(?:thuộc|phải(?:\s+là)?|nằm\s+trong)")
_NEGATION_END = re.compile(r"[.;?!]|(?<!\w)(?:mà|nhưng)(?!\w)")


def _positions(q: str, pattern: str) -> list[int]:
    return [m.start() for m in re.finditer(r"(?<![\w])" + re.escape(pattern) + r"(?![\w])", q)]


def _resolve_zone(q: str, alias_mentions: list[tuple[int, str]]) -> str | None:
    """The zone the question asks about. Alias mentions come first, then "cột/loại/mức X". A zone
    named inside a negation is not the zone asked for; when two of the three are excluded the
    question describes the third ("không thuộc Mức A, Mức B" → C): in general, the single zone
    left once the others are excluded."""
    scopes = []
    for m in _NEGATION.finditer(q):
        end = _NEGATION_END.search(q, m.end())
        scopes.append((m.end(), end.start() if end else len(q)))

    def negated(pos: int) -> bool:
        return any(a <= pos < b for a, b in scopes)

    mentions = sorted(alias_mentions) + [
        (m.start(), m.group(1).upper()) for m in _ZONE_WORD.finditer(q)
    ]
    affirmed = [z for pos, z in mentions if not negated(pos)]
    if affirmed:
        return affirmed[0]
    excluded = {z for pos, z in mentions if negated(pos)}
    rest = [z for z in corpus_mod.current().zones() if z not in excluded]
    return rest[0] if excluded and len(rest) == 1 else None


def _num(s: str) -> float | None:
    """Vietnamese number formats: '3.000' (thousands) → 3000, '2,5' → 2.5, '12.380.000' → 12380000."""
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


class QueryUnderstanding(BaseModel):
    question: str
    domain: Domain
    intent: Intent
    parameters: list[str] = Field(default_factory=list)
    zone: str | None = None
    flow_m3_day: float | None = None
    fuel_type: str | None = None
    capacity_value: float | None = None
    capacity_unit: str | None = None
    receiving_body: str | None = None
    sector_id: str | None = None
    unit_ids: list[str] = Field(default_factory=list)
    document_ids: list[str] = Field(default_factory=list)
    as_of_date: dt.date | None = None
    policy: str | None = Field(default=None, description="M-01 for frequency questions (D10)")
    expansions: list[str] = Field(default_factory=list, description="canonical tokens for BM25")
    matched: list[str] = Field(default_factory=list)
    needs_llm: bool = False


def understand(
    question: str,
    aliases: AliasIndex,
    as_of_date: dt.date | None = None,
    doc_domains: dict[str, str] | None = None,
) -> QueryUnderstanding:
    """`doc_domains` maps document ids (incl. external stubs replaced by a corpus QCVN) to
    wastewater | emission so "QCVN 40:2011 còn hiệu lực không?" routes to wastewater."""
    q = normalize(question)
    hits = aliases.find(question)
    params: list[str] = []
    zone_mentions: list[tuple[int, str]] = []  # (position in q, zone letter) from the aliases
    fuel = None
    rbt = None
    sector = None
    units: list[str] = []
    docs: list[str] = []
    domain_hits: set[str] = set()
    for h in hits:
        if h.entity_type == "parameter" and h.entity_id not in params:
            params.append(h.entity_id)
        elif h.entity_type == "zone":
            zid = h.entity_id
            if "." in zid:
                d, z = zid.split(".", 1)
                domain_hits.add(d)
                zone_mentions += [(pos, z) for pos in _positions(q, h.matched)]
            else:
                zone_mentions += [(pos, zid) for pos in _positions(q, h.matched)]
        elif h.entity_type == "fuel":
            fuel = fuel or h.entity_id
        elif h.entity_type == "receiving_body":
            rbt = rbt or h.entity_id
        elif h.entity_type == "sector":
            sector = sector or h.entity_id
        elif h.entity_type == "unit" and h.entity_id not in units:
            units.append(h.entity_id)
        elif h.entity_type == "document" and h.entity_id not in docs:
            docs.append(h.entity_id)
        elif h.entity_type == "domain":
            domain_hits.add(h.entity_id)
    zone = _resolve_zone(q, zone_mentions)
    flow = _num(_FLOW.search(q).group(1)) if _FLOW.search(q) else None
    cap = _CAPACITY.search(q)
    cap_v, cap_u = (None, None)
    if cap:
        cap_v = _num(cap.group(1))
        u = cap.group(2).replace(" ", "")
        cap_u = {
            "tấnhơi/giờ": "t_steam_per_h",
            "tấn/giờ": "t_per_h",
            "mw": "MW",
            "kcal/giờ": "kcal_per_h",
        }.get(u)

    corpus = corpus_mod.current()
    score: dict[str, int] = {}
    for code, spec in corpus.domains.items():
        own_documents = corpus.documents_of(code)
        score[code] = sum(1 for k in spec.keywords if k in q) + (
            1 if any(d in own_documents for d in docs) else 0
        )
    for d in docs:
        dd = (doc_domains or {}).get(d)
        if corpus.is_regulated(dd):
            domain_hits.add(dd)
    for code, spec in corpus.domains.items():
        score[code] += sum(1 for d in domain_hits if d == code)
        if any(u in units for u in spec.unit_hints):
            score[code] += 1
    matched_domains = [code for code, n in score.items() if n]
    oos = sum(1 for k in corpus.out_of_scope_keywords if k in q)
    if oos and not matched_domains:
        domain: Domain = OUT_OF_SCOPE
    elif len(matched_domains) > 1:
        domain = CROSS_DOMAIN
    elif matched_domains:
        domain = matched_domains[0]
    else:
        domain = UNKNOWN

    intent: Intent = "other"
    for name, keys in _INTENT_RULES:
        if any(k in q for k in keys):
            intent = name
            break
    if intent == "other" and params:
        intent = "threshold"
    policy = "M-01" if intent == "frequency" else None

    expansions = [canonical_token("param", p) for p in params]
    if zone:
        expansions.append(canonical_token("zone", zone))
    if fuel:
        expansions.append(canonical_token("fuel", fuel))
    if rbt:
        expansions.append(canonical_token("rbt", rbt))
    if sector:
        expansions.append(canonical_token("sector", sector))
    expansions += [canonical_token("doc", d) for d in docs]
    if intent == "threshold":
        expansions.append("ctype_table_row")
    return QueryUnderstanding(
        question=question,
        domain=domain,
        intent=intent,
        parameters=params,
        zone=zone,
        flow_m3_day=flow,
        fuel_type=fuel,
        capacity_value=cap_v,
        capacity_unit=cap_u,
        receiving_body=rbt,
        sector_id=sector,
        unit_ids=units,
        document_ids=docs,
        as_of_date=as_of_date,
        policy=policy,
        expansions=expansions,
        matched=[f"{h.entity_type}:{h.entity_id}←{h.matched}" for h in hits],
        needs_llm=domain in (UNKNOWN, CROSS_DOMAIN) or intent == "other",
    )

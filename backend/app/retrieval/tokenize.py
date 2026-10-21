"""Vietnamese tokenization (pyvi word segmentation) and dictionary-driven alias matching (PLAN.md §6.1).

`tokenize` turns text into BM25 tokens: pyvi compounds (`nước_thải`) plus their syllables, numbers,
codes (`40:2025`, `tcvn 6491:1999`). `AliasIndex` finds canonical entities (parameter, zone, unit,
fuel, receiving body, sector, domain, document) mentioned in a question by longest-match on the
aliases of `data/dictionaries/*.yaml`; the entity ids become extra canonical tokens (`param_bod5`)
that documents also carry, so "nhu cầu oxy sinh hóa" and "BOD5" meet on the same token.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from pyvi import ViTokenizer

from app.ontology.schema import Dictionaries

_WS = re.compile(r"\s+")
_STRIP = ".,;:()[]\"'“”‘’?!…"


def normalize(text: str) -> str:
    t = unicodedata.normalize("NFC", text or "").lower()
    t = t.replace("–", "-").replace("—", "-").replace("³", "3").replace("²", "2")
    return _WS.sub(" ", t).strip()


def tokenize(text: str) -> list[str]:
    seg = ViTokenizer.tokenize(normalize(text))
    out: list[str] = []
    for tok in seg.split():
        tok = tok.strip(_STRIP)
        if not tok:
            continue
        out.append(tok)
        if "_" in tok:
            out.extend(p for p in tok.split("_") if len(p) > 1)
    return out


def canonical_token(entity_type: str, entity_id: str) -> str:
    return f"{entity_type}_{entity_id}".lower().replace(".", "_").replace("-", "_")


@dataclass(frozen=True)
class AliasHit:
    entity_type: str
    entity_id: str
    matched: str


class AliasIndex:
    """Longest-match alias lookup over the canonical dictionaries (query side)."""

    def __init__(self, d: Dictionaries, documents: dict[str, str] | None = None) -> None:
        pats: dict[str, tuple[str, str]] = {}

        def add(pattern: str | None, etype: str, eid: str) -> None:
            if not pattern:
                return
            p = normalize(pattern)
            if len(p) < 2 or p.isdigit():
                return
            pats.setdefault(p, (etype, eid))

        for a in d.aliases:
            add(a.pattern, a.entity_type, a.entity_id)
        for p in d.parameters:
            add(p.name, "parameter", p.parameter_id)
            add(p.name.split("(")[0], "parameter", p.parameter_id)
            add(p.symbol, "parameter", p.parameter_id)
            add(p.formula, "parameter", p.parameter_id)
            for al in p.aliases:
                add(al, "parameter", p.parameter_id)
        for u in d.units:
            add(u.symbol, "unit", u.unit_id)
            for al in u.aliases:
                add(al, "unit", u.unit_id)
        for z in d.zones:
            for al in z.aliases:
                if al.lower() not in ("cột a", "cột b", "cột c", "mức a", "mức b"):
                    add(al, "zone", f"{z.domain}.{z.code}")
        for f in d.fuel_types:
            add(f.name, "fuel", f.code)
            for al in f.aliases:
                add(al, "fuel", f.code)
        for r in d.receiving_body_types:
            add(r.name, "receiving_body", r.code)
            for al in r.aliases:
                add(al, "receiving_body", r.code)
        for s in d.industry_sectors:
            add(s.name, "sector", s.sector_id)
            for al in s.aliases:
                add(al, "sector", s.sector_id)
        for doc_id, code in (documents or {}).items():
            add(code, "document", doc_id)
            m = re.match(r"qcvn (\d+)(?:-mt)?:(\d{4})", normalize(code))
            if m:
                add(f"qcvn {m.group(1)}:{m.group(2)}", "document", doc_id)
                add(f"qcvn {m.group(1)}", "document", doc_id)
            m = re.match(r"(\d+)/(\d{4})/tt-btnmt", normalize(code))
            if m:
                add(f"thông tư {m.group(1)}/{m.group(2)}", "document", doc_id)
                add(f"tt {m.group(1)}/{m.group(2)}", "document", doc_id)
        # longest first so "bod5" beats "bod", "tổng phốt pho" beats "tổng p"
        self._patterns = sorted(pats.items(), key=lambda kv: -len(kv[0]))
        self._regex = {
            p: re.compile(r"(?<![\w])" + re.escape(p) + r"(?![\w])") for p, _ in self._patterns
        }

    def find_spans(self, normalized: str) -> list[tuple[AliasHit, int, int]]:
        """Every occurrence with its span in an already normalized text, longest alias first;
        a character belongs to one hit only. Characters masked with '#' by the caller never
        match."""
        masked = normalized
        out: list[tuple[AliasHit, int, int]] = []
        for p, (etype, eid) in self._patterns:
            for m in self._regex[p].finditer(masked):
                out.append((AliasHit(etype, eid, p), m.start(), m.end()))
                masked = masked[: m.start()] + "#" * (m.end() - m.start()) + masked[m.end() :]
        return sorted(out, key=lambda x: x[1])

    def find(self, text: str) -> list[AliasHit]:
        t = normalize(text)
        masked = t
        hits: list[AliasHit] = []
        for p, (etype, eid) in self._patterns:
            rx = self._regex[p]
            found = False
            for m in rx.finditer(masked):
                found = True
                masked = masked[: m.start()] + "#" * (m.end() - m.start()) + masked[m.end() :]
            if found:
                hits.append(AliasHit(etype, eid, p))
        return hits

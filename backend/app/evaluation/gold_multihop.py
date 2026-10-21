"""Tier M — template-generated multi-hop questions (plan 2026-09-29 §4.6, PO decision D32).

An item combines one raw table cell (read by `rawtables.py`, exactly as tier A) with one or more
hops from `data/gold/multihop_v2.yaml`: a clause elsewhere in the corpus that the answer depends
on (default zone, zone definition, transition roadmap, replaced standard, footnote). Each item has
>= 2 grade-2 chunks in different places — the working definition of "multi-hop" for RQ6/RQ7.

Hops are anchored to raw source lines and their quotes are checked here; the graph and the parser
are never used to pick answers (only to map chunk ids / cross-check), so the set cannot favour
the graph retrieval it is used to evaluate.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import random
import re
from pathlib import Path
from typing import Any

import yaml

from app.core.config import Settings
from app.evaluation.gold_generate import (
    DOMAIN_OF_TABLE,
    Generator,
    equipment_phrase,
    norm_question,
    sample_cells,
)
from app.evaluation.gold_schema import (
    ExpectedNumber,
    GoldItem,
    Provenance,
    RelevantChunk,
    SourceRef,
    compute_difficulty,
)
from app.evaluation.rawtables import TABLE_SPECS, RawCell

TABLES = {
    "wastewater": ["QCVN40_2025.B1", "QCVN40_2025.B2"],
    "emission": ["QCVN19_2024.B1", "QCVN19_2024.B2"],
}
AS_OF = dt.date(2026, 1, 15)
AS_OF_2032 = dt.date(2032, 1, 15)
DATE_2032 = "2032-01-01"


class HopError(ValueError):
    pass


def load_spec(path: Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


class MultiHopGenerator:
    def __init__(
        self,
        settings: Settings,
        templates_a: Path,
        spec_path: Path,
        tier_a_splits: dict[str, str] | None = None,
    ) -> None:
        self.spec = load_spec(spec_path)
        self.tier_a_splits = tier_a_splits or {}
        self.seed = int(self.spec["seed"])
        self.base = Generator(settings, templates_a, self.seed)
        self.rng = random.Random(f"M:{self.seed}")
        self.hops = self._check_hops()

    # ------------------------------------------------------------ hops

    def _check_hops(self) -> dict[str, dict[str, Any]]:
        """Resolve every hop: raw quote present in its lines, chunk exists (label copied)."""
        out: dict[str, dict[str, Any]] = {}
        raw, resolver = self.base.raw, self.base.resolver
        for key, h in self.spec["hops"].items():
            f = raw.file(h["document_id"])
            ref = SourceRef(
                document_id=h["document_id"],
                file_sha256=f.sha256,
                line_start=h["line_start"],
                line_end=h["line_end"],
            )
            if not raw.quote_in(ref, h["quote"]):
                raise HopError(
                    f"hop {key}: quote not in {h['document_id']} lines {ref.line_start}-{ref.line_end}"
                )
            chunk = resolver.chunk(h["chunk_id"])
            if chunk is None:
                raise HopError(f"hop {key}: chunk {h['chunk_id']} not found")
            out[key] = {**h, "ref": ref, "citation_label": chunk["citation_label"]}
        return out

    def _hop(self, key: str, grade: int, note: str) -> RelevantChunk:
        h = self.hops[key]
        return RelevantChunk(
            chunk_id=h["chunk_id"],
            citation_label=h["citation_label"],
            grade=grade,  # type: ignore[arg-type]
            note=note,
            source_ref=h["ref"],
        )

    # ------------------------------------------------------------ cells

    def _eligible(self, pattern: str, domain: str) -> list[RawCell]:
        cells: list[RawCell] = []
        for t in TABLES[domain]:
            for c in self.base.reader.cells(t):
                kind = TABLE_SPECS[t]["kind"]
                if kind == "zone" and "." in c.row_key:
                    # sub-rows (receiving body / sector scope) need a row phrase to be unambiguous
                    if f"{t}.r{c.row_key}" not in self.base.t.get("row_phrases", {}):
                        continue
                if pattern == "DZ" and c.zone != "B":
                    continue
                if pattern == "FN_TOC" and not (t == "QCVN40_2025.B1" and c.row_key == "2.b"):
                    continue
                if pattern == "FN_O2" and c.o2_ref is None:
                    continue
                if pattern == "RP" and t != "QCVN40_2025.B1" and "." in c.row_key:
                    continue
                cells.append(c)
        return cells

    def _param_phrase(self, cell: RawCell) -> str:
        """Tier A phrasing, but never a head shorter than 5 characters ("Bis" is ambiguous)."""
        for _ in range(12):
            p = self.base._param_phrase(cell)
            if len(p) >= 5 or p == cell.parameter_short or p == cell.parameter:
                return p
        return cell.parameter

    def _zone_short(self, domain: str, zone: str) -> str:
        """Tier A column phrases without the parenthesised gloss (templates already nest them)."""
        opts = [z for z in self.spec_zone_phrases(domain, zone) if "(" not in z]
        return self.rng.choice(opts)

    def spec_zone_phrases(self, domain: str, zone: str) -> list[str]:
        return list(self.base.t["zone_phrases"][domain][zone])

    def _ctx(self, cell: RawCell) -> tuple[str, dict[str, Any]]:
        """Context phrase without the zone: flow band, sub-row phrase or equipment."""
        kind = TABLE_SPECS[cell.table_code]["kind"]
        if kind == "flow_zone":
            phrase, flow = self.base._flow_phrase(cell.flow_band or "F<=2000")
            return f", {phrase}", {"flow_value": flow}
        if kind == "zone":
            key = f"{cell.table_code}.r{cell.row_key}"
            if key in self.base.t.get("row_phrases", {}):
                return f" của {self.rng.choice(self.base.t['row_phrases'][key])}", {}
            return "", {}
        return f" của {equipment_phrase(cell)}", {}

    # ------------------------------------------------------------ items

    def item(
        self, pattern: str, domain: str, cell: RawCell, siblings: list[RawCell], idx: int
    ) -> GoldItem:
        base = self.base.item_for(cell, siblings, idx)
        ctx, ctx_slots = self._ctx(cell)
        tpl = self.rng.choice(self.spec["templates"][pattern][domain])
        slots: dict[str, Any] = {
            "param": "TOC" if pattern == "FN_TOC" else self._param_phrase(cell),
            "ctx": ctx,
            "zone": self._zone_short(domain, cell.zone),
        }
        dom_key = "WW" if domain == "wastewater" else "EM"
        factors = [f for f in base.difficulty_factors]
        hops: list[RelevantChunk] = []
        numbers = list(base.expected_numbers)
        as_of = AS_OF
        answer_suffix = ""
        must_not = list(base.expected_behaviour.must_not)
        trap = None
        if pattern == "DZ":
            hops.append(self._hop(f"DZ_{dom_key}", 2, "phân vùng mặc định → Cột B"))
            factors += ["default_zone", "cross_document"]
            answer_suffix = " (chưa xác định phân vùng → áp dụng Cột B)"
        elif pattern == "ZD":
            slots["zone_desc"] = self.rng.choice(self.spec["zone_descriptions"][domain][cell.zone])
            hops.append(self._hop(f"ZD_{dom_key}_{cell.zone}", 2, f"định nghĩa Cột {cell.zone}"))
            factors += ["zone_by_definition"]
            answer_suffix = f" (Cột {cell.zone})"
        elif pattern == "TR":
            hops.append(self._hop(f"TR_{dom_key}_ROADMAP", 2, "lộ trình: từ 01/01/2032"))
            hops.append(self._hop(f"TR_{dom_key}_SCOPE", 1, "đối tượng chuyển tiếp"))
            factors += ["temporal", "cross_document"]
            numbers.append(
                ExpectedNumber(
                    value=DATE_2032, display="01/01/2032", role="date", must_appear=False
                )
            )
            as_of = AS_OF_2032
            answer_suffix = " (từ ngày 01/01/2032)"
        elif pattern == "RP":
            hops.append(self._hop("RP_WW", 2, "QCVN 40:2025 thay thế QCVN 40:2011"))
            factors += ["partial_replacement"]
            must_not.append("use_replaced_values")
            trap = "replaced_standard"
            answer_suffix = " theo QCVN 40:2025/BTNMT (thay thế QCVN 40:2011/BTNMT)"
        elif pattern == "FN_TOC":
            hops.append(self._hop("FN_TOC", 2, "được chọn TOC hoặc COD"))
            answer_suffix = " (được chọn áp dụng TOC thay cho COD)"
        elif pattern == "FN_O2":
            key = "FN_O2_B1" if cell.table_code.endswith("B1") else "FN_O2_B2"
            hops.append(self._hop(key, 2, "giá trị trong ngoặc = ôxy tham chiếu"))
            # the template asks for the O2 reference explicitly: it must appear
            numbers = [
                n.model_copy(update={"must_appear": True}) if n.role == "o2_ref" else n
                for n in numbers
            ]
        factors = list(dict.fromkeys([*factors, "multi_hop"]))
        text = tpl["text"].format(**slots)
        text = re.sub(r"\s+", " ", text).replace(" ,", ",").replace(" ?", "?").replace(" )", ")")
        text = text.strip()
        if not text.startswith(("pH", "TOC")):
            text = text[:1].upper() + text[1:]
        # the row is grade 2; FN_TOC / tier A add the B1 COD/TOC footnote as grade 1 — the hop
        # version (grade 2) replaces it
        hop_ids = {h.chunk_id for h in hops}
        relevant = [r for r in base.relevant if r.chunk_id not in hop_ids] + hops
        labels = list(base.expected_citation_labels) + [
            h.citation_label for h in hops if h.grade == 2
        ]
        return base.model_copy(
            update={
                "id": f"G-TM-{idx:04d}",
                "tier": "M",
                "question": text,
                "stratum": f"M-{pattern}",
                "as_of_date": as_of,
                "difficulty_factors": factors,
                "difficulty": compute_difficulty(factors),
                "expected_answer": base.expected_answer + answer_suffix,
                "expected_numbers": numbers,
                "relevant": relevant,
                "expected_citation_labels": list(dict.fromkeys(labels)),
                "expected_behaviour": base.expected_behaviour.model_copy(
                    update={"must_not": list(dict.fromkeys(must_not))}
                ),
                "trap_type": trap,
                "tags": [
                    "tier_m",
                    f"pattern_{pattern.lower()}",
                    *[t for t in base.tags if t != "tier_a"],
                ],
                "provenance": Provenance(origin="template", template_id=tpl["id"]),
                "notes": f"slots: {slots | ctx_slots}; hops: {[h.chunk_id for h in hops]}",
            }
        )

    def generate(self) -> list[GoldItem]:
        items: list[GoldItem] = []
        seen: set[str] = set()
        idx = 1
        for pattern, per_domain in self.spec["quota"].items():
            for domain, n in per_domain.items():
                cells = self._eligible(pattern, domain)
                chosen = sample_cells(cells, n, random.Random(f"{self.seed}:{pattern}:{domain}"))
                siblings = {t: self.base.reader.cells(t) for t in TABLES[domain]}
                batch = []
                for cell in chosen:
                    it = None
                    for _ in range(8):
                        it = self.item(pattern, domain, cell, siblings[cell.table_code], idx)
                        if norm_question(it.question) not in seen:
                            break
                    assert it is not None
                    if norm_question(it.question) in seen:
                        continue
                    seen.add(norm_question(it.question))
                    batch.append(it)
                    idx += 1
                items += batch
        return assign_split(items, int(self.spec["dev_every"]), self.seed, self.tier_a_splits)


def assign_split(
    items: list[GoldItem], dev_every: int, seed: int, tier_a: dict[str, str] | None = None
) -> list[GoldItem]:
    """Cluster = table row. A row already in tier A keeps tier A's split (a row tuned on in dev
    never reappears in test); other rows: every `dev_every`-th (seeded order) → dev. All items
    of one row share the split, across patterns."""
    tier_a = tier_a or {}
    new = sorted(
        {it.cluster_id or it.id for it in items} - set(tier_a),
        key=lambda c: hashlib.sha1(f"{seed}:{c}".encode()).hexdigest(),
    )
    split = dict(tier_a) | {c: ("dev" if i % dev_every == 0 else "test") for i, c in enumerate(new)}
    return [it.model_copy(update={"split": split[it.cluster_id or it.id]}) for it in items]


def generate_multihop(
    settings: Settings, templates_a: Path, spec: Path, tier_a_file: Path | None = None
) -> list[GoldItem]:
    from app.evaluation.gold_schema import load_gold

    splits: dict[str, str] = {}
    if tier_a_file and tier_a_file.exists():
        for it in load_gold(tier_a_file, tier="A"):
            if it.cluster_id and it.split:
                splits[it.cluster_id] = it.split
    return MultiHopGenerator(settings, templates_a, spec, splits).generate()


__all__ = ["DOMAIN_OF_TABLE", "HopError", "MultiHopGenerator", "generate_multihop"]

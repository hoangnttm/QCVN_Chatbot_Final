"""Gold set validator (plan §2.5): schema-level consistency, chunk/label/rule existence, source
anchoring against the raw files, numbers grounded in the cited chunks, behaviour ↔ abstain
consistency, quotas/splits/coverage against `strata_v2.yaml`, duplicates.

Codes: E = error (exit 1), W = warning; `strict=True` (freeze) promotes quota/coverage/trap
warnings to errors."""

from __future__ import annotations

import datetime as dt
import difflib
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from app.evaluation.gold_resolver import Resolver
from app.evaluation.gold_schema import (
    GoldItem,
    RelevantChunk,
    Strata,
    compute_difficulty,
)
from app.evaluation.gold_source import RawSource, normalize_raw, quote_matches
from app.qa.validator import numbers

_DATE_RE = re.compile(r"\b(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})\b|\b(\d{4})-(\d{2})-(\d{2})\b")
_VN_DATE_RE = re.compile(r"ngày\s+(\d{1,2})\s+tháng\s+(\d{1,2})\s+năm\s+(\d{4})", re.I)


@dataclass
class Finding:
    code: str
    level: str  # E | W
    item_id: str | None
    message: str

    def __str__(self) -> str:
        who = self.item_id or "-"
        return f"{self.level}{self.code[1:]} [{who}] {self.message}"


@dataclass
class ValidationReport:
    findings: list[Finding] = field(default_factory=list)
    n_items: int = 0

    def add(self, code: str, level: str, item_id: str | None, message: str) -> None:
        self.findings.append(Finding(code, level, item_id, message))

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "E"]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "W"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def by_code(self) -> Counter:
        return Counter(f"{f.level}{f.code[1:]}" for f in self.findings)

    def render_markdown(self, title: str = "Gold validation") -> str:
        lines = [
            f"# {title}",
            "",
            f"Items: {self.n_items} · errors: {len(self.errors)} · warnings: {len(self.warnings)}",
            "",
        ]
        if self.findings:
            lines += ["| Code | Count |", "|---|---|"]
            for code, n in sorted(self.by_code().items()):
                lines.append(f"| {code} | {n} |")
            lines.append("")
            for f in self.findings:
                lines.append(f"- `{f.level}{f.code[1:]}` **{f.item_id or '-'}** — {f.message}")
        return "\n".join(lines) + "\n"


def _canon_float(v: Any) -> str:
    s = format(float(v), "f").rstrip("0").rstrip(".")
    return s or "0"


def label_matches(gold: str, chunk_label: str) -> bool:
    """Separator-aware prefix: a row-level chunk label may be a proper prefix of a column-level
    gold label only at a ', ' boundary (`…TT 1` never matches `…TT 12, Cột B`)."""
    return gold == chunk_label or gold.startswith(chunk_label + ", ")


def dates_in(text: str) -> set[str]:
    out = set()
    for m_ in _DATE_RE.finditer(text or ""):
        if m_.group(4):
            out.add(f"{m_.group(4)}-{m_.group(5)}-{m_.group(6)}")
        else:
            d, mo, y = int(m_.group(1)), int(m_.group(2)), m_.group(3)
            out.add(f"{y}-{mo:02d}-{d:02d}")
    for m_ in _VN_DATE_RE.finditer(text or ""):
        d, mo, y = int(m_.group(1)), int(m_.group(2)), m_.group(3)
        out.add(f"{y}-{mo:02d}-{d:02d}")
    return out


def _tokens(text: str) -> list[str]:
    return normalize_raw(text).split()


def _shared_run(a: list[str], b: list[str], n: int) -> bool:
    if len(a) < n or len(b) < n:
        return False
    grams = {tuple(b[i : i + n]) for i in range(len(b) - n + 1)}
    return any(tuple(a[i : i + n]) in grams for i in range(len(a) - n + 1))


def _norm_q(q: str) -> str:
    t = normalize_raw(q)
    t = re.sub(r"(?<![a-z0-9])\d+(?:[.,]\d+)?(?![a-z0-9])", "#", t)
    return re.sub(r"[^\w#\s]", "", t)


class GoldValidator:
    def __init__(
        self,
        resolver: Resolver,
        raw: RawSource | None,
        strata: Strata | None = None,
        *,
        strict: bool = False,
    ) -> None:
        self.r = resolver
        self.raw = raw
        self.strata = strata
        self.strict = strict
        self.rep = ValidationReport()

    # ------------------------------------------------------------ helpers

    def _w_or_e(self, code: str, item_id: str | None, msg: str) -> None:
        self.rep.add(code, "E" if self.strict else "W", item_id, msg)

    def _chunk_texts(self, chunks: list[RelevantChunk]) -> tuple[set[str], set[str], str]:
        nums: set[str] = set()
        dates: set[str] = set()
        joined = []
        for rc in chunks:
            c = self.r.chunk(rc.chunk_id)
            if not c:
                continue
            txt = f"{c.get('text', '')}\n{c.get('context_text', '')}\n{c.get('citation_label', '')}"
            nums |= numbers(txt)
            dates |= dates_in(txt)
            joined.append(c.get("text", ""))
        return nums, dates, "\n".join(joined)

    # ------------------------------------------------------------ per item

    def check_item(self, it: GoldItem) -> None:
        rep, r = self.rep, self.r
        iid = it.id

        # E03/E04 stratum & split
        if self.strata and it.stratum and it.stratum not in self.strata.by_code():
            rep.add("E03", "E", iid, f"unknown stratum {it.stratum!r}")
        if it.split is None:
            self._w_or_e("E04", iid, "split not assigned")

        # E22 source_ref against the raw file
        if it.source_ref and it.source_ref.kind == "registry":
            ref = it.source_ref
            if self.raw is not None:
                try:
                    f = self.raw.file(ref.document_id)
                    if f.sha256 != ref.file_sha256:
                        rep.add(
                            "E22",
                            "E",
                            iid,
                            f"registry source_ref sha256 differs for {ref.document_id}",
                        )
                except KeyError as e:
                    rep.add("E22", "E", iid, str(e))
        elif it.source_ref:
            ref = it.source_ref
            if self.raw is not None:
                try:
                    f = self.raw.file(ref.document_id)
                    if f.sha256 != ref.file_sha256:
                        rep.add(
                            "E22",
                            "E",
                            iid,
                            f"source_ref sha256 differs from registry for {ref.document_id}",
                        )
                    elif not self.raw.sha256_ok(ref.document_id):
                        rep.add(
                            "E22",
                            "E",
                            iid,
                            f"registered file for {ref.document_id} does not match its sha256",
                        )
                    n = len(self.raw.lines(ref.document_id))
                    if ref.line_end > n:
                        rep.add(
                            "E22",
                            "E",
                            iid,
                            f"source_ref lines {ref.line_start}-{ref.line_end} beyond file length {n}",
                        )
                except KeyError as e:
                    rep.add("E22", "E", iid, str(e))
        elif it.tier != "C" and it.expected_behaviour.mode not in ("abstain", "clarify"):
            self._w_or_e("E22", iid, "source_ref missing")

        # E05/E06/E23 relevant chunks
        for rc in it.relevant:
            c = r.chunk(rc.chunk_id)
            if c is None:
                rep.add("E05", "E", iid, f"chunk not found: {rc.chunk_id}")
                continue
            if c["citation_label"] != rc.citation_label:
                rep.add(
                    "E06",
                    "E",
                    iid,
                    f"label mismatch for {rc.chunk_id}: gold={rc.citation_label!r} db={c['citation_label']!r}",
                )
            ref = rc.source_ref or it.source_ref
            if ref and rc.grade == 2 and ref.document_id == c.get("document_id"):
                sp = c.get("span") or {}
                covers = (
                    sp.get("file_hash") == ref.file_sha256
                    and sp.get("line_start") is not None
                    and sp["line_start"] <= ref.line_start
                    and ref.line_end <= sp["line_end"]
                )
                if not covers:
                    rep.add(
                        "E23",
                        "E",
                        iid,
                        f"grade-2 chunk {rc.chunk_id} span {sp.get('line_start')}-{sp.get('line_end')}"
                        f" does not cover source_ref {ref.line_start}-{ref.line_end}",
                    )

        # E07 expected_citation_labels
        g2_labels = [rc.citation_label for rc in it.grade2()]
        for lbl in it.expected_citation_labels:
            if not any(label_matches(lbl, g) for g in g2_labels):
                rep.add(
                    "E07",
                    "E",
                    iid,
                    f"expected label not backed by a grade-2 relevant chunk: {lbl!r}",
                )
            if not r.label_exists(lbl):
                rep.add(
                    "E07",
                    "E",
                    iid,
                    f"expected label not found in evidence_chunks/limit_rules: {lbl!r}",
                )

        # E08 behaviour vs relevant grades
        mode = it.expected_behaviour.mode
        g2 = it.grade2()
        if mode in ("answer", "pointer") and not g2:
            rep.add("E08", "E", iid, f"mode={mode} but no grade-2 relevant chunk")
        if it.domain == "out_of_scope" and it.relevant_ids(1):
            rep.add(
                "E08", "E", iid, "out_of_scope item must not have relevant chunks of grade >= 1"
            )

        # E09 expected numbers grounded in grade-2 chunk texts
        nums, dates, _ = self._chunk_texts(g2)
        for en in it.expected_numbers:
            if en.role == "date":
                if en.value not in dates and en.value not in nums:
                    self._w_or_e(
                        "E09", iid, f"expected date {en.value!r} not found in grade-2 chunks"
                    )
            elif en.value not in nums:
                if en.role == "other" and not en.must_appear:
                    rep.add(
                        "W09", "W", iid, f"number {en.value!r} (role=other) not in grade-2 chunks"
                    )
                else:
                    rep.add(
                        "E09", "E", iid, f"expected number {en.value!r} not in grade-2 chunk text"
                    )
        # W01 numbers in expected_answer that are neither declared nor grounded
        if not it.must_abstain and it.expected_numbers:
            declared = {en.value for en in it.expected_numbers}
            stray = numbers(it.expected_answer) - numbers(it.question) - declared - nums
            if stray:
                rep.add(
                    "W01",
                    "W",
                    iid,
                    f"numbers in expected_answer not declared/grounded: {sorted(stray)}",
                )

        # E10 target rules and uniqueness
        if it.target_rule_codes:
            rules = []
            for code in it.target_rule_codes:
                rule = r.rule(code)
                if rule is None:
                    tr = r.transition(code) or r.monitoring(code)
                    if tr is None:
                        rep.add("E10", "E", iid, f"target not found: {code}")
                    continue
                rules.append(rule)
            if rules and it.intent == "threshold":
                want = {
                    en.value
                    for en in it.expected_numbers
                    if en.role in ("limit", "range_min", "range_max")
                }
                have: set[str] = set()
                for rule in rules:
                    for k in ("value", "min", "max"):
                        if rule.get(k) is not None:
                            have.add(_canon_float(rule[k]))
                if want and not want <= have:
                    rep.add(
                        "E10",
                        "E",
                        iid,
                        f"expected numbers {sorted(want)} differ from target rule values"
                        f" {sorted(have)} (parser finding? record in parser_findings)",
                    )
                axes = it.expected_conditions.axes()
                pid = rules[0].get("parameter_id")
                tcode = rules[0].get("table_code")
                if pid and tcode and axes:
                    matched = {x["rule_code"] for x in r.rules_matching(tcode, pid, **axes)}
                    targets = set(it.target_rule_codes)
                    if matched != targets:
                        rep.add(
                            "E10",
                            "E",
                            iid,
                            f"ambiguous: conditions {axes} match {sorted(matched)[:6]} ≠ targets {sorted(targets)}",
                        )
                elif not axes:
                    rep.add(
                        "E10",
                        "E",
                        iid,
                        "threshold item without expected_conditions axes (E10-ambiguous)",
                    )

        # E11 must_abstain ↔ mode
        if it.must_abstain != (mode in ("abstain", "pointer", "clarify")):
            rep.add(
                "E11", "E", iid, f"must_abstain={it.must_abstain} inconsistent with mode={mode}"
            )

        # E12 warnings implied by cited chunks
        for rc in g2:
            c = r.chunk(rc.chunk_id) or {}
            if c.get("text_status") == "draft" and "draft_document" not in it.expected_warnings:
                rep.add(
                    "E12",
                    "E",
                    iid,
                    f"grade-2 chunk {rc.chunk_id} is draft text → expected_warnings needs draft_document",
                )
            if (
                c.get("chunk_type") == "registry_fact"
                and "khai báo" in (c.get("text") or "")
                and "admin_declared_fact" not in it.expected_warnings
            ):
                rep.add(
                    "E12",
                    "E",
                    iid,
                    f"registry fact {rc.chunk_id} is admin-declared → expected_warnings needs admin_declared_fact",
                )

        # E13 verification when self_verified or better
        if it.check_level in ("self_verified", "double_checked"):
            v = it.verification
            if not (v.verified_by and v.verified_at and v.source_quote):
                rep.add(
                    "E13",
                    "E",
                    iid,
                    "check_level >= self_verified requires verified_by, verified_at, source_quote",
                )
            elif it.source_ref and it.source_ref.kind == "registry":
                texts = " ".join((r.chunk(rc.chunk_id) or {}).get("text", "") for rc in g2)
                if not quote_matches(v.source_quote, texts):
                    rep.add("E13", "E", iid, "source_quote not found in the registry fact text")
            elif it.source_ref and self.raw is not None:
                try:
                    if not self.raw.quote_in(it.source_ref, v.source_quote):
                        rep.add(
                            "E13",
                            "E",
                            iid,
                            "source_quote not found in the raw source lines of source_ref",
                        )
                except KeyError as e:
                    rep.add("E13", "E", iid, str(e))
            if it.check_level == "double_checked" and not v.second_check_by:
                rep.add("E13", "E", iid, "double_checked requires second_check_by")

        # E14 provenance
        is_para = it.source == "paraphrase_llm_verified"
        has_para = bool(it.provenance and it.provenance.origin == "llm_paraphrase")
        if is_para != has_para:
            rep.add(
                "E14", "E", iid, "source=paraphrase_llm_verified ⇔ provenance.origin=llm_paraphrase"
            )
        if (
            is_para
            and it.provenance
            and not (it.provenance.llm_model and it.provenance.original_question)
        ):
            rep.add("E14", "E", iid, "paraphrase provenance needs llm_model and original_question")
        if it.trap_type == "injection" and it.provenance and it.provenance.origin != "human":
            rep.add("E14", "E", iid, "injection traps must be human-written")
        if it.source == "template_generated" and not (
            it.provenance and it.provenance.origin == "template"
        ):
            rep.add("E14", "E", iid, "template_generated items need provenance.origin=template")

        # E15 as_of_date
        if (
            it.intent in ("effectivity", "applicability")
            or "temporal" in it.tags
            or "temporal" in it.difficulty_factors
        ) and it.as_of_date is None:
            rep.add(
                "E15", "E", iid, "as_of_date required for effectivity/applicability/temporal items"
            )

        # E21 difficulty
        if it.difficulty is not None and it.difficulty != compute_difficulty(it.difficulty_factors):
            rep.add(
                "E21",
                "E",
                iid,
                f"difficulty {it.difficulty} ≠ computed {compute_difficulty(it.difficulty_factors)}",
            )

        # W02 verbatim reuse of chunk text
        _, _, text = self._chunk_texts(g2)
        if (
            it.tier not in ("A", "M")
            and text
            and _shared_run(_tokens(it.question), _tokens(text), 8)
        ):
            rep.add(
                "W02",
                "W",
                iid,
                "question shares >= 8 consecutive tokens with the target chunk text",
            )

        # E20-ish per item: trap items need a must_not or abstain
        if it.trap_type and not it.expected_behaviour.must_not and not it.must_abstain:
            rep.add("W20", "W", iid, "trap item without must_not and not abstaining")

    # ------------------------------------------------------------ dataset level

    def check_dataset(self, items: list[GoldItem]) -> None:
        rep = self.rep
        ids = Counter(it.id for it in items)
        for iid, n in ids.items():
            if n > 1:
                rep.add("E02", "E", iid, f"duplicate id ({n}×)")

        # E18/W18 duplicates
        norm = defaultdict(list)
        for it in items:
            norm[_norm_q(it.question)].append(it.id)
        for _k, group in norm.items():
            if len(group) > 1:
                rep.add("E18", "E", group[0], f"identical normalized question: {group}")
        by_tier = defaultdict(list)
        for it in items:
            by_tier[it.tier].append(it)
        for tier, lst in by_tier.items():
            if tier in ("A", "M"):
                continue  # template items are near-duplicates by design (checked by cluster_id)
            qs = [(it.id, _norm_q(it.question)) for it in lst]
            for i in range(len(qs)):
                for j in range(i + 1, len(qs)):
                    if qs[i][1] == qs[j][1]:
                        continue
                    if difflib.SequenceMatcher(None, qs[i][1], qs[j][1]).ratio() >= 0.85:
                        a = next(x for x in lst if x.id == qs[i][0])
                        b = next(x for x in lst if x.id == qs[j][0])
                        if a.paired_with == b.id or b.paired_with == a.id:
                            continue
                        rep.add("W18", "W", qs[i][0], f"near-duplicate of {qs[j][0]}")

        # E17 twins share split; v1_migrated in dev
        by_id = {it.id: it for it in items}
        for it in items:
            if it.paired_with:
                tw = by_id.get(it.paired_with)
                if tw is None:
                    rep.add("E17", "E", it.id, f"paired_with {it.paired_with} not found")
                elif tw.split != it.split:
                    rep.add("E17", "E", it.id, f"twin {tw.id} has split {tw.split} ≠ {it.split}")
            if it.source == "v1_migrated" and it.split != "dev":
                rep.add("E17", "E", it.id, "v1_migrated items must be in dev")

        if not self.strata or not any(it.tier == "B" for it in items):
            return
        strata = self.strata
        # E16/W16 quotas (tier B strata only)
        counts: dict[tuple[str, str | None], int] = Counter()
        for it in items:
            if it.tier == "B" and it.stratum:
                counts[(it.stratum, it.split)] += 1
        for s in strata.strata:
            if s.tier != "B":
                continue
            ndev = counts[(s.code, "dev")]
            ntest = counts[(s.code, "test")]
            if ndev > s.quota_dev:
                rep.add("E16", "E", None, f"stratum {s.code}: dev {ndev} > quota {s.quota_dev}")
            if ntest > s.quota_test:
                rep.add("E16", "E", None, f"stratum {s.code}: test {ntest} > quota {s.quota_test}")
            if ndev < s.quota_dev or ntest < s.quota_test:
                self._w_or_e(
                    "E16",
                    None,
                    f"stratum {s.code}: dev {ndev}/{s.quota_dev}, test {ntest}/{s.quota_test}",
                )

        # paraphrase caps
        b_items = [it for it in items if it.tier == "B"]
        n_para = sum(1 for it in b_items if it.source == "paraphrase_llm_verified")
        if b_items and n_para / len(b_items) > strata.paraphrase_max_share:
            rep.add(
                "E14",
                "E",
                None,
                f"paraphrase share {n_para}/{len(b_items)} exceeds {strata.paraphrase_max_share:.0%}",
            )
        per_s = Counter(it.stratum for it in b_items if it.source == "paraphrase_llm_verified")
        for code, n in per_s.items():
            if n > strata.paraphrase_max_per_stratum:
                rep.add(
                    "E14",
                    "E",
                    None,
                    f"stratum {code}: {n} paraphrases > {strata.paraphrase_max_per_stratum}",
                )

        # E20 traps
        n_trap = sum(1 for it in b_items if it.trap_type)
        if n_trap < strata.trap_min:
            self._w_or_e("E20", None, f"trap items {n_trap} < {strata.trap_min}")

        # W19 coverage
        self._coverage(b_items)

    def _coverage(self, items: list[GoldItem]) -> None:
        for rule in self.strata.coverage if self.strata else []:
            kind = rule.kind
            if kind == "table":
                for tcode in rule.values:
                    n = sum(
                        1
                        for it in items
                        if any(x.startswith(tcode + ".") for x in it.target_rule_codes)
                        or (it.source_ref and it.source_ref.table_code == tcode)
                        or any(
                            (self.r.chunk(rc.chunk_id) or {})
                            .get("source_id", "")
                            .startswith(tcode + ".")
                            for rc in it.relevant
                        )
                    )
                    if n < (rule.min_each or 1):
                        self._w_or_e(
                            "W19",
                            None,
                            f"coverage {rule.name}: table {tcode} has {n} < {rule.min_each or 1}",
                        )
            elif kind in ("zone", "flow_band", "o2_ref", "fuel", "receiving_body"):
                for v in rule.values:
                    doms = rule.domains or [None]
                    for dom in doms:
                        n = 0
                        for it in items:
                            if it.intent != "threshold" or (dom and it.domain != dom):
                                continue
                            got = getattr(it.expected_conditions, kind)
                            if got is not None and (
                                str(got) == str(v) or (kind == "o2_ref" and float(got) == float(v))
                            ):
                                n += 1
                        if n < (rule.min_each or 1):
                            where = f" ({dom})" if dom else ""
                            self._w_or_e(
                                "W19",
                                None,
                                f"coverage {rule.name}: {kind}={v}{where} has {n} < {rule.min_each or 1}",
                            )
            elif kind == "capacity":
                for v in rule.values:
                    n = sum(
                        1
                        for it in items
                        if it.expected_conditions.capacity_text
                        and str(v).lower() in it.expected_conditions.capacity_text.lower()
                    )
                    if n < (rule.min_each or 1):
                        self._w_or_e(
                            "W19",
                            None,
                            f"coverage {rule.name}: capacity '{v}' has {n} < {rule.min_each or 1}",
                        )
            elif kind == "tag":
                for v in rule.values:
                    n = sum(1 for it in items if v in it.tags)
                    if n < (rule.min_each or 1):
                        self._w_or_e(
                            "W19",
                            None,
                            f"coverage {rule.name}: tag {v} has {n} < {rule.min_each or 1}",
                        )
            elif kind in ("equipment_depth3", "inheritance", "exception", "footnote"):
                n = sum(1 for it in items if kind in it.difficulty_factors)
                if n < (rule.min_total or 1):
                    self._w_or_e(
                        "W19",
                        None,
                        f"coverage {rule.name}: factor {kind} has {n} < {rule.min_total or 1}",
                    )
            elif kind == "trap":
                n = sum(1 for it in items if it.trap_type)
                if n < (rule.min_total or 1):
                    self._w_or_e("W19", None, f"coverage {rule.name}: traps {n} < {rule.min_total}")
            elif kind == "frequency":
                n = sum(1 for it in items if it.intent == "frequency")
                if n < (rule.min_total or 1):
                    self._w_or_e(
                        "W19", None, f"coverage {rule.name}: frequency items {n} < {rule.min_total}"
                    )
            elif kind == "as_of":
                bad = [
                    it.id
                    for it in items
                    if it.intent in ("effectivity", "applicability") and it.as_of_date is None
                ]
                if bad:
                    self._w_or_e("W19", None, f"coverage {rule.name}: as_of_date missing on {bad}")
            elif kind == "alias_share":
                thr = [it for it in items if it.intent == "threshold"]
                n = sum(1 for it in thr if "alias_variant" in it.tags)
                if thr and n / len(thr) < (rule.min_share or 0):
                    self._w_or_e(
                        "W19",
                        None,
                        f"coverage {rule.name}: alias_variant share {n}/{len(thr)} < {rule.min_share:.0%}",
                    )

    # ------------------------------------------------------------ entry

    def run(self, items: list[GoldItem]) -> ValidationReport:
        self.rep = ValidationReport(n_items=len(items))
        for it in items:
            self.check_item(it)
        self.check_dataset(items)
        return self.rep


def validate(
    items: list[GoldItem],
    resolver: Resolver,
    raw: RawSource | None = None,
    strata: Strata | None = None,
    *,
    strict: bool = False,
    date: dt.date | None = None,
) -> ValidationReport:
    return GoldValidator(resolver, raw, strata, strict=strict).run(items)


def freeze_check(
    items: list[GoldItem], resolver: Resolver, raw: RawSource, strata: Strata
) -> ValidationReport:
    rep = validate(items, resolver, raw, strata, strict=True)
    for it in items:
        if it.tier == "B" and it.check_level not in ("self_verified", "double_checked"):
            rep.add(
                "E13",
                "E",
                it.id,
                f"freeze requires check_level >= self_verified (is {it.check_level})",
            )
        if it.check_level == "disputed":
            rep.add("E13", "E", it.id, "disputed items cannot be frozen into test")
    return rep


__all__: list[Any] = [
    "Finding",
    "ValidationReport",
    "GoldValidator",
    "validate",
    "freeze_check",
    "label_matches",
    "dates_in",
]

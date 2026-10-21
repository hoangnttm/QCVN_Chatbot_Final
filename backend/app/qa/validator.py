"""Citation validator (PLAN.md §8, 7 rules). Runs after the LLM, before anything reaches the user.

Rules: (1) assertive answer must cite; (2) every chunk_id must be in the evidence pack;
(3) every number in conclusion/basis must occur in a cited chunk (question echoes excluded);
(4) cross-domain citations warn, only-cross-domain rejects; (5) not-in-force chunks are filtered
before retrieval returns (retriever); (6) draft documents must carry a draft warning, and admin-declared
dates (registry facts) must be cited from the registry-fact chunk, not from the body text;
(7) regeneration is handled by the service.
"""

from __future__ import annotations

import re

from app.ontology import corpus as corpus_mod
from app.qa.models import Answer, ValidationResult
from app.retrieval.query import QueryUnderstanding
from app.retrieval.retriever import EvidenceItem

_NUM = re.compile(r"\d+(?:[.,]\d+)*")
_IGNORE = {"1", "2", "3"}


ADMIN_DECLARED_WARNING = (
    "Ngày do quản trị viên khai báo trong sổ đăng ký (văn bản không in ngày), "
    "không lấy từ nội dung văn bản"
)


def numbers(text: str) -> set[str]:
    """Canonical numeric tokens: `0,05`→`0.05`, `3.000`→`3000`, `6,0`→`6`, `1.234,5`→`1234.5`."""
    out = set()
    for n in _NUM.findall(text or ""):
        if re.fullmatch(r"[1-9]\d{0,2}(\.\d{3})+", n):  # thousands with dots (never "0.001")
            n2 = n.replace(".", "")
        elif "," in n and "." in n:  # 1.234,5
            n2 = n.replace(".", "").replace(",", ".")
        else:
            n2 = n.replace(",", ".")
        try:
            f = float(n2)
            n2 = format(f, "f").rstrip("0").rstrip(".") or "0"
        except ValueError:
            pass
        out.add(n2)
    return out


def validate(
    ans: Answer, evidence: list[EvidenceItem], qu: QueryUnderstanding, question: str
) -> tuple[ValidationResult, Answer]:
    """Returns (result, possibly amended answer): warnings are added in place (rule 6), errors reject."""
    errors: list[str] = []
    warns: list[str] = []
    checked: list[str] = []
    by_id = {e.chunk_id: e for e in evidence}
    cited = ans.all_citations()

    checked.append("1-citation-required")
    if not ans.abstained and not cited:
        errors.append("câu trả lời khẳng định nhưng không có citation")

    checked.append("2-citation-in-pack")
    bad = [c for c in cited if c not in by_id]
    if bad:
        errors.append("chunk_id không có trong evidence: " + ", ".join(bad[:5]))
    valid = [by_id[c] for c in cited if c in by_id]

    checked.append("3-numbers-grounded")
    if not ans.abstained:
        claimed = numbers(ans.conclusion) | {n for b in ans.basis for n in numbers(b.text)}
        grounded = set()
        for e in valid:
            grounded |= numbers(e.text) | numbers(e.citation_label) | numbers(e.document_code)
        echo = numbers(question) | (numbers(str(qu.as_of_date)) if qu.as_of_date else set())
        loose = sorted(
            n for n in claimed if n not in grounded and n not in echo and n not in _IGNORE
        )
        if loose:
            errors.append("số liệu không có trong chunk được trích: " + ", ".join(loose[:8]))

    checked.append("4-cross-domain")
    corpus = corpus_mod.current()
    if corpus.is_regulated(qu.domain) and valid:
        others = corpus.other_domains(qu.domain)
        cross = [e for e in valid if e.domain in others]
        if cross and len(cross) == len(valid) and not ans.abstained:
            errors.append("chỉ trích dẫn văn bản thuộc miền khác câu hỏi")
        elif cross:
            warns.append(
                "Có trích dẫn thuộc miền khác câu hỏi: "
                + "; ".join(e.citation_label for e in cross[:3])
            )

    checked.append("6-draft-and-registry-fact")
    drafts = sorted({e.document_code for e in valid if e.text_status == "draft"})
    for code in drafts:
        if not any("dự thảo" in w.lower() and code.split("/")[0] in w for w in ans.warnings):
            ans.warnings.append(f"Căn cứ từ bản dự thảo {code}, chưa đối chiếu bản ký chính thức")
    facts = [e for e in valid if e.chunk_type == "registry_fact"]
    for e in facts:
        if "quản trị viên khai báo" in e.citation_label and not any(
            "quản trị viên" in w for w in ans.warnings
        ):
            ans.warnings.append(ADMIN_DECLARED_WARNING)
    if qu.intent == "effectivity" and not ans.abstained:
        # documents whose number/dates are admin-declared (their text leaves them blank) and
        # draft documents: the date may only be cited from the registry-fact chunk
        pack_facts = [e for e in evidence if e.chunk_type == "registry_fact"]
        declared_docs = {
            e.document_id for e in pack_facts if "quản trị viên khai báo" in e.citation_label
        }
        cites_body = any(
            e.chunk_type != "registry_fact"
            and (e.text_status == "draft" or e.document_id in declared_docs)
            for e in valid
        )
        if pack_facts and cites_body and not facts:
            errors.append(
                "ngày ký/hiệu lực do quản trị viên khai báo phải trích chunk 'Sổ đăng ký văn bản', "
                "không lấy từ nội dung văn bản (văn bản không in ngày)"
            )

    checked.append("abstain-reason")
    if ans.abstained and not ans.abstain_reason:
        ans.abstain_reason = "Không đủ căn cứ trong các văn bản của hệ thống."
    for w in warns:
        if w not in ans.warnings:
            ans.warnings.append(w)
    return ValidationResult(
        ok=not errors, errors=errors, warnings=warns, rules_checked=checked
    ), ans

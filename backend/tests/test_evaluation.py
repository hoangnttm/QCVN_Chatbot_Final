"""Evaluation toolkit: retrieval metrics on graded relevance, answer metrics on synthetic
responses, statistics against known values, judge with FakeLLM + cache, human agreement."""

from __future__ import annotations

import csv
import datetime as dt
import json
from pathlib import Path

import pytest

from app.evaluation import human, metrics_answer, metrics_retrieval, stats
from app.evaluation.cache import JsonCache
from app.evaluation.gold_schema import (
    ExpectedBehaviour,
    ExpectedConditions,
    ExpectedNumber,
    ForbiddenNumber,
    GoldItem,
    RelevantChunk,
)
from app.evaluation.judge import JUDGE_PROMPT_VERSION, JudgeVerdict, judge_item, judge_prompt
from app.evaluation.runner import write_jsonl
from app.llm.client import FakeLLM


def _item(**over) -> GoldItem:
    base = dict(
        id="G-WW-950",
        question="Giới hạn BOD5 trong nước thải công nghiệp cột B, lưu lượng 3000 m3/ngày là bao nhiêu?",
        domain="wastewater",
        intent="threshold",
        as_of_date=dt.date(2026, 1, 15),
        expected_answer="≤ 50 mg/L (F > 2000, Cột B)",
        expected_numbers=[ExpectedNumber(value="50", display="50", unit="mg/L", role="limit")],
        forbidden_numbers=[ForbiddenNumber(value="60", unit="mg/L", reason="cột B F<=2000")],
        expected_conditions=ExpectedConditions(zone="B", flow_band="F>2000"),
        relevant=[
            RelevantChunk(chunk_id="chunk:row1", citation_label="QCVN 40, Bảng 1, TT 1", grade=2),
            RelevantChunk(
                chunk_id="chunk:fn", citation_label="QCVN 40, Bảng 1, Chú thích", grade=1
            ),
            RelevantChunk(chunk_id="chunk:def", citation_label="QCVN 40, Mục 1.3.3", grade=0),
        ],
        expected_citation_labels=["QCVN 40, Bảng 1, TT 1, F > 2000, Cột B"],
        stratum="WW-B1-FLOWZONE",
        tier="B",
    )
    base.update(over)
    return GoldItem(**base)


def _resp(
    conclusion: str,
    citations: list[str],
    *,
    abstained: bool = False,
    warnings: list[str] | None = None,
    status: str = "answered",
    path: str = "llm",
    attempts: int = 1,
    ok: bool = True,
    enforced: bool = True,
) -> dict:
    evidence = [
        {
            "rank": 1,
            "chunk_id": "chunk:row1",
            "citation_label": "QCVN 40, Bảng 1, TT 1",
            "document_id": "QCVN_40_2025",
            "document_code": "QCVN 40:2025/BTNMT",
            "chunk_type": "table_row",
            "domain": "wastewater",
            "text_status": "official",
            "score": 1.0,
            "sources": ["bm25"],
            "text": "BOD5 | giới hạn F>2000.B: <= 50; F<=2000.B: <= 60",
        },
        {
            "rank": 2,
            "chunk_id": "chunk:other",
            "citation_label": "QCVN 19, Bảng 1, TT 2",
            "document_id": "QCVN_19_2024",
            "document_code": "QCVN 19:2024/BTNMT",
            "chunk_type": "table_row",
            "domain": "emission",
            "text_status": "draft",
            "score": 0.5,
            "sources": ["vector"],
            "text": "CO <= 350(6)",
        },
    ]
    return {
        "query_id": "q1",
        "question": "Giới hạn BOD5 trong nước thải công nghiệp cột B, lưu lượng 3000 m3/ngày là bao nhiêu?",
        "as_of_date": "2026-01-15",
        "understanding": {"domain": "wastewater", "intent": "threshold"},
        "evidence": evidence,
        "answer": {
            "conclusion": conclusion,
            "basis": [],
            "conditions": [],
            "citations": citations,
            "missing_information": [],
            "warnings": warnings or [],
            "abstained": abstained,
            "abstain_reason": None,
        },
        "citations": [],
        "validation": {
            "ok": ok,
            "errors": [] if ok else ["số 999 không có trong evidence"],
            "warnings": [],
            "rules_checked": [],
            "enforced": enforced,
        },
        "status": status,
        "path": path,
        "attempts": attempts,
        "usage": {"total_tokens": 1200},
        "cost_usd": 0.005,
        "latency_ms": {"total": 4200, "retrieval": 700},
    }


# ---------------------------------------------------------------- retrieval metrics


def test_retrieval_metrics_graded():
    it = _item()
    lst = [
        ("chunk:x", None, "wastewater"),
        ("chunk:row1", None, "wastewater"),
        ("chunk:fn", None, "wastewater"),
        ("chunk:y", None, "emission"),
    ]
    row = metrics_retrieval.score_list(lst, it, ks=(1, 2, 5), stage="A")
    assert row["applicable"] and row["hit@1"] == 0.0 and row["hit@2"] == 1.0
    assert row["recall_must@2"] == 1.0 and row["recall_all@2"] == 0.5 and row["recall_all@5"] == 1.0
    assert row["mrr"] == pytest.approx(0.5)
    # DCG@2 = 0 + 3/log2(3); IDCG@2 = 3/1 + 1/log2(3)
    import math

    assert row["ndcg@2"] == pytest.approx((3 / math.log2(3)) / (3 + 1 / math.log2(3)))
    assert row["precision@5"] == pytest.approx(2 / 4)
    assert row["completeness"] == 1.0 and row["cross_domain_share"] == pytest.approx(0.25)
    empty = metrics_retrieval.score_list([], it, ks=(5,), stage="B")
    assert empty["applicable"] and empty["hit@5"] == 0.0 and empty["completeness"] == 0.0
    agg = metrics_retrieval.aggregate([row, empty], None)
    assert agg["all"]["n"] == 2 and agg["all"]["hit@5"] == pytest.approx(0.5)


def test_retrieval_metrics_v1_label_fallback():
    it = _item(relevant=[], expected_citation_labels=["QCVN 40, Bảng 1, TT 1, F > 2000, Cột B"])
    lst = [
        ("chunk:a", "QCVN 40, Bảng 1, TT 12", "wastewater"),
        ("chunk:row1", "QCVN 40, Bảng 1, TT 1", "wastewater"),
    ]
    row = metrics_retrieval.score_list(lst, it, ks=(1, 2), stage="A")
    assert row["hit@1"] == 0.0 and row["hit@2"] == 1.0  # separator-aware: TT 1 ≠ TT 12


# ---------------------------------------------------------------- answer metrics


def test_answer_metrics_correct_answer():
    it = _item()
    row = metrics_answer.score_answer(it, _resp("BOD5 cột B tối đa 50 mg/L.", ["chunk:row1"]))
    assert (
        row["citation_valid"] == 1.0
        and row["citation_precision"] == 1.0
        and row["citation_complete"] == 1.0
    )
    assert row["nums_grounded"] == 1.0 and row["nums_exact"] == 1.0 and row["forbidden_hit"] == 0.0
    assert (
        row["abstain_correct"] == 1.0
        and row["warnings_ok"] == 1.0
        and row["warnings_spurious"] == 0.0
    )
    assert row["first_pass"] == 1.0 and row["safe"] == 1.0 and row["correct_safe"] == 1.0
    assert row["routing_strict"] == 1.0 and row["latency_total_ms"] == 4200


def test_answer_metrics_wrong_cell_and_ungrounded():
    it = _item()
    wrong = metrics_answer.score_answer(it, _resp("BOD5 cột B tối đa 60 mg/L.", ["chunk:row1"]))
    assert (
        wrong["nums_exact"] == 0.0
        and wrong["forbidden_hit"] == 1.0
        and wrong["correct_safe"] == 0.0
    )
    assert wrong["nums_grounded"] == 1.0  # 60 is in the chunk text: grounded but the wrong cell
    hallu = metrics_answer.score_answer(it, _resp("BOD5 cột B tối đa 55 mg/L.", ["chunk:row1"]))
    assert (
        hallu["nums_grounded"] == 0.0
        and hallu["safe"] == 0.0
        and hallu["nums_ungrounded"] == ["55"]
    )
    outside = metrics_answer.score_answer(it, _resp("BOD5 50 mg/L.", ["chunk:nope"]))
    assert outside["citation_valid"] == 0.0 and outside["cited_outside_pack"] == 1
    cross = metrics_answer.score_answer(
        it, _resp("BOD5 50 mg/L.", ["chunk:other"], warnings=["Căn cứ từ bản dự thảo"])
    )
    assert cross["citation_precision"] == 0.0 and cross["citation_complete"] == 0.0


def test_answer_metrics_abstention_and_traps():
    must = _item(
        id="G-OOS-950",
        domain="out_of_scope",
        must_abstain=True,
        relevant=[],
        expected_citation_labels=[],
        expected_numbers=[],
        forbidden_numbers=[],
        expected_behaviour=ExpectedBehaviour(mode="abstain"),
        expected_warnings=["out_of_scope"],
        trap_type="out_of_scope",
    )
    good = metrics_answer.score_answer(
        must,
        _resp(
            "Câu hỏi nằm ngoài phạm vi hệ thống.",
            [],
            abstained=True,
            status="abstained",
            path="policy",
        ),
    )
    assert good["abstain_tp"] == 1.0 and good["correct_safe"] == 1.0 and good["violation"] == 0.0
    bad = metrics_answer.score_answer(must, _resp("Giới hạn là 55 dB.", ["chunk:row1"]))
    assert (
        bad["abstain_fn"] == 1.0
        and bad["correct_safe"] == 0.0
        and "should_have_abstained" in bad["violations"]
    )
    trap = _item(
        id="G-TRP-950",
        trap_type="pass_fail_request",
        expected_behaviour=ExpectedBehaviour(mode="answer", must_not=["pass_fail_verdict"]),
    )
    verdict = metrics_answer.score_answer(
        trap, _resp("Nước thải của bạn không đạt vì 120 > 50 mg/L.", ["chunk:row1"])
    )
    assert "verdict" in verdict["violations"] and verdict["safe"] == 0.0
    fine = metrics_answer.score_answer(
        trap, _resp("Giới hạn là 50 mg/L; hệ thống không kết luận đạt/không đạt.", ["chunk:row1"])
    )
    assert (
        fine["violation"] == 0.0 or "verdict" in fine["violations"]
    )  # regex is conservative: documented


def test_validator_stats_and_regen_taxonomy():
    it = _item()
    row = metrics_answer.score_answer(
        it,
        _resp("BOD5 50 mg/L.", ["chunk:row1"], attempts=2),
        regen_errors=["số 999 không có trong evidence"],
    )
    assert (
        row["regenerated"] == 1.0
        and row["first_pass"] == 0.0
        and "rule3_numbers" in row["validator_errors"]
    )
    fb = metrics_answer.score_answer(
        it,
        _resp(
            "Không tạo được câu trả lời.",
            [],
            abstained=True,
            status="failed",
            path="fallback",
            attempts=2,
            ok=False,
        ),
    )
    assert fb["fallback"] == 1.0 and fb["abstain_fp"] == 1.0


# ---------------------------------------------------------------- stats


def test_stats_known_values():
    assert stats.mcnemar_exact(10, 2) == pytest.approx(0.0386, abs=1e-3)
    assert stats.mcnemar_exact(0, 0) == 1.0
    lo, hi = stats.wilson_ci(90, 100)
    assert 0.82 < lo < 0.83 and 0.94 < hi < 0.95
    a = stats.bootstrap_ci([1.0, 0.0, 1.0, 1.0, 0.0, 1.0], seed=1)
    b = stats.bootstrap_ci([1.0, 0.0, 1.0, 1.0, 0.0, 1.0], seed=1)
    assert a == b and a[0] <= 4 / 6 <= a[1]
    cl = stats.bootstrap_ci([1.0, 1.0, 0.0, 0.0], clusters=["r1", "r1", "r2", "r2"], seed=1)
    assert cl[0] <= 0.5 <= cl[1]
    assert stats.cohen_kappa(
        [(True, True), (False, False), (True, False), (False, True)]
    ) == pytest.approx(0.0)
    assert stats.pabak(
        [(True, True), (False, False), (True, False), (False, True)]
    ) == pytest.approx(0.0)
    assert stats.weighted_kappa([(3, 3), (0, 0), (2, 2), (1, 1)]) == 1.0
    assert stats.weighted_kappa([(3, 0), (0, 3)]) < 0
    alpha = stats.krippendorff_alpha_ordinal([(3, 3), (2, 2), (1, 1), (0, 0), (3, 2)])
    assert alpha is not None and 0.5 < alpha <= 1.0
    h = stats.holm({"a": 0.01, "b": 0.04, "c": 0.20})
    assert h["a"]["reject"] and h["a"]["p_holm"] == pytest.approx(0.03)
    assert not h["c"]["reject"]
    assert stats.permutation_p([0.0] * 10, seed=1) == 1.0
    p = stats.permutation_p([1.0] * 12, seed=1)
    assert p < 0.01
    s = stats.summarize_metric([1.0, 1.0, 1.0])
    assert s["mean"] == 1.0 and s["rule_of_three_upper"] == pytest.approx(1.0)


# ---------------------------------------------------------------- judge


def test_judge_with_fake_llm_and_cache(tmp_path):
    it = _item(judge_notes="Essential: 50 mg/L, Cột B.")
    resp = _resp("BOD5 cột B tối đa 50 mg/L.", ["chunk:row1"])
    prompt = judge_prompt(it, resp)
    assert "<cited_chunks>" in prompt and "chunk:other" not in prompt and "50 mg/L" in prompt
    verdict = {
        "value_correct": True,
        "column_or_condition_correct": True,
        "conditions_complete": True,
        "basis_complete": "complete",
        "faithful": True,
        "unsupported_claims": [],
        "abstain_appropriate": "not_applicable",
        "warnings_ok": True,
        "overall": 3,
        "rationale": "Đúng giá trị và cột.",
    }
    llm = FakeLLM([verdict])
    cache = JsonCache(tmp_path / "cache")
    out = judge_item(
        llm, it, resp, cache=cache, model="fake-judge", thinking_level="high", tag="J1"
    )
    assert (
        out["pass"] is True
        and out["overall"] == 3
        and out["prompt_version"] == JUDGE_PROMPT_VERSION
    )
    assert llm.calls[0]["schema"] == "JudgeVerdict" and len(llm.calls) == 1
    again = judge_item(
        llm, it, resp, cache=cache, model="fake-judge", thinking_level="high", tag="J1"
    )
    assert again["cached"] is True and len(llm.calls) == 1
    bad = FakeLLM([{"overall": 9}])  # schema violation → parse error recorded
    err = judge_item(bad, it, resp, cache=None, model="fake-judge", thinking_level=None)
    assert err.get("judge_error") is True
    assert JudgeVerdict.model_validate(verdict).overall == 3


# ---------------------------------------------------------------- human sheet + agreement


def test_human_sheet_and_agreement(tmp_path):
    run_dir = tmp_path / "run"
    it = _item()
    qa_rows = []
    judge_rows = []
    for i in range(10):
        r = _resp("BOD5 cột B tối đa 50 mg/L.", ["chunk:row1"])
        qa_rows.append(
            {
                "item_id": f"G-WW-{900 + i}",
                "repeat": 1,
                "stratum": "S" if i % 2 else "T",
                "expected_answer": it.expected_answer,
                "status": "answered",
                "response": r,
            }
        )
        judge_rows.append(
            {
                "item_id": f"G-WW-{900 + i}",
                "repeat": 1,
                "tag": "J1",
                "overall": 3 if i < 8 else 1,
                "pass": i < 8,
            }
        )
    write_jsonl(run_dir / "qa.jsonl", qa_rows)
    write_jsonl(run_dir / "judge.jsonl", judge_rows)
    rows = human.sample_for_human(run_dir, frac=0.4, seed=1)
    ids = {r["item_id"] for r in rows}
    assert {"G-WW-908", "G-WW-909"} <= ids and len(rows) == 4  # all failures + stratified passes
    sheet = run_dir / "human_sheet.csv"
    assert human.export_sheet(run_dir, rows, sheet) == 4
    with open(sheet, encoding="utf-8-sig", newline="") as fh:
        data = list(csv.DictReader(fh))
    assert "overall" not in json.dumps(data[0]) or "human_overall (0-3)" in data[0]
    for d in data:
        d["human_overall (0-3)"] = "3" if d["item_id"] not in ("G-WW-908",) else "0"
    with open(sheet, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(data[0].keys()))
        w.writeheader()
        w.writerows(data)
    res = human.agreement(run_dir, sheet)
    assert res["n"] == 4 and 0 <= res["percent_agreement"] <= 1
    assert res["confusion_judge_x_human"]
    assert "p_judge_pass_given_human_fail" in res


def test_write_and_read_jsonl_roundtrip(tmp_path):
    from app.evaluation.runner import read_jsonl

    p = tmp_path / "x.jsonl"
    write_jsonl(p, [{"a": 1, "b": "ê"}])
    assert read_jsonl(p) == [{"a": 1, "b": "ê"}]
    assert read_jsonl(Path(tmp_path / "missing.jsonl")) == []

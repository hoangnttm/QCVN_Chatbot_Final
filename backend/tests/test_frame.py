"""Rule path of query understanding v2 (plan 2026-10-12, G3 steps 3.2–3.3): no database, no LLM."""

from pathlib import Path

import pytest
import yaml

from app.ontology.corpus import Corpus, DocumentSpec
from app.ontology.loader import load_dictionaries
from app.retrieval.frame import FrameBuilder, parse_number
from app.retrieval.tokenize import AliasIndex

REGISTRY = Path(__file__).resolve().parents[2] / "data" / "registry" / "documents.yaml"


@pytest.fixture(scope="module")
def builder() -> FrameBuilder:
    d = load_dictionaries()
    corpus = Corpus.from_dictionaries(d)
    docs = yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))["documents"]
    for doc in docs:
        corpus.documents[doc["document_id"]] = DocumentSpec(
            doc["document_id"], doc["document_code"], doc["document_type"], doc["domain"]
        )
    codes = {doc["document_id"]: doc["document_code"] for doc in docs}
    return FrameBuilder(d, AliasIndex(d, codes), corpus, corpus.document_domains())


def slots(frame, dimension):
    return [("!" if s.negated else "") + s.value for s in frame.slots if s.dimension == dimension]


def test_threshold_with_flow_and_zone(builder):
    f = builder.build("Nhà máy xả 2.500 m3/ngày, cột A thì COD tối đa bao nhiêu mg/L?")
    assert f.domain == builder.corpus.domain_codes()[0]
    assert f.intents[0] == "threshold" and not f.unsure
    assert slots(f, "parameter") == ["COD"] and slots(f, "zone") == ["A"]
    (flow,) = [s for s in f.slots if s.dimension == "flow"]
    assert float(flow.value) == 2500 and flow.unit == "m3_per_day"
    assert flow.text.startswith("2.500")  # every slot keeps the words it was read from


def test_value_question_keeps_threshold_as_main_intent(builder):
    # the transition wording adds an intent, it does not replace the question about a value
    f = builder.build(
        "Cơ sở đã đi vào vận hành trước đó. Kể từ ngày 01/01/2032, Niken (Ni) ở Cột B phải đáp ứng"
        " mức nào?"
    )
    assert f.intents[0] == "threshold" and "applicability" in f.intents
    assert slots(f, "date") == ["2032-01-01"]
    # " là gì" alone would read as a definition
    assert builder.build("Ngưỡng Fe cột B là gì?").intents[0] == "threshold"
    # no cue word at all: a parameter and a column still ask for the value
    assert builder.build("Tổng Phốt pho khi xả ra sông, cột A?").intents == ["threshold"]
    # without a parameter the same cue words do not make a threshold question
    f = builder.build("Khi lấy mẫu khí thải, nhà máy phải chạy tối thiểu bao nhiêu phần trăm?")
    assert f.intents[0] == "sampling"


def test_several_intents(builder):
    f = builder.build(
        "Lò hỏa táng cột A, bụi PM đo được 18 mg/Nm3. Bạn xác nhận nhà máy đạt quy chuẩn không?"
    )
    assert f.intents[0] == "threshold" and "compliance_check" in f.intents
    assert slots(f, "measured_value") == ["18"]
    assert (
        builder.build("Tần suất quan trắc COD là bao nhiêu lần một năm?").intents[0] == "frequency"
    )


def test_equipment_is_the_last_label_and_masks_its_words(builder):
    f = builder.build(
        "Theo quy chuẩn, thiết bị sản xuất sản phẩm dầu mỏ tinh chế (lọc, hóa dầu), đốt CO thuộc"
        " cột B thì NOx không được vượt quá bao nhiêu?"
    )
    assert slots(f, "equipment") == ["thiet_bi_dot_co"]
    assert slots(f, "parameter") == ["NOx"]  # the CO of the label is not a parameter asked
    assert f.domain == builder.corpus.domain_codes()[1]  # the equipment decides the domain
    # a label inside a longer label is the longer one; a short top-level label stands alone
    f = builder.build("Lò cốc trong sản xuất than cốc, cột B: H2S tối đa bao nhiêu?")
    assert slots(f, "equipment") == ["lo_coc_trong_san_xuat_than_coc"]
    assert slots(builder.build("Bụi của lò hỏa táng cột B?"), "equipment") == ["lo_hoa_tang"]
    # a generic child label on its own names no equipment
    assert slots(builder.build("Thiết bị đốt cột B thì SO2 bao nhiêu?"), "equipment") == []


def test_negation(builder):
    f = builder.build(
        "Cơ sở không thuộc vùng bảo vệ nghiêm ngặt hay vùng hạn chế phát thải phải đạt SO2 ở mức"
        " nào?"
    )
    assert slots(f, "zone") == ["C"] and slots(f, "parameter") == ["SO2"]
    # the negation excludes a sector here, the column is asked
    f = builder.build("T-P đối với cơ sở không thuộc nhóm thủy sản xả ra sông, Cột B tối đa?")
    assert slots(f, "zone") == ["B"]
    f = builder.build("Nước làm mát không dùng Clo thì chỉ cần quan trắc thông số nào?")
    assert slots(f, "parameter") == ["!Cl2"] and f.intents[0] == "management"


def test_domain_evidence_and_unsure(builder):
    wastewater, emission = builder.corpus.domain_codes()[:2]
    # a routing keyword of the other domain ("nitơ") does not outvote a document or a zone name
    f = builder.build("Nitơ oxit ở vùng hạn chế phát thải tối đa bao nhiêu?")
    assert f.domain == emission
    f = builder.build("Giới hạn ClO4^- theo QCVN 40:2025, cột B?")
    assert f.domain == wastewater and slots(f, "parameter") == ["Perchlorate"]
    f = builder.build("Số giấy phép môi trường mới nhất của nhà máy là số nào?")
    assert f.domain == "unknown" and "domain:unknown" in f.unsure and "intent:none" in f.unsure
    assert builder.build("Giới hạn tiếng ồn khu dân cư ban đêm?").domain == "out_of_scope"


def test_parse_number():
    assert parse_number("3.000") == 3000 and parse_number("2,5") == 2.5
    assert parse_number("12.380.000") == 12380000

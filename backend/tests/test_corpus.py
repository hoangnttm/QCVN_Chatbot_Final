"""`Corpus`: what the code knows about domains, documents and tables comes from data (plan G1)."""

from app.ontology.corpus import SHARED, Corpus, DocumentSpec, DomainSpec
from app.ontology.loader import load_dictionaries
from app.qa.models import LLMUnderstanding, understanding_schema


def _corpus() -> Corpus:
    return Corpus(
        domains={
            "water": DomainSpec("water", "Nước", keywords=("nước thải",), unit_hints=("mg_L",)),
            "air": DomainSpec("air", "Khí", keywords=("khí thải",)),
            "noise": DomainSpec("noise", "Tiếng ồn", keywords=("tiếng ồn",)),
        },
        zone_codes={"water": ("A", "B", "C"), "noise": ("A", "B", "C", "D")},
        documents={
            "Q1": DocumentSpec("Q1", "QCVN 1", "qcvn", "water"),
            "T1": DocumentSpec("T1", "TT 1", "circular", "water"),
            "Q2": DocumentSpec("Q2", "QCVN 2", "qcvn", "air"),
            "T9": DocumentSpec("T9", "TT 9", "circular", SHARED),
        },
        limit_tables={"water": ("Q1.B1", "Q1.B2"), "air": ("Q2.B1",)},
    )


def test_domains_are_an_open_list():
    c = _corpus()
    assert c.domain_codes() == ["water", "air", "noise"]
    assert c.search_domains("air") == {"air", SHARED} and c.search_domains("unknown") is None
    assert c.other_domains("air") == {"water", "noise"}
    assert c.candidates("noise") == ["noise"] and c.candidates("cross_domain") == c.domain_codes()
    assert c.members(SHARED) == c.domain_codes() and c.members("other") == []
    assert c.zones("noise") == ("A", "B", "C", "D") and c.zones() == ("A", "B", "C", "D")


def test_documents_and_tables_by_domain():
    c = _corpus()
    assert c.documents_of("water") == [
        "Q1",
        "T1",
    ]  # the shared circular belongs to no single domain
    assert c.regulations("water") == ["Q1"] and c.regulations() == ["Q1", "Q2"]
    assert c.document_domains() == {"Q1": "water", "T1": "water", "Q2": "air"}
    assert c.threshold_tables("air") == ["Q2.B1"]
    assert c.threshold_tables("unknown") == ["Q1.B1", "Q1.B2", "Q2.B1"]  # unclear: every domain
    assert c.threshold_tables("noise") == []


def test_dictionaries_carry_the_routing_vocabulary():
    c = Corpus.from_dictionaries(load_dictionaries())
    assert c.domain_codes() and all(spec.keywords for spec in c.domains.values())
    assert c.out_of_scope_keywords and c.equipment_cue_words
    assert all(c.zones(d) for d in c.domains)


def test_llm_classification_schema_follows_the_domains():
    schema = understanding_schema(["water", "air"])
    assert issubclass(schema, LLMUnderstanding) and schema.__name__ == "LLMUnderstanding"
    enum = schema.model_json_schema()["properties"]["domain"]["enum"]
    assert enum == ["water", "air", "cross_domain", "out_of_scope"]
    assert schema.model_validate({"domain": "air", "intent": "threshold"}).domain == "air"
    try:
        schema.model_validate({"domain": "noise", "intent": "threshold"})
    except ValueError:
        pass
    else:
        raise AssertionError("a domain outside the corpus must be rejected")

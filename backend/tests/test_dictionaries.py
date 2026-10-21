"""Phase 0 acceptance: all dictionaries load without errors and are internally consistent."""

from app.ontology.loader import integrity_problems, load_dictionaries


def test_dictionaries_load(settings):
    d = load_dictionaries(settings=settings)
    assert len(d.domains) == 2
    assert {z.code for z in d.zones if z.domain == "wastewater"} == {"A", "B", "C"}
    assert {z.code for z in d.zones if z.domain == "emission"} == {"A", "B", "C"}
    assert len(d.parameters) >= 100, "expect ~130 parameters across both QCVN"
    assert len(d.industry_sectors) == 54, "QCVN 40:2025 Phụ lục 2 has 54 loại hình"
    assert len(d.equipment_types) >= 60


def test_dictionaries_integrity(settings):
    d = load_dictionaries(settings=settings)
    assert integrity_problems(d) == []


def test_core_parameters_present(settings):
    d = load_dictionaries(settings=settings)
    ids = {p.parameter_id for p in d.parameters}
    for must in [
        "BOD5",
        "COD",
        "TOC",
        "TSS",
        "pH",
        "T_N",
        "T_P",
        "NH4_N",
        "PM",
        "CO",
        "SO2",
        "NOx",
        "HCl",
        "NH3",
    ]:
        assert must in ids, must

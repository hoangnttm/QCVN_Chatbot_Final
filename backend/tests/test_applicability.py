"""Phase 3 gate: the applicability engine is deterministic, every fired rule carries evidence,
missing information is reported instead of guessed, and draft/pending data is always flagged.
Scenarios are drafts for the expert's 20-scenario review (PLAN.md §13 Phase 3)."""

import pytest

from app.applicability.engine import Assessor, form_schema, get_ruleset
from app.applicability.models import AssessmentRequest
from app.applicability.rules import resolve_evidence
from app.db.loader import load_document
from app.db.session import get_sessionmaker
from app.registry.loader import load_registry

DOCS = ("TT_06_2025", "QCVN_40_2025", "TT_45_2024", "QCVN_19_2024", "TT_10_2021")
BOILER_20T = "thiet_bi_co_cong_suat_hoi_tu_20_tan_gio_tro_len_hoac_co_nhiet_luong_tu_12_380_00"


@pytest.fixture(scope="module")
def assessor(db_settings):
    reg = load_registry(settings=db_settings)
    maker = get_sessionmaker(db_settings)
    with maker.begin() as session:
        for doc_id in DOCS:
            load_document(doc_id, reg, db_settings, session)
    session = maker()
    yield Assessor(session, db_settings)
    session.close()


def ww(assessor, **ctx):
    base = {
        "wastewater_kind": "industrial",
        "discharges_to_receiving_water": True,
        "connected_to_centralized_system": False,
        "mixed_domestic": False,
        "sector_id": "S02",
        "receiving_body_type": "river",
        "zone": "B",
        "flow_m3_day": 3000,
        "is_cooling_water": False,
        "is_aquaculture": False,
        "uses_chlorine": False,
        "project_status": "new",
        "as_of_date": "2026-01-15",
    }
    base.update(ctx)
    return assessor.assess(AssessmentRequest(domain="wastewater", context=base))


def em(assessor, **ctx):
    base = {
        "has_stack_emission": True,
        "is_vehicle": False,
        "zone": "B",
        "equipment_type_id": BOILER_20T,
        "fuel_type": "solid",
        "project_status": "new",
        "as_of_date": "2026-01-15",
    }
    base.update(ctx)
    return assessor.assess(AssessmentRequest(domain="emission", context=base))


def test_rules_load_and_every_evidence_resolves(assessor):
    rs = get_ruleset(assessor.settings)
    assert rs.version == 1 and len(rs.rules) >= 24
    assert resolve_evidence(rs, assessor.s) == []
    assert all(r.evidence_resolved for r in rs.rules), "every rule cites at least one chunk"
    schema = form_schema("wastewater", assessor.settings)
    sectors = next(f for f in schema["fields"] if f["name"] == "sector_id")
    assert len(sectors["options"]) == 54 and sectors["options"][0]["value"] == "S01"
    equip = next(
        f
        for f in form_schema("emission", assessor.settings)["fields"]
        if f["name"] == "equipment_type_id"
    )
    assert any(o["value"] == BOILER_20T for o in equip["options"])


def test_wastewater_baseline_zone_b_flow_over_2000(assessor):
    r = ww(assessor)
    assert [c.status for c in r.candidates] == ["candidate"] and r.effective_zone == "B"
    by_param = {(x.parameter_id, x.zone): x for x in r.limit_rules}
    assert by_param[("BOD5", "B")].value == 50 and "F>2000" in by_param[("BOD5", "B")].conditions[0]
    assert ("COD", "B") in by_param and ("TOC", "B") in by_param, "COD and TOC are separate rows"
    assert by_param[("pH", "B")].op == "between" and by_param[("Coliform", "B")].value == 5000
    assert by_param[("Color", "B")].selected_by == ["W-09"]  # sector S02 → Độ màu
    assert "T_P" not in {p for p, _ in by_param}, "T-P only when the sector lists it"
    assert r.transition is not None and r.transition.applies == "new_standard"
    assert not r.missing_information
    assert any("chưa được chuyên gia duyệt" in w for w in r.warnings)
    fired = {a.rule_id for a in r.applied_rules if a.outcome == "applied"}
    assert {"W-01", "W-07", "W-09", "W-12"} <= fired and "W-05" not in fired
    assert all(a.evidence for a in r.applied_rules if a.outcome == "applied")
    assert any(
        p["kind"] == "structure_path" and p["table_code"] == "QCVN40_2025.B1"
        for p in r.evidence_path
    )


def test_wastewater_unknown_zone_defaults_to_b_with_warning_and_missing(assessor):
    r = ww(assessor, zone="unknown")
    assert r.effective_zone == "B" and r.zone_defaulted
    assert any("Điều 2.4" in w for w in r.warnings)
    assert [mi.field for mi in r.missing_information] == ["zone"]
    w05 = next(a for a in r.applied_rules if a.rule_id == "W-05")
    assert w05.outcome == "applied" and w05.evidence[0].citation_label.endswith("Điều 2, Khoản 4")


def test_wastewater_flow_regime_and_missing_flow(assessor):
    r = ww(assessor, flow_m3_day=1500)
    bod = next(x for x in r.limit_rules if x.parameter_id == "BOD5")
    assert bod.value == 60 and "F<=2000" in bod.conditions[0]
    r2 = ww(assessor, flow_m3_day=None)
    assert not [x for x in r2.limit_rules if x.table_code == "QCVN40_2025.B1"]
    assert any(mi.field == "flow_m3_day" for mi in r2.missing_information)


def test_wastewater_tp_row_by_receiving_body_and_sector(assessor):
    # S01 nuôi trồng thủy sản: characteristic params from Mục 4.3.2 → use is_aquaculture path instead
    r_lake = ww(assessor, sector_id="S05", receiving_body_type="lake", is_aquaculture=False)
    tp = [x for x in r_lake.limit_rules if x.parameter_id == "T_P"]
    if tp:  # only when the sector lists T-P
        assert {x.row_id for x in tp} == {"QCVN40_2025.B2.r4.1"}
    r = ww(
        assessor,
        sector_id="S01",
        is_aquaculture=True,
        receiving_body_type="river",
        flow_m3_day=9000,
    )
    tp = [x for x in r.limit_rules if x.parameter_id == "T_P"]
    assert {x.row_id for x in tp} == {"QCVN40_2025.B2.r4.2"} and tp[0].value == 14
    bod = next(x for x in r.limit_rules if x.parameter_id == "BOD5")
    assert "F<=2000" in bod.conditions[0], "aquaculture uses the F ≤ 2000 tier regardless of flow"
    assert set(r.parameter_set) >= {"pH", "Coliform", "T_N", "T_P"}


def test_wastewater_saline_excludes_chloride_and_chlorine_adds_parameters(assessor):
    r = ww(assessor, receiving_body_type="saline_brackish", uses_chlorine=True, sector_id="S10")
    ids = {x.parameter_id for x in r.limit_rules}
    assert "Cl" not in ids and {"Residual_chlorine", "Chloroform"} <= ids
    w11 = next(x for x in r.limit_rules if x.parameter_id == "Chloroform")
    assert w11.selected_by == ["W-11"]


def test_wastewater_cooling_water_only_temperature(assessor):
    r = ww(assessor, is_cooling_water=True, uses_chlorine=False)
    assert r.parameter_set == ["Temperature"]
    assert {x.parameter_id for x in r.limit_rules} == {"Temperature"}
    assert not [x for x in r.limit_rules if x.table_code == "QCVN40_2025.B1"]


def test_wastewater_exclusions_and_indirect(assessor):
    r = ww(assessor, wastewater_kind="livestock")
    assert r.candidates[0].status == "excluded" and r.candidates[0].pointers
    assert r.limit_rules == []
    r2 = ww(assessor, connected_to_centralized_system=True)
    assert r2.candidates[0].status == "indirect" and any("chủ hạ tầng" in n for n in r2.notes)


def test_wastewater_transition_old_standard_until_2032(assessor):
    r = ww(assessor, project_status="operating", dossier_date="2020-05-01", as_of_date="2026-01-15")
    assert r.transition.applies == "old_standard_until_deadline"
    assert "QCVN 40:2011/BTNMT" in r.transition.old_standards
    assert any("chưa bắt buộc" in w for w in r.warnings)
    r2 = ww(
        assessor, project_status="operating", dossier_date="2020-05-01", as_of_date="2032-03-01"
    )
    assert r2.transition.applies == "new_standard"
    r3 = ww(assessor, project_status="new", as_of_date="2025-05-01")
    assert r3.transition.applies == "not_yet_effective"
    r4 = ww(assessor, project_status=None)
    assert (
        any(mi.field == "project_status" for mi in r4.missing_information) and r4.transition is None
    )


def test_emission_boiler_solid_fuel_zone_b(assessor):
    r = em(assessor)
    assert [c.status for c in r.candidates] == ["candidate"]
    by = {x.parameter_id: x for x in r.limit_rules}
    assert (
        by["CO"].value == 350
        and by["CO"].ref_o2 == 6
        and by["SO2"].value == 300
        and by["PM"].value == 40
    )
    assert all(x.zone == "B" for x in r.limit_rules)
    assert (
        r.transition.applies == "new_standard"
        and r.transition.effective_source == "do quản trị viên khai báo"
    )
    assert any("do quản trị viên khai báo" in w and "không in ngày" in w for w in r.warnings)
    assert not any("dự thảo" in w for w in r.warnings), "all five texts official (2026-09-24)"
    assert not any("unverified" in w for w in r.warnings)
    fired = {a.rule_id for a in r.applied_rules if a.outcome == "applied"}
    assert {"E-01", "E-04", "E-05", "E-07", "E-09", "M-03", "M-04"} <= fired
    e07 = next(a for a in r.applied_rules if a.rule_id == "E-07")
    assert e07.evidence[0].citation_label.startswith("Sổ đăng ký văn bản")


def test_emission_fuel_and_capacity_mismatch_is_flagged_not_silent(assessor):
    r_gas = em(assessor, fuel_type="gas")
    assert any("không khớp dòng thiết bị" in w for w in r_gas.warnings)
    assert r_gas.limit_rules, "rows are kept for reference, never dropped silently"
    r_small = em(assessor, capacity_value=5, capacity_unit="t_steam_per_h")
    assert any("không thỏa điều kiện công suất" in w for w in r_small.warnings)
    r_ok = em(assessor, capacity_value=25, capacity_unit="t_steam_per_h")
    assert not any("công suất" in w.lower() for w in r_ok.warnings)


def test_emission_unknown_equipment_falls_back_and_unknown_zone(assessor):
    r = em(assessor, equipment_type_id="thiet_bi_khong_ton_tai", zone="unknown")
    assert r.effective_zone == "B" and r.zone_defaulted
    assert any("thiết bị xả thải khác" in w for w in r.warnings)
    assert r.limit_rules and all(x.zone == "B" for x in r.limit_rules)
    assert any(mi.field == "zone" for mi in r.missing_information)


def test_emission_vehicle_excluded_and_local_roadmap_missing(assessor):
    r = em(assessor, is_vehicle=True)
    assert r.candidates[0].status == "excluded" and r.limit_rules == []
    r2 = em(assessor, project_status="operating", dossier_date="2019-01-01")
    assert r2.transition.applies == "old_standard_until_deadline"
    assert any(mi.field == "local_roadmap" for mi in r2.missing_information)


def test_deterministic(assessor):
    a = ww(assessor, uses_chlorine=True).model_dump(exclude={"assessment_id"})
    b = ww(assessor, uses_chlorine=True).model_dump(exclude={"assessment_id"})
    assert a == b

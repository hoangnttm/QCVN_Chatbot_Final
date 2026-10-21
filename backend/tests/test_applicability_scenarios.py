"""Every scenario in data/rules/applicability_scenarios_v1.yaml runs and meets its `expect` block —
the same scenarios are printed for the expert in the review pack (sheet 12)."""

import yaml

from app.applicability.models import AssessmentRequest
from tests.test_applicability import assessor  # noqa: F401  (module fixture)


def check(result, expect: dict) -> list[str]:
    problems: list[str] = []
    params = set(result.parameter_set) | {x.parameter_id for x in result.limit_rules}
    if "candidate" in expect and not any(
        c.document_id == expect["candidate"] and c.status == "candidate" for c in result.candidates
    ):
        problems.append(f"candidate {expect['candidate']} not found")
    if "candidate_status" in expect and not (
        result.candidates and result.candidates[0].status == expect["candidate_status"]
    ):
        problems.append(f"candidate status != {expect['candidate_status']}")
    if "zone" in expect and result.effective_zone != expect["zone"]:
        problems.append(f"zone {result.effective_zone} != {expect['zone']}")
    if expect.get("zone_defaulted") and not result.zone_defaulted:
        problems.append("zone not defaulted")
    if "transition" in expect and (
        result.transition is None or result.transition.applies != expect["transition"]
    ):
        problems.append(f"transition != {expect['transition']}")
    for p in expect.get("has_params", []):
        if p not in params:
            problems.append(f"missing parameter {p}")
    for p in expect.get("not_params", []):
        if p in params:
            problems.append(f"unexpected parameter {p}")
    if "params_exact" in expect and result.parameter_set != expect["params_exact"]:
        problems.append(f"parameter_set {result.parameter_set} != {expect['params_exact']}")
    if "limits" in expect and len(result.limit_rules) != expect["limits"]:
        problems.append(f"{len(result.limit_rules)} limits != {expect['limits']}")
    if "table1_limits" in expect:
        n = len([x for x in result.limit_rules if x.table_code == "QCVN40_2025.B1"])
        if n != expect["table1_limits"]:
            problems.append(f"table1 limits {n}")
    for f in expect.get("missing", []):
        if not any(mi.field == f for mi in result.missing_information):
            problems.append(f"missing_information lacks {f}")
    if "tp_row" in expect:
        rows = {x.row_id for x in result.limit_rules if x.parameter_id == "T_P"}
        if rows != {expect["tp_row"]}:
            problems.append(f"T-P rows {rows}")
    if "bod_regime" in expect:
        bod = next((x for x in result.limit_rules if x.parameter_id == "BOD5"), None)
        if bod is None or expect["bod_regime"] not in bod.conditions[0]:
            problems.append("BOD regime")
    if "limit_CO" in expect:
        co = next((x for x in result.limit_rules if x.parameter_id == "CO"), None)
        if co is None or co.value != expect["limit_CO"] or co.ref_o2 != expect.get("ref_o2_CO"):
            problems.append("CO limit")
    if "warning_contains" in expect and not any(
        expect["warning_contains"] in w for w in result.warnings
    ):
        problems.append(f"no warning containing {expect['warning_contains']!r}")
    return problems


def test_all_scenarios(assessor):  # noqa: F811
    path = assessor.settings.data_dir / "rules" / "applicability_scenarios_v1.yaml"
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    failures = {}
    for sc in doc["scenarios"]:
        result = assessor.assess(AssessmentRequest(domain=sc["domain"], context=sc["context"]))
        problems = check(result, sc["expect"])
        if problems:
            failures[sc["id"]] = problems
    assert not failures, failures
    assert len(doc["scenarios"]) >= 17

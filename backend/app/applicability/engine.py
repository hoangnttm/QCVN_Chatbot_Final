"""Deterministic applicability engine (PLAN.md §7): rules from YAML, data from PostgreSQL, no LLM.

The engine walks the rules of a domain in file order. A rule whose `requires` fields are missing
is skipped and reported as MissingInformation; a rule whose `when` holds runs its handler, which
mutates the assessment state (candidates, effective zone, flow regime, parameter set, row filters,
transition) and records the rule with its evidence. Finally the matching LimitRules are pulled from
`limit_rules` and returned with citation labels, review status and the structural evidence path.
"""

from __future__ import annotations

import datetime as dt
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.applicability.models import (
    AppliedRule,
    AssessmentRequest,
    AssessmentResult,
    Candidate,
    MissingInformation,
    SelectedLimit,
    TransitionOutcome,
)
from app.applicability.rules import Rule, RuleSet, evaluate, load_rules, resolve_evidence
from app.core.config import Settings, get_settings
from app.db import models as m
from app.graph.store import PostgresGraphStore
from app.ontology.loader import load_dictionaries
from app.ontology.schema import Dictionaries

DRAFT_WARNING = "Căn cứ từ bản dự thảo {code}, chưa đối chiếu bản ký chính thức"
PENDING_WARNING = "{n} giới hạn được chọn chưa được chuyên gia duyệt (review_status = pending) — chỉ dùng tham khảo"


@lru_cache
def _cached_rules(path: str) -> RuleSet:
    return load_rules(Path(path))


def get_ruleset(settings: Settings | None = None) -> RuleSet:
    settings = settings or get_settings()
    return _cached_rules(str(settings.data_dir / "rules" / "applicability_v1.yaml"))


@lru_cache
def _cached_dicts(data_dir: str) -> Dictionaries:
    return load_dictionaries(settings=Settings(data_dir=Path(data_dir), _env_file=None))


def _parse_date(v: Any) -> dt.date | None:
    if v in (None, ""):
        return None
    if isinstance(v, dt.date):
        return v
    return dt.date.fromisoformat(str(v)[:10])


class Assessor:
    def __init__(self, session: Session, settings: Settings | None = None) -> None:
        self.s = session
        self.settings = settings or get_settings()
        self.rs = get_ruleset(self.settings)
        if not any(r.evidence_resolved for r in self.rs.rules):
            problems = resolve_evidence(self.rs, session)
            if problems:
                raise RuntimeError("applicability evidence unresolved: " + "; ".join(problems))
        self.dicts = _cached_dicts(str(self.settings.data_dir))
        self.docs = {d.document_id: d for d in session.execute(select(m.Document)).scalars()}
        self.graph = PostgresGraphStore(session)

    # ------------------------------------------------------------------ public

    def assess(self, req: AssessmentRequest) -> AssessmentResult:
        ctx = dict(req.context)
        as_of = _parse_date(ctx.get("as_of_date")) or dt.date.today()
        ctx["as_of_date"] = as_of
        for k in ("dossier_date",):
            ctx[k] = _parse_date(ctx.get(k))
        state: dict[str, Any] = {
            "domain": req.domain,
            "ctx": ctx,
            "candidates": {},
            "zone": ctx.get("zone") if ctx.get("zone") in ("A", "B", "C") else None,
            "zone_defaulted": False,
            "flow_regime": None,
            "params": [],  # ordered parameter ids (Bảng 2 / Bảng 1 selection)
            "param_notes": [],
            "table1": True,
            "table2": True,
            "row_filters": {},  # parameter_id -> allowed row ids
            "excluded_params": set(),
            "mode": None,  # cooling | aquaculture
            "equipment_rules": None,
            "transition": None,
            "missing": [],
            "warnings": [],
            "notes": [],
            "applied": [],
            "extra_evidence": [],
        }
        for rule in self.rs.for_domain(req.domain):
            self._run(rule, state)
        limits = self._select_limits(state)
        result = self._finish(req, state, limits)
        return result

    # ------------------------------------------------------------------ rule loop

    def _run(self, rule: Rule, st: dict[str, Any]) -> None:
        ctx = st["ctx"]
        missing = [f for f in rule.requires if ctx.get(f) in (None, "", "unknown")]
        # `dossier_date` is only required when the project is not new
        if "dossier_date" in missing and ctx.get("project_status") == "new":
            missing.remove("dossier_date")
        if missing:
            for f in missing:
                spec = self.rs.field(rule.domain, f)
                self._add_missing(st, f, spec.label if spec else f, rule)
            st["applied"].append(
                AppliedRule(
                    rule_id=rule.id,
                    title=rule.title,
                    outcome="skipped_missing",
                    detail="thiếu: " + ", ".join(missing),
                )
            )
            return
        excluded = any(c.status == "excluded" for c in st["candidates"].values())
        if (rule.when and not evaluate(rule.when, ctx)) or (
            excluded and rule.action not in ("exclude", "note", "qa_policy")
        ):
            st["applied"].append(
                AppliedRule(rule_id=rule.id, title=rule.title, outcome="not_triggered")
            )
            return
        handler = getattr(self, f"_act_{rule.action}", None)
        if handler is None:
            raise RuntimeError(f"{rule.id}: no handler for action {rule.action}")
        detail = handler(rule, st)
        if detail is False:  # handler decided the rule does not apply after all
            st["applied"].append(
                AppliedRule(rule_id=rule.id, title=rule.title, outcome="not_triggered")
            )
            return
        st["applied"].append(
            AppliedRule(
                rule_id=rule.id,
                title=rule.title,
                outcome="applied",
                message=rule.message,
                detail=detail if isinstance(detail, str) else None,
                evidence=rule.evidence_resolved,
            )
        )

    def _add_missing(self, st: dict[str, Any], field: str, label: str, rule: Rule) -> None:
        for mi in st["missing"]:
            if mi.field == field:
                if rule.id not in mi.rule_ids:
                    mi.rule_ids.append(rule.id)
                return
        st["missing"].append(
            MissingInformation(
                field=field, label=label, reason=f"cần cho {rule.title}", rule_ids=[rule.id]
            )
        )

    # ------------------------------------------------------------------ handlers

    def _act_candidate(self, rule: Rule, st: dict[str, Any]) -> str | None:
        doc_id = rule.params["document_id"]
        c = st["candidates"].get(doc_id)
        if c and c.status == "excluded":
            return "đã bị loại bởi rule khác"
        st["candidates"][doc_id] = Candidate(
            document_id=doc_id,
            document_code=self.docs[doc_id].document_code,
            status="candidate",
            reason=rule.message,
            rule_ids=[rule.id],
        )
        return None

    def _act_exclude(self, rule: Rule, st: dict[str, Any]) -> str | None:
        doc_id = rule.params["document_id"]
        kind = st["ctx"].get("wastewater_kind") or (
            "vehicle" if st["ctx"].get("is_vehicle") else ""
        )
        pointer = (rule.params.get("pointers") or {}).get(kind)
        st["candidates"][doc_id] = Candidate(
            document_id=doc_id,
            document_code=self.docs[doc_id].document_code,
            status="excluded",
            reason=rule.message,
            rule_ids=[rule.id],
            pointers=[pointer] if pointer else [],
        )
        st["table1"] = st["table2"] = False
        st["equipment_rules"] = []
        return pointer

    def _act_indirect(self, rule: Rule, st: dict[str, Any]) -> str | None:
        doc_id = rule.params["document_id"]
        c = st["candidates"].get(doc_id)
        if c and c.status == "excluded":
            return None
        st["candidates"][doc_id] = Candidate(
            document_id=doc_id,
            document_code=self.docs[doc_id].document_code,
            status="indirect",
            reason=rule.message,
            rule_ids=[rule.id] + (c.rule_ids if c else []),
        )
        st["notes"].append(
            "Ngưỡng dưới đây chỉ để tham khảo: tại điểm đấu nối áp dụng yêu cầu của chủ hạ tầng."
        )
        return None

    def _act_note(self, rule: Rule, st: dict[str, Any]) -> str | None:
        st["notes"].append(rule.message)
        return None

    def _act_qa_policy(self, rule: Rule, st: dict[str, Any]) -> bool:
        return False

    def _act_zone_source(self, rule: Rule, st: dict[str, Any]) -> str | None:
        if st["zone"] is None:
            self._add_missing(
                st,
                "zone",
                "Phân vùng (Cột A/B/C)",
                rule,
            )
            st["missing"][-1].reason = f"xác định theo {rule.params.get('external')}"
        return None

    def _act_default_zone(self, rule: Rule, st: dict[str, Any]) -> str | None:
        if st["zone"] is not None:
            return False
        st["zone"] = rule.params["zone"]
        st["zone_defaulted"] = True
        st["warnings"].append(rule.message)
        return f"zone = {st['zone']} (mặc định)"

    def _act_flow_regime(self, rule: Rule, st: dict[str, Any]) -> str | None:
        flow = float(st["ctx"]["flow_m3_day"])
        thr = float(rule.params.get("threshold", 2000))
        if st["mode"] == "aquaculture":  # Mục 4.3.2: F ≤ 2000 tier regardless of flow
            st["flow_regime"] = "F<=2000"
            return f"F = {flow:g} m³/ngày nhưng nuôi trồng thủy sản → mức F ≤ 2000 (Mục 4.3.2)"
        st["flow_regime"] = "F<=2000" if flow <= thr else "F>2000"
        return f"F = {flow:g} m³/ngày → nhóm cột {st['flow_regime'].replace('<=', ' ≤ ').replace('>', ' > ')}"

    def _act_special_cases(self, rule: Rule, st: dict[str, Any]) -> str | None:
        ctx = st["ctx"]
        p = rule.params
        if ctx.get("is_cooling_water"):
            st["mode"] = "cooling"
            st["table1"] = False
            st["params"] = list(p["cooling_parameters"])
            if ctx.get("uses_chlorine"):
                st["params"] += list(p["cooling_chlorine_parameters"])
            st["param_notes"].append(
                "Nước làm mát: chỉ nhiệt độ (+ Clo, Chloroform nếu dùng Clo) — Mục 4.3.1"
            )
            return "nước làm mát"
        if ctx.get("is_aquaculture"):
            st["mode"] = "aquaculture"
            st["flow_regime"] = "F<=2000"
            st["params"] = list(p["aquaculture_table2_parameters"])
            st["param_notes"].append(
                "Nuôi trồng thủy sản: BOD5, COD/TOC, TSS theo Bảng 1 mức F ≤ 2000 không phân biệt lưu lượng; "
                "pH, coliform, T-N, T-P theo Bảng 2 — Mục 4.3.2; nước đầu vào vượt ngưỡng → "
                "không vượt giá trị đầu vào (4.3.3)"
            )
            return "nước thải nuôi trồng thủy sản"
        return False

    def _act_sector_parameters(self, rule: Rule, st: dict[str, Any]) -> str | None:
        if st["mode"]:  # cooling / aquaculture have their own parameter sets
            return False
        sector_id = st["ctx"]["sector_id"]
        row = self.s.execute(
            select(m.TableRow).where(
                m.TableRow.table_code == rule.params["table_code"],
                m.TableRow.entities["sector_id"].astext == sector_id,
            )
        ).scalar_one_or_none()
        if row is None:
            self._add_missing(st, "sector_id", "Loại hình (Phụ lục 2)", rule)
            st["missing"][-1].reason = f"không tìm thấy loại hình {sector_id!r} trong Phụ lục 2"
            return "loại hình không có trong Phụ lục 2"
        base = list(rule.params.get("base_parameters", []))
        chars = list(row.entities.get("characteristic_parameter_ids") or [])
        for pid in base + chars:
            if pid not in st["params"]:
                st["params"].append(pid)
        st["extra_evidence"].append(f"chunk:{row.row_id}")
        for cp in row.conditional_parameters:
            txt = cp.get("parameters_raw") or ""
            cond = cp.get("condition_text")
            st["param_notes"].append(
                f"Phụ lục 2 cột 5 ({row.cells.get('sector', sector_id)}): "
                + (f"{cond}: {txt}" if cond else txt)
            )
        if row.cells.get("characteristic_parameters", "").startswith("Xác định tại Mục 4.3.2"):
            st["param_notes"].append(
                "Thông số đặc trưng xác định tại Mục 4.3.2 (nuôi trồng thủy sản)"
            )
        return f"{row.cells.get('sector', sector_id)} → {', '.join(chars) or 'không có thông số đặc trưng riêng'}"

    def _act_add_parameters(self, rule: Rule, st: dict[str, Any]) -> str | None:
        for pid in rule.params["parameters"]:
            if pid not in st["params"]:
                st["params"].append(pid)
        return ", ".join(rule.params["parameters"])

    def _act_receiving_body(self, rule: Rule, st: dict[str, Any]) -> str | None:
        ctx = st["ctx"]
        p = rule.params
        body = ctx["receiving_body_type"]
        tp = p["tp_parameter"]
        sector_name = self._sector_name(ctx.get("sector_id")) or ""
        # row 4.2 lists aquaculture explicitly, so the special-case flag counts as well
        special = bool(ctx.get("is_aquaculture")) or any(
            k in sector_name.lower() for k in p["special_sector_keywords"]
        )
        rows = p["tp_rows"]
        if body == "lake":
            chosen = rows["lake"]
        elif body in ("river", "sea", "saline_brackish"):
            chosen = rows["special"] if special else rows["other"]
        else:
            chosen = None
        detail = []
        if chosen:
            st["row_filters"][tp] = [chosen]
            detail.append(f"T-P → dòng {chosen.split('.')[-1]}")
        if body in p.get("chloride_excluded_when", []):
            st["excluded_params"].add(p["chloride_parameter"])
            detail.append("Clorua không áp dụng (nguồn nước mặn, nước lợ)")
        return "; ".join(detail) or None

    def _act_transition(self, rule: Rule, st: dict[str, Any]) -> str | None:
        ctx = st["ctx"]
        p = rule.params
        circ = p["circular_id"]
        fact = self._fact(circ, "effective_date")
        eff = _parse_date(fact.value) if fact else None
        deadline = _parse_date(p.get("deadline"))
        as_of: dt.date = ctx["as_of_date"]
        status = ctx["project_status"]
        dossier = ctx.get("dossier_date")
        old = [
            self.docs[r.target_document_id].document_code
            for r in self.s.execute(
                select(m.DocumentRelation).where(
                    m.DocumentRelation.source_document_id == p["document_id"],
                    m.DocumentRelation.type == "REPLACES",
                    m.DocumentRelation.origin == "registry",
                )
            ).scalars()
            if r.target_document_id in self.docs
        ]
        code = self.docs[p["document_id"]].document_code
        if eff is None:
            st["transition"] = TransitionOutcome(
                applies="unknown",
                document_id=p["document_id"],
                effective_date=None,
                deadline=deadline,
                old_standards=old,
                explanation="Không có ngày hiệu lực trong sổ đăng ký",
            )
            self._add_missing(st, "effective_date", "Ngày hiệu lực Thông tư", rule)
            return None
        src = "do quản trị viên khai báo" if fact.source == "admin_declared" else "từ văn bản"
        is_new = status == "new" or (dossier is not None and dossier >= eff)
        if is_new:
            if as_of < eff:
                applies, expl = (
                    "not_yet_effective",
                    (
                        f"{code} áp dụng cho dự án mới kể từ {eff:%d/%m/%Y} ({src}); thời điểm đánh giá "
                        f"{as_of:%d/%m/%Y} trước ngày hiệu lực"
                    ),
                )
            else:
                applies, expl = (
                    "new_standard",
                    f"Dự án mới (hồ sơ từ {eff:%d/%m/%Y}) áp dụng {code} ({src})",
                )
        elif deadline and as_of >= deadline:
            applies, expl = (
                "new_standard",
                (
                    f"Từ {deadline:%d/%m/%Y} mọi cơ sở/dự án chuyển tiếp phải đáp ứng {code}"
                    + (
                        " trừ trường hợp UBND tỉnh ban hành lộ trình sớm hơn"
                        if p.get("local_roadmap_exception")
                        else ""
                    )
                ),
            )
        else:
            applies, expl = (
                "old_standard_until_deadline",
                (
                    f"Cơ sở/dự án đã vận hành hoặc có hồ sơ trước {eff:%d/%m/%Y} tiếp tục áp dụng quy chuẩn cũ "
                    f"({', '.join(old) or 'ngoài corpus'}) đến hết {(deadline - dt.timedelta(days=1)):%d/%m/%Y}; "
                    f"khuyến khích áp dụng {code} sớm"
                    + (
                        "; UBND tỉnh có thể ban hành lộ trình sớm hơn"
                        if p.get("local_roadmap_exception")
                        else ""
                    )
                ),
            )
        if p.get("local_roadmap_exception") and applies != "new_standard":
            self._add_missing(st, "local_roadmap", "Lộ trình của UBND cấp tỉnh", rule)
            st["missing"][
                -1
            ].reason = "TT 45/2024 Điều 4.2 cho phép UBND tỉnh ban hành lộ trình áp dụng sớm hơn"
        st["transition"] = TransitionOutcome(
            applies=applies,
            document_id=p["document_id"],
            effective_date=eff,
            effective_source=src,
            deadline=deadline,
            old_standards=old,
            explanation=expl,
        )
        if applies == "old_standard_until_deadline":
            st["warnings"].append(
                f"Theo điều khoản chuyển tiếp, {code} chưa bắt buộc với cơ sở này tới {deadline:%d/%m/%Y}; "
                "ngưỡng dưới đây là của quy chuẩn mới (tham khảo / áp dụng sớm)"
            )
        return expl

    def _act_equipment_match(self, rule: Rule, st: dict[str, Any]) -> str | None:
        ctx = st["ctx"]
        eq = ctx["equipment_type_id"]
        rules = self._rules_for_tables(rule.params["tables"])
        matched = [r for r in rules if r.equipment_type_id == eq]
        detail = []
        if not matched:
            fb = set(rule.params.get("fallback_equipment_ids", []))
            matched = [r for r in rules if r.equipment_type_id in fb]
            st["warnings"].append(
                f"Thiết bị {eq!r} không có dòng riêng trong Bảng 1/2 → áp dụng dòng 'Các thiết bị xả thải khác' "
                "(cần chuyên gia xác nhận loại thiết bị)"
            )
            detail.append("dùng dòng thiết bị xả thải khác")
        fuel = ctx.get("fuel_type")
        if fuel and fuel != "unknown":
            keep = []
            for r in matched:
                fc = [c for c in r.conditions if c.get("type") == "fuel_type"]
                if fc and all(c.get("value") != fuel for c in fc):
                    continue
                keep.append(r)
            if not keep:
                st["warnings"].append(
                    f"Nhiên liệu {fuel!r} không khớp dòng thiết bị đã chọn (dòng này quy định nhiên liệu "
                    "khác) — kiểm tra lại loại thiết bị; giữ nguyên dòng đã chọn để tham khảo"
                )
            elif len(keep) != len(matched):
                detail.append(f"lọc theo nhiên liệu {fuel}")
                matched = keep
        cap_v, cap_u = ctx.get("capacity_value"), ctx.get("capacity_unit")
        if cap_v is not None and cap_u:
            keep = []
            for r in matched:
                cc = [c for c in r.conditions if c.get("type") == "capacity"]
                ok = True
                for c in cc:
                    cand = [c] + ([c["alt"]] if c.get("alt") else [])
                    same = [
                        x for x in cand if x.get("unit") == cap_u and x.get("value") is not None
                    ]
                    if not same:
                        continue
                    x = same[0]
                    op = x.get("op") or ">="
                    v = float(x["value"])
                    ok = {
                        "<": cap_v < v,
                        "<=": cap_v <= v,
                        ">": cap_v > v,
                        ">=": cap_v >= v,
                        "=": cap_v == v,
                    }.get(op, True)
                    if not ok:
                        break
                if ok:
                    keep.append(r)
            if not keep:
                st["warnings"].append(
                    f"Công suất {cap_v:g} {cap_u} không thỏa điều kiện công suất của dòng thiết bị đã "
                    "chọn — chọn dòng thiết bị có ngưỡng công suất phù hợp; giữ nguyên dòng đã chọn để tham khảo"
                )
            elif len(keep) != len(matched):
                detail.append(f"lọc theo công suất {cap_v:g} {cap_u}")
                matched = keep
        if ctx.get("co_processes_waste") and not any(
            c.get("type") == "co_processing" for r in matched for c in r.conditions
        ):
            st["notes"].append(
                "Có đồng xử lý chất thải nhưng loại thiết bị đã chọn không phải dòng 'đồng xử lý' "
                "— kiểm tra lại loại thiết bị (ví dụ lò nung clanke đồng xử lý)"
            )
        st["equipment_rules"] = matched
        params = []
        for r in matched:
            if r.parameter_id and r.parameter_id not in params:
                params.append(r.parameter_id)
        st["params"] = params
        return "; ".join(detail) or f"{len(matched)} dòng ngưỡng khớp thiết bị"

    def _act_unknown_parameters(self, rule: Rule, st: dict[str, Any]) -> str | None:
        wanted = st["ctx"].get("parameters") or []
        if not wanted:
            return False
        covered = set(st["params"])
        unknown = [p for p in wanted if p not in covered]
        if not unknown:
            return False
        st["warnings"].append(
            "Thông số quan tâm chưa có dòng ngưỡng cho thiết bị này: "
            + ", ".join(unknown)
            + " — "
            + rule.message
        )
        return ", ".join(unknown)

    def _act_effective_fact(self, rule: Rule, st: dict[str, Any]) -> str | None:
        fact = self._fact(rule.params["circular_id"], rule.params["fact_type"])
        if fact is None:
            self._add_missing(st, "effective_date", "Ngày hiệu lực", rule)
            return "registry fact không có"
        if fact.source == "admin_declared":
            doc = self.docs.get(rule.params["circular_id"])
            state = (
                "văn bản trong hệ thống là bản dự thảo"
                if doc is not None and doc.text_status == "draft"
                else "văn bản không in ngày, ngày lấy từ sổ đăng ký"
            )
            st["warnings"].append(
                f"Ngày hiệu lực {fact.value} do quản trị viên khai báo ({fact.declared_by}); {state}"
            )
        src = "do quản trị viên khai báo" if fact.source == "admin_declared" else "từ văn bản"
        return f"hiệu lực {fact.value} ({src})"

    def _act_monitoring_note(self, rule: Rule, st: dict[str, Any]) -> str | None:
        st["notes"].append(rule.message)
        return None

    # ------------------------------------------------------------------ selection

    def _rules_for_tables(self, tables: list[str]) -> list[m.LimitRule]:
        return list(
            self.s.execute(
                select(m.LimitRule)
                .where(m.LimitRule.table_code.in_(tables))
                .order_by(m.LimitRule.line_start)
            ).scalars()
        )

    def _select_limits(self, st: dict[str, Any]) -> list[SelectedLimit]:
        zone = st["zone"]
        out: list[SelectedLimit] = []
        if st["domain"] == "wastewater":
            active = any(c.status in ("candidate", "indirect") for c in st["candidates"].values())
            if not active or zone is None:
                return out
            selected: list[tuple[m.LimitRule, str]] = []
            if st["table1"]:
                regime = st["flow_regime"]
                for r in self._rules_for_tables(["QCVN40_2025.B1"]):
                    if r.zone != zone:
                        continue
                    if regime is None:
                        continue  # W-07 skipped -> missing flow, no Table 1 rows
                    if not any(
                        c.get("type") == "flow_regime" and c.get("text") == regime
                        for c in r.conditions
                    ):
                        continue
                    selected.append((r, "W-07" if st["mode"] != "aquaculture" else "W-10"))
            if st["table2"] and st["params"]:
                wanted = [p for p in st["params"] if p not in ("BOD5", "COD", "TOC", "TSS")]
                for r in self._rules_for_tables(["QCVN40_2025.B2"]):
                    if r.zone != zone or r.parameter_id not in wanted:
                        continue
                    if r.parameter_id in st["excluded_params"]:
                        continue
                    allowed = st["row_filters"].get(r.parameter_id)
                    if allowed is not None and r.row_id not in allowed:
                        continue
                    by = "W-09" if st["mode"] is None else "W-10"
                    if r.parameter_id in ("Residual_chlorine", "Chloroform") and st["ctx"].get(
                        "uses_chlorine"
                    ):
                        by = "W-11"
                    if r.parameter_id == "T_P" and st["row_filters"].get("T_P"):
                        by = "W-08"
                    selected.append((r, by))
            for r, by in selected:
                out.append(self._limit(r, [by]))
        else:
            if zone is None or st["equipment_rules"] is None:
                return out
            for r in st["equipment_rules"]:
                if r.zone == zone:
                    out.append(self._limit(r, ["E-04"]))
        return out

    def _limit(self, r: m.LimitRule, by: list[str]) -> SelectedLimit:
        return SelectedLimit(
            rule_code=r.rule_code,
            table_code=r.table_code,
            row_id=r.row_id,
            chunk_id=f"chunk:{r.row_id}",
            parameter_id=r.parameter_id,
            parameter_raw=r.parameter_raw,
            zone=r.zone,
            op=r.op,
            value=r.value,
            min=r.min,
            max=r.max,
            unit_id=r.unit_id,
            unit_raw=r.unit_raw,
            ref_o2=r.ref_o2,
            conditions=[
                c.get("text") or f"{c.get('type')} {c.get('op') or ''} {c.get('value')}"
                for c in r.conditions
                if c.get("type") not in ("zone",)
            ],
            exceptions=list(r.exceptions),
            citation_label=r.citation_label,
            review_status=r.review_status,
            selected_by=by,
        )

    # ------------------------------------------------------------------ finish

    def _finish(
        self, req: AssessmentRequest, st: dict[str, Any], limits: list[SelectedLimit]
    ) -> AssessmentResult:
        warnings = list(st["warnings"])
        drafts: list[str] = []
        for ar in st["applied"]:
            for ev in ar.evidence:
                if ev.text_status == "draft" and ev.document_id not in drafts:
                    drafts.append(ev.document_id)
        for lim in limits:
            doc = self.docs.get(self._doc_of_table(lim.table_code))
            if doc and doc.text_status == "draft" and doc.document_id not in drafts:
                drafts.append(doc.document_id)
        for d in drafts:
            warnings.append(DRAFT_WARNING.format(code=self.docs[d].document_code))
        unverified = {self._doc_of_table(lim.table_code) for lim in limits} & {
            d for d, doc in self.docs.items() if doc.text_status == "unverified"
        }
        for d in sorted(unverified):
            warnings.append(
                f"Text {self.docs[d].document_code} chưa đối chiếu với bản ký chính thức (unverified)"
            )
        pending = sum(1 for lim in limits if lim.review_status != "approved")
        if pending:
            warnings.append(PENDING_WARNING.format(n=pending))
        ev_path = self._evidence_path(st, limits)
        params = [p for p in st["params"] if p not in st["excluded_params"]]
        return AssessmentResult(
            assessment_id=str(uuid.uuid4()),
            domain=req.domain,
            rules_version=self.rs.version,
            as_of_date=st["ctx"]["as_of_date"],
            effective_zone=st["zone"],
            zone_defaulted=st["zone_defaulted"],
            candidates=list(st["candidates"].values()),
            transition=st["transition"],
            applied_rules=st["applied"],
            parameter_set=params,
            parameter_notes=st["param_notes"],
            limit_rules=limits,
            missing_information=st["missing"],
            warnings=warnings,
            notes=st["notes"],
            evidence_path=ev_path,
            disclaimer=self.rs.disclaimer,
        )

    def _evidence_path(
        self, st: dict[str, Any], limits: list[SelectedLimit]
    ) -> list[dict[str, Any]]:
        """Rule evidence + one structural path per table used (row → table → section → document)."""
        path: list[dict[str, Any]] = []
        seen: set[str] = set()
        for ar in st["applied"]:
            for ev in ar.evidence:
                if ev.chunk_id in seen:
                    continue
                seen.add(ev.chunk_id)
                path.append({"kind": "rule_evidence", "rule_id": ar.rule_id, **ev.model_dump()})
        for cid in st["extra_evidence"]:
            if cid not in seen:
                seen.add(cid)
                c = self.s.get(m.EvidenceChunk, cid)
                if c:
                    path.append(
                        {
                            "kind": "row_evidence",
                            "chunk_id": cid,
                            "citation_label": c.citation_label,
                        }
                    )
        for table_code in sorted({lim.table_code for lim in limits}):
            first = next(lim for lim in limits if lim.table_code == table_code)
            chain = self.graph.path_for_chunk(first.chunk_id)
            path.append({"kind": "structure_path", "table_code": table_code, "path": chain})
        return path

    # ------------------------------------------------------------------ helpers

    def _fact(self, doc_id: str, fact_type: str) -> m.RegistryFact | None:
        return self.s.execute(
            select(m.RegistryFact).where(
                m.RegistryFact.document_id == doc_id,
                m.RegistryFact.fact_type == fact_type,
                m.RegistryFact.scope.is_(None),
            )
        ).scalar_one_or_none()

    def _sector_name(self, sector_id: str | None) -> str | None:
        if not sector_id:
            return None
        return next((s.name for s in self.dicts.industry_sectors if s.sector_id == sector_id), None)

    def _doc_of_table(self, table_code: str) -> str:
        t = self.s.get(m.TableDef, table_code)
        return t.document_id if t else ""


def form_schema(domain: str, settings: Settings | None = None) -> dict[str, Any]:
    """Field specs with options expanded from the dictionaries (for the UI form)."""
    settings = settings or get_settings()
    rs = get_ruleset(settings)
    d = _cached_dicts(str(settings.data_dir))
    schema = rs.schemas[domain]
    fields = []
    for f in schema.fields:
        spec: dict[str, Any] = f.model_dump(exclude={"options_from"})
        opts: list[dict[str, str]] | None = None
        if f.options_from == "sectors":
            opts = [
                {"value": s.sector_id, "label": f"{s.tt}. {s.name} ({', '.join(s.vsic_codes)})"}
                for s in d.industry_sectors
            ]
        elif f.options_from == "receiving_bodies":
            opts = [{"value": r.code, "label": r.name} for r in d.receiving_body_types] + [
                {"value": "unknown", "label": "Không rõ"}
            ]
        elif f.options_from == "zones":
            opts = [
                {"value": z.code, "label": f"{z.name}: {z.definition[:90]}…"}
                for z in d.zones
                if z.domain == domain
            ] + [{"value": "unknown", "label": "Không rõ / chưa xác định"}]
        elif f.options_from == "fuels":
            opts = [{"value": x.code, "label": x.name} for x in d.fuel_types] + [
                {"value": "unknown", "label": "Không rõ / không đốt nhiên liệu"}
            ]
        elif f.options_from == "equipment":
            opts = [
                {
                    "value": e.equipment_id,
                    "label": ("  " * (len(e.path) - 1)) + ".".join(e.path) + " " + e.name,
                }
                for e in d.equipment_types
                if not e.is_group_header
            ]
        elif f.options_from == "parameters_emission":
            opts = [
                {"value": p.parameter_id, "label": p.name}
                for p in d.parameters
                if "emission" in p.domains or "both" in p.domains
            ]
        if opts is not None:
            spec["options"] = opts
        fields.append(spec)
    return {"domain": domain, "label": schema.label, "fields": fields, "rules_version": rs.version}

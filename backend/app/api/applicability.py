"""Applicability assessment API (PLAN.md §9, Phase 3) + dictionaries for the form."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.applicability.engine import Assessor, form_schema
from app.applicability.models import AssessmentRequest, AssessmentResult
from app.core.config import get_settings
from app.db import models as m
from app.graph.store import PostgresGraphStore
from app.ontology.loader import load_dictionaries

router = APIRouter(prefix="/api/v1", tags=["applicability"])
DB = Annotated[Session, Depends(get_db)]


@router.get("/applicability/schema/{domain}")
def get_schema(domain: str) -> dict[str, Any]:
    if domain not in ("wastewater", "emission"):
        raise HTTPException(404, "domain must be wastewater | emission")
    return form_schema(domain)


@router.post("/applicability-assessments", response_model=AssessmentResult)
def create_assessment(req: AssessmentRequest, db: DB) -> AssessmentResult:
    result = Assessor(db).assess(req)
    db.add(
        m.ApplicabilityAssessment(
            id=result.assessment_id,
            domain=req.domain,
            rules_version=result.rules_version,
            context=req.model_dump(mode="json")["context"],
            result=result.model_dump(mode="json"),
        )
    )
    db.commit()
    return result


@router.get("/applicability-assessments/{assessment_id}", response_model=AssessmentResult)
def get_assessment(assessment_id: str, db: DB) -> AssessmentResult:
    row = db.get(m.ApplicabilityAssessment, assessment_id)
    if row is None:
        raise HTTPException(404, f"assessment {assessment_id} not found")
    return AssessmentResult.model_validate(row.result)


@router.get("/dictionaries/{name}")
def get_dictionary(name: str) -> list[dict[str, Any]]:
    d = load_dictionaries(settings=get_settings())
    table = {
        "parameters": d.parameters,
        "units": d.units,
        "zones": d.zones,
        "sectors": d.industry_sectors,
        "equipment": d.equipment_types,
        "fuels": d.fuel_types,
        "receiving_bodies": d.receiving_body_types,
        "parameter_groups": d.parameter_groups,
    }
    if name not in table:
        raise HTTPException(404, f"unknown dictionary {name}; one of {sorted(table)}")
    return [x.model_dump() for x in table[name]]


@router.get("/graph/nodes/{node_id:path}/neighbors")
def get_neighbors(node_id: str, db: DB) -> dict[str, Any]:
    out = PostgresGraphStore(db).neighbors(node_id)
    if not out:
        raise HTTPException(404, f"node {node_id} not found")
    return out


@router.get("/graph/evidence-path/{chunk_id:path}")
def get_evidence_path(chunk_id: str, db: DB) -> list[dict[str, Any]]:
    path = PostgresGraphStore(db).path_for_chunk(chunk_id)
    if not path:
        raise HTTPException(404, f"chunk {chunk_id} not found")
    return path

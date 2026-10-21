"""Ids of the projection nodes that other modules have to name (the builder, the seeds of a
question, the mention linker). An id is built from the natural key of the record, never from a
database sequence: two loads of the same corpus must give the same graph."""

from __future__ import annotations


def zone_id(scheme: str, code: str) -> str:
    """A zone is a column of the scheme of one document (`limit_rules.zone_scheme`)."""
    return f"zone:{scheme}:{code}"


def registry_fact_id(document_id: str, fact_type: str) -> str:
    return f"regfact:{document_id}:{fact_type}"


def document_version_id(document_id: str, sha256: str) -> str:
    return f"ver:{document_id}:{sha256[:12]}"

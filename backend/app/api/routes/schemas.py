"""Exposes complaint schema definitions so the frontend can render field help
without duplicating business config.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.domain.complaint_schemas.registry import ComplaintSchemaError, get_registry
from app.domain.complaint_schemas.spec import ComplaintSchema

router = APIRouter()


@router.get("", response_model=list[ComplaintSchema])
def list_schemas() -> list[ComplaintSchema]:
    return get_registry().all()


@router.get("/{complaint_type}", response_model=ComplaintSchema)
def get_schema(complaint_type: str) -> ComplaintSchema:
    try:
        return get_registry().get(complaint_type)
    except ComplaintSchemaError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

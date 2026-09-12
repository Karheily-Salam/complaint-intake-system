"""API schemas for the ML assistance features.

Every response here is advisory: it describes what a model suggests and why,
and carries no customer identifiers beyond ticket references the caller is
already authorised to open.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel
from app.schemas.ticket import TicketDetail


class SimilarTicketOut(BaseModel):
    reference: str
    type: str
    status: str
    created_at: datetime
    similarity: float
    # possible_duplicate | similar
    relation: str
    same_customer: bool
    # Keys (never values) of the extracted fields both tickets share.
    matched_fields: list[str]
    # Machine-readable reasons, e.g. "semantic_similarity", "same_customer",
    # "same_withdrawal_transaction_id" - the UI translates them.
    reasons: list[str]


class CorrectionIn(BaseModel):
    """A staff correction to a ticket's complaint type or one collected field."""

    kind: Literal["classification", "field"]
    # Required for kind="field"; ignored for classification.
    field_key: str | None = Field(default=None, max_length=80)
    corrected_value: str = Field(min_length=1, max_length=500)
    note: str | None = Field(default=None, max_length=500)


class FeedbackOut(ORMModel):
    id: int
    kind: str
    field_key: str | None
    original_value: str | None
    corrected_value: str
    original_source: str | None
    original_confidence: float | None
    model_name: str | None
    model_version: str | None
    language_code: str | None
    note: str | None
    exported: bool
    created_at: datetime


class CorrectionOut(BaseModel):
    feedback: FeedbackOut
    ticket: TicketDetail


class SimilarTicketsOut(BaseModel):
    reference: str
    model_version: str
    similar_threshold: float
    duplicate_threshold: float
    items: list[SimilarTicketOut]

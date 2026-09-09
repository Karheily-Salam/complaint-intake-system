"""Value objects passed to and returned by :class:`ConversationEngine`.

These are framework-free and DB-free. The service layer maps ORM rows to a
:class:`ConversationState`, calls the engine, then persists the
:class:`EngineOutcome`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.ai.base import Classification, ExtractedField, ReplyDraft
from app.domain.complaint_schemas.spec import FieldSpec
from app.domain.enums import ConversationStatus
from app.domain.validation import FieldValidationResult


@dataclass
class ConversationState:
    latest_message: str
    complaint_type: str | None = None
    method_key: str | None = None
    collected: dict[str, str] = field(default_factory=dict)
    customer_name: str | None = None
    # Every inbound customer message, oldest first (used for summarisation).
    inbound_transcript: list[str] = field(default_factory=list)


@dataclass
class EngineOutcome:
    classification: Classification | None = None
    complaint_type: str | None = None
    method_key: str | None = None

    newly_extracted: list[ExtractedField] = field(default_factory=list)
    missing_fields: list[FieldSpec] = field(default_factory=list)
    validation_errors: list[FieldValidationResult] = field(default_factory=list)

    concise_description: str | None = None
    is_complete: bool = False
    next_status: ConversationStatus = ConversationStatus.COLLECTING_INFO
    reply: ReplyDraft | None = None
    needs_method_selection: bool = False

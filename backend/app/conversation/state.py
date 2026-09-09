"""Value objects passed to and returned by :class:`ConversationEngine`.

Framework-free and DB-free. The service layer maps ORM rows into a
:class:`ConversationState`, calls the engine, then persists the
:class:`EngineOutcome`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.ai.base import Classification, ReplyDraft
from app.domain.complaint_schemas.spec import FieldSpec
from app.domain.enums import ConversationStatus, FieldStatus

_PRESENT_STATUSES = {FieldStatus.VALIDATED, FieldStatus.CONFIRMED}


@dataclass
class CollectedField:
    key: str
    value: str | None
    status: FieldStatus


@dataclass
class ConversationState:
    """Everything the engine needs to process one new inbound message."""

    latest_message: str
    complaint_type: str | None = None
    method_key: str | None = None
    collected: list[CollectedField] = field(default_factory=list)
    customer_name: str | None = None
    # Every inbound customer message, oldest first (used for summarisation).
    inbound_transcript: list[str] = field(default_factory=list)

    def by_key(self) -> dict[str, CollectedField]:
        return {f.key: f for f in self.collected}

    def valid_values(self) -> dict[str, str]:
        return {
            f.key: f.value
            for f in self.collected
            if f.status in _PRESENT_STATUSES and f.value
        }


@dataclass
class FieldOutcome:
    """The resolved state of one field after processing the latest message."""

    key: str
    value: str | None
    status: FieldStatus
    validation_error: str | None = None
    confidence: float | None = None
    changed: bool = False  # did the latest message change this field?

    @property
    def is_present(self) -> bool:
        return self.status in _PRESENT_STATUSES and bool(self.value)


@dataclass
class EngineOutcome:
    classification: Classification | None = None
    complaint_type: str | None = None
    method_key: str | None = None

    # Full resolved field set to persist (only fields that exist/were touched).
    fields: list[FieldOutcome] = field(default_factory=list)
    missing_fields: list[FieldSpec] = field(default_factory=list)
    invalid_fields: list[FieldSpec] = field(default_factory=list)

    concise_description: str | None = None
    is_complete: bool = False
    awaiting_clarification: bool = False
    next_status: ConversationStatus = ConversationStatus.COLLECTING_INFO
    reply: ReplyDraft | None = None

"""Domain enumerations shared across models, schemas and services."""

from __future__ import annotations

from enum import StrEnum


class ComplaintType(StrEnum):
    WITHDRAWAL = "withdrawal"
    DEPOSIT = "deposit"
    OTHER = "other"


class ConversationStatus(StrEnum):
    OPEN = "open"                    # created, not yet classified
    COLLECTING_INFO = "collecting_info"  # waiting on the customer for missing fields
    VALIDATING = "validating"        # all fields present, running validation
    COMPLETED = "completed"          # ticket generated
    ABANDONED = "abandoned"          # closed without completion


class ComplaintStatus(StrEnum):
    DRAFT = "draft"
    COLLECTING = "collecting"
    READY = "ready"                  # validated, ready to ticket
    TICKETED = "ticketed"


class FieldStatus(StrEnum):
    EXTRACTED = "extracted"          # provided by AI extraction, unverified
    CONFIRMED = "confirmed"          # customer confirmed / restated explicitly
    VALIDATED = "validated"          # passed schema validation
    INVALID = "invalid"             # failed schema validation


class FieldSource(StrEnum):
    CUSTOMER_MESSAGE = "customer_message"
    AI_INFERENCE = "ai_inference"
    EMPLOYEE = "employee"


class MessageDirection(StrEnum):
    INBOUND = "inbound"              # customer -> system
    OUTBOUND = "outbound"           # system -> customer


class TicketStatus(StrEnum):
    NEW = "new"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    CLOSED = "closed"


class TicketPriority(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"

"""AI provider abstraction.

The conversation engine depends only on :class:`AIProvider` and on the typed
request/response models below. Concrete providers (rule-based, Ollama, or a
future hosted model) differ only in *how* they populate these structures.

Design rules:
- No business logic here. Field definitions, required-ness and validation come
  from the complaint schema registry and are passed in as :class:`FieldSpec`.
- The provider never decides conversation state. It only classifies, extracts,
  summarises and generates language; the deterministic engine owns every
  decision.
- Providers must never invent information that is not supported by the
  customer's message.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import StrEnum

from pydantic import BaseModel, Field

from app.domain.complaint_schemas.spec import FieldSpec


class AIProviderError(RuntimeError):
    """Raised when an AI provider cannot fulfil a request (e.g. Ollama offline)."""


class TypeOption(BaseModel):
    """A candidate complaint type offered to the classifier."""

    type: str
    label: str
    description: str = ""


class MLSignal(BaseModel):
    """What the statistical classifier said, recorded whether or not it was used.

    ``label`` is None when the model abstained. Kept next to the decision so
    prediction logging, evaluation and staff corrections can compare the
    model with what the deterministic system actually did.
    """

    label: str | None = None
    top_label: str | None = None
    confidence: float = 0.0
    probabilities: dict[str, float] = Field(default_factory=dict)
    abstained: bool = True
    model_name: str = ""
    model_version: str = ""
    latency_ms: float = 0.0


class Classification(BaseModel):
    type: str | None = None
    confidence: float = 0.0
    rationale: str = ""
    # Which layer produced ``type``: "rules" (deterministic keyword match),
    # "ml" (calibrated classifier, only when the rules had no match) or
    # "provider" (the configured provider's own classify). None for providers
    # that predate this field.
    decided_by: str | None = None
    ml: MLSignal | None = None


class LanguageDetection(BaseModel):
    """The natural language of a customer message.

    ``code`` is a lowercase ISO 639-1 code (e.g. ``"en"``, ``"ru"``, ``"ar"``),
    or ``None`` when the message carries no reliable language signal (e.g. it
    is only numbers or symbols) - the engine then falls back to the
    conversation's previously known language.
    """

    code: str | None = None
    confidence: float = 0.0


class ExtractedField(BaseModel):
    key: str
    value: str
    confidence: float = 0.5


class ExtractionResult(BaseModel):
    fields: list[ExtractedField] = Field(default_factory=list)

    def as_dict(self) -> dict[str, str]:
        return {f.key: f.value for f in self.fields}


class ReplyKind(StrEnum):
    ASK = "ask"                # request specific missing / invalid fields
    CLARIFY = "clarify"        # message is too vague; ask an open clarification
    ACKNOWLEDGE = "acknowledge"  # everything collected; confirm receipt


class InvalidField(BaseModel):
    key: str
    label: str
    error: str
    # Optional example of a valid value (from the schema), so a provider can
    # show the customer a concrete format hint without needing the raw
    # FieldSpec. Never customer-facing on its own - just phrasing material.
    example: str | None = None


class CollectedFieldView(BaseModel):
    """One resolved field to display in the final ticket confirmation.

    ``label`` is the schema's own (English) label - only a fallback for a
    provider that doesn't have its own localized display name for ``key``;
    it must never be replaced by the raw internal ``key`` itself.
    """

    key: str
    label: str
    value: str


class ReplyRequest(BaseModel):
    """Everything a provider needs to draft one customer-facing message.

    The engine decides ``kind`` and which fields to include; the provider only
    turns this into natural language.
    """

    kind: ReplyKind
    complaint_label: str
    customer_name: str | None = None
    missing_fields: list[FieldSpec] = Field(default_factory=list)
    invalid_fields: list[InvalidField] = Field(default_factory=list)
    # Resolved field values to show in an ACKNOWLEDGE (ticket-created) reply -
    # only ever populated for that kind.
    collected_fields: list[CollectedFieldView] = Field(default_factory=list)
    guidance: str = ""                 # free-text steer for CLARIFY messages
    ticket_reference: str | None = None
    # ISO 639-1 code the engine has resolved for this turn (see
    # ConversationEngine._resolve_language). The provider must write the reply
    # body in this language - it never decides the language itself here, only
    # how to phrase the content in it.
    language_code: str = "en"


class ReplyDraft(BaseModel):
    body: str


class AIProvider(ABC):
    """Replaceable AI backend. Implementations must be free of conversation state."""

    name: str = "base"

    async def available(self) -> bool:
        """Whether the provider is ready to serve requests (best-effort)."""
        return True

    @abstractmethod
    async def classify(self, message: str, options: list[TypeOption]) -> Classification:
        """Pick the most likely complaint type from ``options``.

        Return ``type=None`` (or a low ``confidence``) when the message is too
        ambiguous to classify - the engine, not the provider, decides what to do
        with that.
        """

    @abstractmethod
    async def extract(
        self,
        message: str,
        specs: list[FieldSpec],
        known: dict[str, str] | None = None,
        pending_field: str | None = None,
    ) -> ExtractionResult:
        """Extract values for ``specs`` that are explicitly supported by ``message``.

        ``known`` holds already-validated fields. A provider may return a field
        that is already in ``known`` only if the message provides a *different*
        (corrected) value. It must never fabricate or guess a value.

        ``pending_field`` is the single field key the engine's most recent
        outbound message asked the customer for (``None`` if nothing specific
        was pending - e.g. the very first message, or after a clarification).
        This is an email conversation, not a sequence of independent
        messages: when set, the customer's reply should be interpreted
        *primarily* as an answer to this field - including a bare value with
        no label at all (e.g. just "583921") - without preventing extraction
        of any other field the message clearly also contains.
        """

    @abstractmethod
    async def summarize(self, transcript: list[str]) -> str:
        """Produce a concise, self-contained problem description from the customer's
        messages. Return an empty string if the messages do not describe a problem.
        """

    @abstractmethod
    async def detect_language(self, message: str) -> LanguageDetection:
        """Detect the natural language ``message`` is written in.

        This only *detects* - it never decides what language to reply in or
        whether to switch; that policy (prefer the latest message, otherwise
        keep the conversation's existing language) lives in the engine.
        """

    @abstractmethod
    async def compose_reply(self, request: ReplyRequest) -> ReplyDraft:
        """Draft one customer-facing message for the given request, written in
        ``request.language_code``.
        """

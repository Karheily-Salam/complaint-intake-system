"""AI provider abstraction.

The conversation engine depends only on :class:`AIProvider` and on the typed
request/response models below. Concrete providers (rule-based, Ollama, or a
future hosted model) differ only in *how* they populate these structures.

Design rules:
- No business logic here. Field definitions, required-ness and validation come
  from the complaint schema registry and are passed in as :class:`FieldSpec`.
- Prompts belong to providers that need them (see ``providers/ollama.py`` and
  ``app/conversation/prompts/``), not to this interface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel, Field

from app.domain.complaint_schemas.spec import FieldSpec


class TypeOption(BaseModel):
    """A candidate complaint type offered to the classifier."""

    type: str
    label: str
    description: str = ""


class Classification(BaseModel):
    type: str | None = None
    confidence: float = 0.0
    rationale: str = ""


class ExtractedField(BaseModel):
    key: str
    value: str
    confidence: float = 0.5


class ExtractionResult(BaseModel):
    fields: list[ExtractedField] = Field(default_factory=list)

    def as_dict(self) -> dict[str, str]:
        return {f.key: f.value for f in self.fields}


class ReplyDraft(BaseModel):
    body: str


class AIProvider(ABC):
    """Replaceable AI backend. Implementations must be side-effect free."""

    name: str = "base"

    @abstractmethod
    async def classify(self, message: str, options: list[TypeOption]) -> Classification:
        """Pick the most likely complaint type from ``options`` (or ``None``)."""

    @abstractmethod
    async def extract(
        self,
        message: str,
        specs: list[FieldSpec],
        known: dict[str, str] | None = None,
    ) -> ExtractionResult:
        """Extract values for ``specs`` present in ``message``.

        ``known`` holds already-collected fields so a provider can avoid
        re-extracting or can resolve references. Implementations must not
        fabricate values that are not supported by the message.
        """

    @abstractmethod
    async def summarize(self, transcript: list[str]) -> str:
        """Produce a concise, self-contained problem description."""

    @abstractmethod
    async def compose_reply(
        self,
        missing: list[FieldSpec],
        *,
        complaint_label: str,
        customer_name: str | None = None,
        extra_context: str = "",
    ) -> ReplyDraft:
        """Draft a customer-facing message asking only for ``missing`` fields."""

"""Local LLM provider backed by Ollama (http://localhost:11434).

No paid API, no external network - Ollama runs on the developer's machine. Selected
with ``AI_PROVIDER=ollama``.

Guarantees / behaviour:
- Structured, typed Pydantic inputs and outputs (same contract as every provider).
- Field definitions are supplied dynamically from the complaint schema registry;
  nothing about withdrawal/deposit/other is hard-coded here.
- The model is instructed to extract only information explicitly present in the
  customer's message and never to invent values; results are additionally
  filtered against the allowed field keys and dropped if empty.
- Long, unstructured messages are passed through in full (no truncation).
- If Ollama is unreachable it either falls back to the rule-based provider
  (``OLLAMA_FALLBACK_TO_RULE_BASED=true``, the default) or raises
  :class:`AIProviderError` with a clear message.

The conversation engine depends only on :class:`AIProvider`; it never imports
this module.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import TypeVar

import httpx
from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.ai.base import (
    AIProvider,
    AIProviderError,
    Classification,
    ExtractedField,
    ExtractionResult,
    LanguageDetection,
    ReplyDraft,
    ReplyRequest,
    TypeOption,
)
from app.core.config import settings
from app.core.logging import get_logger
from app.domain.complaint_schemas.spec import FieldSpec

logger = get_logger(__name__)

T = TypeVar("T")

_TRANSPORT_ERRORS = (httpx.HTTPError, OSError)

# Presentation only, for the reply prompt ("write this in <name>") - the model
# still receives the ISO code and can handle any language it recognizes even
# if it isn't in this map (it falls back to the raw code as the name).
_LANGUAGE_NAMES: dict[str, str] = {
    "en": "English",
    "ru": "Russian",
    "ar": "Arabic",
}


class OllamaAIProvider(AIProvider):
    name = "ollama"

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        timeout: int | None = None,
        fallback: AIProvider | None = None,
    ) -> None:
        self._base_url = (base_url or settings.ollama_base_url).rstrip("/")
        self._model = model or settings.ollama_model
        self._timeout = timeout or settings.ollama_timeout_seconds
        self._fallback = fallback
        self._jinja = Environment(
            loader=FileSystemLoader(str(settings.prompt_template_dir)),
            autoescape=select_autoescape(enabled_extensions=()),
            trim_blocks=True,
            lstrip_blocks=True,
        )

    async def available(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{self._base_url}/api/tags")
                return resp.status_code == 200
        except _TRANSPORT_ERRORS:
            return False

    async def classify(self, message: str, options: list[TypeOption]) -> Classification:
        async def primary() -> Classification:
            prompt = self._render(
                "classify.jinja", message=message, options=[o.model_dump() for o in options]
            )
            data = await self._generate_json(prompt)
            allowed = {o.type for o in options}
            ctype = data.get("type")
            return Classification(
                type=ctype if ctype in allowed else None,
                confidence=_as_float(data.get("confidence"), 0.0),
                rationale=str(data.get("rationale", "")),
            )

        return await self._call("classify", primary, lambda fb: fb.classify(message, options))

    async def extract(
        self,
        message: str,
        specs: list[FieldSpec],
        known: dict[str, str] | None = None,
        pending_field: str | None = None,
    ) -> ExtractionResult:
        known = known or {}

        async def primary() -> ExtractionResult:
            prompt = self._render(
                "extract.jinja",
                message=message,
                fields=[_field_view(s) for s in specs],
                known=known,
                pending_field=pending_field,
            )
            data = await self._generate_json(prompt)
            allowed = {s.key for s in specs}
            out: list[ExtractedField] = []
            for item in data.get("fields", []) if isinstance(data, dict) else []:
                if not isinstance(item, dict):
                    continue
                key = item.get("key")
                value = item.get("value")
                if key not in allowed or value is None:
                    continue
                text = str(value).strip()
                if not text:
                    continue
                # Ignore a re-statement of an already-known value (not a correction).
                if known.get(key, "").strip().lower() == text.lower():
                    continue
                out.append(
                    ExtractedField(
                        key=key,
                        value=text,
                        confidence=_as_float(item.get("confidence"), 0.5),
                    )
                )
            return ExtractionResult(fields=out)

        return await self._call(
            "extract", primary, lambda fb: fb.extract(message, specs, known, pending_field)
        )

    async def summarize(self, transcript: list[str]) -> str:
        async def primary() -> str:
            prompt = self._render("summarize.jinja", transcript=transcript)
            return (await self._generate_text(prompt)).strip()

        return await self._call("summarize", primary, lambda fb: fb.summarize(transcript))

    async def detect_language(self, message: str) -> LanguageDetection:
        async def primary() -> LanguageDetection:
            prompt = self._render("language.jinja", message=message)
            data = await self._generate_json(prompt)
            code = data.get("code")
            code = str(code).strip().lower() if code else None
            confidence = _as_float(data.get("confidence"), 0.0)
            return LanguageDetection(code=code or None, confidence=confidence)

        return await self._call(
            "detect_language", primary, lambda fb: fb.detect_language(message)
        )

    async def compose_reply(self, request: ReplyRequest) -> ReplyDraft:
        async def primary() -> ReplyDraft:
            prompt = self._render(
                "reply.jinja",
                kind=request.kind.value,
                complaint_label=request.complaint_label,
                customer_name=request.customer_name,
                missing_fields=[_field_view(s) for s in request.missing_fields],
                invalid_fields=[f.model_dump() for f in request.invalid_fields],
                guidance=request.guidance,
                ticket_reference=request.ticket_reference,
                language_code=request.language_code,
                language_name=_LANGUAGE_NAMES.get(request.language_code, request.language_code),
            )
            return ReplyDraft(body=(await self._generate_text(prompt)).strip())

        return await self._call("compose_reply", primary, lambda fb: fb.compose_reply(request))

    # ---- internals ----

    async def _call(
        self,
        op: str,
        primary: Callable[[], Awaitable[T]],
        fallback: Callable[[AIProvider], Awaitable[T]],
    ) -> T:
        try:
            return await primary()
        except _TRANSPORT_ERRORS as exc:
            if self._fallback is not None:
                logger.warning("Ollama '%s' failed (%s); using rule-based fallback.", op, exc)
                return await fallback(self._fallback)
            raise AIProviderError(
                f"Ollama provider unavailable during '{op}': {exc}. "
                f"Is Ollama running at {self._base_url}?"
            ) from exc

    def _render(self, template_name: str, **ctx: object) -> str:
        return self._jinja.get_template(template_name).render(**ctx)

    async def _generate_text(self, prompt: str) -> str:
        payload = {"model": self._model, "prompt": prompt, "stream": False}
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(f"{self._base_url}/api/generate", json=payload)
            resp.raise_for_status()
            return resp.json().get("response", "")

    async def _generate_json(self, prompt: str) -> dict:
        payload = {"model": self._model, "prompt": prompt, "stream": False, "format": "json"}
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(f"{self._base_url}/api/generate", json=payload)
            resp.raise_for_status()
            text = resp.json().get("response", "{}")
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            logger.warning("Ollama returned non-JSON response: %s", text[:200])
            return {}


def _field_view(spec: FieldSpec) -> dict:
    """Schema-derived, prompt-friendly description of one field."""
    return {
        "key": spec.key,
        "label": spec.label,
        "type": spec.type.value,
        "required": spec.required,
        "description": spec.description,
        "hint": spec.extraction_hint or spec.description,
        "example": spec.example,
        "allowed_values": spec.validation.enum_values,
    }


def _as_float(value: object, default: float) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default

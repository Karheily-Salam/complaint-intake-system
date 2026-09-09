"""Local LLM provider backed by Ollama (http://localhost:11434).

No paid API, no external network - Ollama runs on the developer's machine. This
class is fully wired but only selected when ``AI_PROVIDER=ollama``.

Prompts are loaded from ``app/conversation/prompts/`` as Jinja templates. They
receive schema-derived data (labels, hints) and contain no business rules.
"""

from __future__ import annotations

import json

import httpx
from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.ai.base import (
    AIProvider,
    Classification,
    ExtractedField,
    ExtractionResult,
    ReplyDraft,
    TypeOption,
)
from app.core.config import settings
from app.core.logging import get_logger
from app.domain.complaint_schemas.spec import FieldSpec

logger = get_logger(__name__)


class OllamaAIProvider(AIProvider):
    name = "ollama"

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        timeout: int | None = None,
    ) -> None:
        self._base_url = (base_url or settings.ollama_base_url).rstrip("/")
        self._model = model or settings.ollama_model
        self._timeout = timeout or settings.ollama_timeout_seconds
        self._jinja = Environment(
            loader=FileSystemLoader(str(settings.prompt_template_dir)),
            autoescape=select_autoescape(enabled_extensions=()),
            trim_blocks=True,
            lstrip_blocks=True,
        )

    async def classify(self, message: str, options: list[TypeOption]) -> Classification:
        prompt = self._render(
            "classify.jinja",
            message=message,
            options=[o.model_dump() for o in options],
        )
        data = await self._generate_json(prompt)
        return Classification(
            type=data.get("type"),
            confidence=float(data.get("confidence", 0.0) or 0.0),
            rationale=str(data.get("rationale", "")),
        )

    async def extract(
        self,
        message: str,
        specs: list[FieldSpec],
        known: dict[str, str] | None = None,
    ) -> ExtractionResult:
        prompt = self._render(
            "extract.jinja",
            message=message,
            fields=[s.model_dump() for s in specs],
            known=known or {},
        )
        data = await self._generate_json(prompt)
        raw_fields = data.get("fields", []) if isinstance(data, dict) else []
        fields: list[ExtractedField] = []
        valid_keys = {s.key for s in specs}
        for item in raw_fields:
            key = item.get("key")
            value = item.get("value")
            if key in valid_keys and value:
                fields.append(
                    ExtractedField(
                        key=key,
                        value=str(value).strip(),
                        confidence=float(item.get("confidence", 0.5) or 0.5),
                    )
                )
        return ExtractionResult(fields=fields)

    async def summarize(self, transcript: list[str]) -> str:
        prompt = self._render("summarize.jinja", transcript=transcript)
        return (await self._generate_text(prompt)).strip()

    async def compose_reply(
        self,
        missing: list[FieldSpec],
        *,
        complaint_label: str,
        customer_name: str | None = None,
        extra_context: str = "",
    ) -> ReplyDraft:
        prompt = self._render(
            "compose_reply.jinja",
            missing=[s.model_dump() for s in missing],
            complaint_label=complaint_label,
            customer_name=customer_name,
            extra_context=extra_context,
        )
        return ReplyDraft(body=(await self._generate_text(prompt)).strip())

    # ---- transport ----

    def _render(self, template_name: str, **ctx: object) -> str:
        return self._jinja.get_template(template_name).render(**ctx)

    async def _generate_text(self, prompt: str) -> str:
        payload = {"model": self._model, "prompt": prompt, "stream": False}
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(f"{self._base_url}/api/generate", json=payload)
            resp.raise_for_status()
            return resp.json().get("response", "")

    async def _generate_json(self, prompt: str) -> dict:
        payload = {
            "model": self._model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(f"{self._base_url}/api/generate", json=payload)
            resp.raise_for_status()
            text = resp.json().get("response", "{}")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            logger.warning("Ollama returned non-JSON response: %s", text[:200])
            return {}

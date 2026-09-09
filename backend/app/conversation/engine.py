"""The AI Conversation Engine - deterministic multi-turn orchestrator.

It owns every decision about conversation state. The AI provider only classifies,
extracts, summarises and writes language; it never decides what happens next.

Guarantees (see the project brief):
- information may arrive in any order and across many messages
- an already-collected valid field is never asked for again
- newly extracted fields are merged with existing ones; empty/absent extraction
  never clears a field
- invalid values are detected and requested again
- a customer may correct a previously supplied value
- the complaint type may become known only after an ambiguous first message
- a deposit method may be discovered mid-conversation; its method-specific fields
  become required only once the method is known
- the complaint becomes ready/ticketable exactly when the deterministic schema
  requirements are satisfied

It depends only on :class:`AIProvider` and :class:`ComplaintSchemaRegistry` and
performs no I/O.
"""

from __future__ import annotations

from app.ai.base import AIProvider, InvalidField, ReplyKind, ReplyRequest, TypeOption
from app.conversation.state import ConversationState, EngineOutcome, FieldOutcome
from app.core.config import settings
from app.domain.complaint_schemas.registry import ComplaintSchemaRegistry
from app.domain.complaint_schemas.spec import ComplaintSchema, FieldSpec
from app.domain.enums import ConversationStatus, FieldStatus
from app.domain.validation import validate_field

_PROBLEM_DESCRIPTION_KEY = "problem_description"


class ConversationEngine:
    def __init__(self, ai: AIProvider, registry: ComplaintSchemaRegistry) -> None:
        self._ai = ai
        self._registry = registry

    async def advance(self, state: ConversationState) -> EngineOutcome:
        outcome = EngineOutcome(
            complaint_type=state.complaint_type,
            method_key=state.method_key,
        )

        # ---- 1. classify the complaint type (only while unknown) ----
        complaint_type = state.complaint_type
        if complaint_type is None:
            options = [
                TypeOption(type=s.type, label=s.label, description=s.description)
                for s in self._registry.all()
            ]
            classification = await self._ai.classify(state.latest_message, options)
            outcome.classification = classification
            if (
                classification.type is not None
                and classification.confidence >= settings.min_classification_confidence
            ):
                complaint_type = classification.type

        if complaint_type is None:
            return await self._clarify_type(state, outcome)

        schema = self._registry.get(complaint_type)
        outcome.complaint_type = complaint_type

        # ---- 2. seed current field state from what we already have ----
        current: dict[str, FieldOutcome] = {
            f.key: FieldOutcome(f.key, f.value, f.status) for f in state.collected
        }

        # ---- 3-5. extraction, with a second pass if the method just appeared ----
        method_key = self._resolved_method(schema, current)
        specs = schema.fields_for(method_key)
        current = await self._extract_and_merge(state.latest_message, specs, current)

        rediscovered = self._resolved_method(schema, current)
        if rediscovered != method_key:
            method_key = rediscovered
            specs = schema.fields_for(method_key)
            current = await self._extract_and_merge(state.latest_message, specs, current)
        outcome.method_key = method_key

        # ---- 6. concise description, kept fresh every turn ----
        transcript = state.inbound_transcript or [state.latest_message]
        summary = (await self._ai.summarize(transcript)).strip()

        # ---- 7. open schema ("other"): description-sufficiency gate ----
        if schema.open_schema:
            if len(summary.split()) >= schema.min_description_words:
                current[_PROBLEM_DESCRIPTION_KEY] = FieldOutcome(
                    _PROBLEM_DESCRIPTION_KEY, summary, FieldStatus.VALIDATED, changed=True
                )
                outcome.concise_description = summary
            else:
                return await self._clarify_vague(state, schema, current, summary, outcome)
        else:
            outcome.concise_description = summary or None

        # ---- 8. deterministic missing / invalid computation ----
        specs_now = schema.fields_for(method_key)
        outcome.fields = list(current.values())
        missing, invalid = self._classify_fields(specs_now, current)
        outcome.missing_fields = missing
        outcome.invalid_fields = invalid

        # ---- 9. transition ----
        if not missing and not invalid:
            outcome.is_complete = True
            outcome.next_status = ConversationStatus.VALIDATING
            outcome.reply = await self._ai.compose_reply(
                ReplyRequest(
                    kind=ReplyKind.ACKNOWLEDGE,
                    complaint_label=schema.label,
                    customer_name=state.customer_name,
                )
            )
            return outcome

        outcome.next_status = ConversationStatus.COLLECTING_INFO
        outcome.reply = await self._ai.compose_reply(
            ReplyRequest(
                kind=ReplyKind.ASK,
                complaint_label=schema.label,
                customer_name=state.customer_name,
                missing_fields=missing,
                invalid_fields=[
                    InvalidField(
                        key=s.key,
                        label=s.label,
                        error=(current[s.key].validation_error or "Please check this value."),
                    )
                    for s in invalid
                ],
            )
        )
        return outcome

    # ------------------------------------------------------------------ helpers

    def _resolved_method(
        self, schema: ComplaintSchema, current: dict[str, FieldOutcome]
    ) -> str | None:
        if not schema.methods or schema.method_field is None:
            return None
        fo = current.get(schema.method_field.key)
        return fo.value if (fo and fo.is_present) else None

    async def _extract_and_merge(
        self,
        message: str,
        specs: list[FieldSpec],
        current: dict[str, FieldOutcome],
    ) -> dict[str, FieldOutcome]:
        known = {k: fo.value for k, fo in current.items() if fo.is_present and fo.value}
        extraction = await self._ai.extract(message, specs, known)
        spec_by_key = {s.key: s for s in specs}
        result = dict(current)

        for ef in extraction.fields:
            spec = spec_by_key.get(ef.key)
            if spec is None:
                continue  # provider returned an unknown key - ignore it
            raw = (ef.value or "").strip()
            if not raw:
                continue  # never clear a field with empty extraction

            vr = validate_field(spec, raw)
            new_status = FieldStatus.VALIDATED if vr.ok else FieldStatus.INVALID
            new_value = vr.normalized_value if (vr.ok and vr.normalized_value) else raw
            existing = result.get(ef.key)

            if existing is None or existing.status == FieldStatus.INVALID:
                result[ef.key] = FieldOutcome(
                    ef.key, new_value, new_status, vr.error, ef.confidence, changed=True
                )
                continue

            if existing.is_present:
                if _norm(new_value) == _norm(existing.value):
                    continue  # same value restated - no change
                if new_status == FieldStatus.VALIDATED:
                    # a genuine correction: the most recent explicit value wins
                    result[ef.key] = FieldOutcome(
                        ef.key, new_value, new_status, None, ef.confidence, changed=True
                    )
                # else: new value is invalid - keep the existing valid one
                continue

            result[ef.key] = FieldOutcome(
                ef.key, new_value, new_status, vr.error, ef.confidence, changed=True
            )

        return result

    @staticmethod
    def _classify_fields(
        specs: list[FieldSpec], current: dict[str, FieldOutcome]
    ) -> tuple[list[FieldSpec], list[FieldSpec]]:
        missing: list[FieldSpec] = []
        invalid: list[FieldSpec] = []
        for spec in specs:
            fo = current.get(spec.key)
            if fo is not None and fo.status == FieldStatus.INVALID:
                invalid.append(spec)
            elif spec.required and (fo is None or not fo.is_present):
                missing.append(spec)
        return missing, invalid

    async def _clarify_type(
        self, state: ConversationState, outcome: EngineOutcome
    ) -> EngineOutcome:
        outcome.complaint_type = None
        outcome.awaiting_clarification = True
        outcome.next_status = ConversationStatus.OPEN
        outcome.fields = [FieldOutcome(f.key, f.value, f.status) for f in state.collected]
        outcome.reply = await self._ai.compose_reply(
            ReplyRequest(
                kind=ReplyKind.CLARIFY,
                complaint_label="your issue",
                customer_name=state.customer_name,
                guidance=(
                    "Ask whether the problem concerns a withdrawal, a deposit, or another "
                    "issue, and what went wrong."
                ),
            )
        )
        return outcome

    async def _clarify_vague(
        self,
        state: ConversationState,
        schema: ComplaintSchema,
        current: dict[str, FieldOutcome],
        summary: str,
        outcome: EngineOutcome,
    ) -> EngineOutcome:
        outcome.concise_description = summary or None
        outcome.fields = list(current.values())
        outcome.missing_fields = [
            s for s in schema.fields_for() if s.key == _PROBLEM_DESCRIPTION_KEY
        ]
        outcome.awaiting_clarification = True
        outcome.next_status = ConversationStatus.COLLECTING_INFO
        outcome.reply = await self._ai.compose_reply(
            ReplyRequest(
                kind=ReplyKind.CLARIFY,
                complaint_label=schema.label,
                customer_name=state.customer_name,
                guidance=(
                    "Ask the customer to describe what the problem is, what they were "
                    "trying to do, and what went wrong."
                ),
            )
        )
        return outcome


def _norm(value: str | None) -> str:
    return (value or "").strip().lower()

"""The AI Conversation Engine.

Responsibilities (see PROJECT GOAL 1-11):
- classify the complaint type
- extract information from each message
- track which required fields are present vs missing
- ask only for missing fields
- validate collected information
- decide when the complaint is ready to become a ticket

It depends only on :class:`AIProvider` and :class:`ComplaintSchemaRegistry`.
It performs no I/O and holds no state - callers pass a :class:`ConversationState`
and persist the returned :class:`EngineOutcome`.
"""

from __future__ import annotations

from app.ai.base import AIProvider, TypeOption
from app.conversation.state import ConversationState, EngineOutcome
from app.domain.complaint_schemas.registry import ComplaintSchemaRegistry
from app.domain.complaint_schemas.spec import ComplaintSchema, FieldSpec
from app.domain.enums import ConversationStatus
from app.domain.validation import validate_fields


class ConversationEngine:
    def __init__(self, ai: AIProvider, registry: ComplaintSchemaRegistry) -> None:
        self._ai = ai
        self._registry = registry

    async def advance(self, state: ConversationState) -> EngineOutcome:
        outcome = EngineOutcome(
            complaint_type=state.complaint_type,
            method_key=state.method_key,
        )

        # 1-2. Classify if we do not yet know the complaint type.
        if not outcome.complaint_type:
            options = [
                TypeOption(type=s.type, label=s.label, description=s.description)
                for s in self._registry.all()
            ]
            classification = await self._ai.classify(state.latest_message, options)
            outcome.classification = classification
            outcome.complaint_type = classification.type

        schema = self._registry.try_get(outcome.complaint_type)
        if schema is None:
            # Could not classify yet - ask the customer to clarify.
            outcome.next_status = ConversationStatus.OPEN
            outcome.reply = await self._ai.compose_reply(
                [],
                complaint_label="your issue",
                customer_name=state.customer_name,
                extra_context=(
                    "Could you tell us whether this is about a withdrawal, a deposit, "
                    "or something else, and briefly what went wrong?"
                ),
            )
            return outcome

        # 3. Determine the field set for this complaint (+ method).
        needs_method = bool(schema.methods) and not outcome.method_key
        specs = schema.fields_for(outcome.method_key)

        # 4. Extract whatever is present in the latest message.
        extraction = await self._ai.extract(state.latest_message, specs, known=state.collected)
        outcome.newly_extracted = extraction.fields

        merged = dict(state.collected)
        for f in extraction.fields:
            merged.setdefault(f.key, f.value)  # never overwrite an existing value

        # 5-6. Validate and compute what is still missing.
        results = validate_fields(specs, merged)
        outcome.validation_errors = [r for r in results if not r.ok and _is_required(specs, r.key)]

        valid_keys = {r.key for r in results if r.ok and r.normalized_value is not None}
        outcome.missing_fields = [
            spec
            for spec in specs
            if spec.required and spec.key not in valid_keys
        ]

        # Concise description for "other" (open schema) or whenever we have text.
        outcome.concise_description = await self._maybe_summarize(schema, state, merged)

        # Decide next step.
        if needs_method:
            outcome.needs_method_selection = True
            outcome.next_status = ConversationStatus.COLLECTING_INFO
            outcome.reply = await self._ai.compose_reply(
                [],
                complaint_label=schema.label,
                customer_name=state.customer_name,
                extra_context=schema.method_selector_label or "Which method did you use?",
            )
            return outcome

        if not outcome.missing_fields and not outcome.validation_errors:
            # 9-11. All required info collected and valid.
            outcome.is_complete = True
            outcome.next_status = ConversationStatus.VALIDATING
            outcome.reply = await self._ai.compose_reply(
                [], complaint_label=schema.label, customer_name=state.customer_name
            )
            return outcome

        # 7-8. Still collecting - ask only for missing / invalid fields.
        ask_for = list(outcome.missing_fields)
        invalid_keys = {r.key for r in outcome.validation_errors}
        ask_for.extend(s for s in specs if s.key in invalid_keys and s not in ask_for)

        outcome.next_status = ConversationStatus.COLLECTING_INFO
        outcome.reply = await self._ai.compose_reply(
            ask_for,
            complaint_label=schema.label,
            customer_name=state.customer_name,
            extra_context=_format_validation_hints(outcome.validation_errors),
        )
        return outcome

    async def _maybe_summarize(
        self, schema: ComplaintSchema, state: ConversationState, merged: dict[str, str]
    ) -> str | None:
        if schema.open_schema or "problem_description" in merged:
            transcript = state.inbound_transcript or [state.latest_message]
            return await self._ai.summarize(transcript)
        return None


def _is_required(specs: list[FieldSpec], key: str) -> bool:
    return any(s.key == key and s.required for s in specs)


def _format_validation_hints(errors: list) -> str:
    if not errors:
        return ""
    lines = [f"- {e.key}: {e.error}" for e in errors]
    return "Some details need correcting:\n" + "\n".join(lines)

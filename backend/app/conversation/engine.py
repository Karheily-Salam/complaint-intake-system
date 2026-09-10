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
- the complaint becomes ready/ticketable exactly when the deterministic schema
  requirements are satisfied

The engine never branches on any field value (e.g. the deposit method). It asks
the schema for the required fields and collects them; a field like
``deposit_method`` is just free text the customer provided.

It depends only on :class:`AIProvider` and :class:`ComplaintSchemaRegistry` and
performs no I/O.
"""

from __future__ import annotations

from app.ai.base import (
    AIProvider,
    CollectedFieldView,
    InvalidField,
    ReplyDraft,
    ReplyKind,
    ReplyRequest,
    TypeOption,
)
from app.conversation.state import ConversationState, EngineOutcome, FieldOutcome
from app.core.config import settings
from app.domain.complaint_schemas.registry import ComplaintSchemaRegistry
from app.domain.complaint_schemas.spec import ComplaintSchema, FieldSpec, FieldType
from app.domain.enums import ConversationStatus, FieldStatus
from app.domain.validation import validate_field

_PROBLEM_DESCRIPTION_KEY = "problem_description"
# Purely for denormalisation onto the ticket / UI - the engine does not branch on it.
_DEPOSIT_METHOD_KEY = "deposit_method"
_DEFAULT_LANGUAGE = "en"


class ConversationEngine:
    def __init__(self, ai: AIProvider, registry: ComplaintSchemaRegistry) -> None:
        self._ai = ai
        self._registry = registry

    async def advance(self, state: ConversationState) -> EngineOutcome:
        outcome = EngineOutcome(complaint_type=state.complaint_type)
        outcome.language_code = await self._resolve_language(state)

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

        # ---- 3. extraction against this complaint's (flat) field set ----
        specs = schema.fields_for()
        current = await self._extract_and_merge(
            state.latest_message, specs, current, state.pending_field
        )

        # deposit_method (if the schema has one) is captured verbatim - the engine
        # does not interpret it or let it change the required field set.
        method_fo = current.get(_DEPOSIT_METHOD_KEY)
        outcome.method_key = method_fo.value if (method_fo and method_fo.is_present) else None

        # ---- 4. concise description, kept fresh every turn ----
        transcript = state.inbound_transcript or [state.latest_message]
        summary = (await self._ai.summarize(transcript)).strip()

        # ---- 5. open schema ("other"): description-sufficiency gate ----
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

        # ---- 6. deterministic missing / invalid computation ----
        outcome.fields = list(current.values())
        missing, invalid, next_unresolved = self._classify_fields(specs, current)
        outcome.missing_fields = missing
        outcome.invalid_fields = invalid

        # ---- 7. transition ----
        if next_unresolved is None:
            outcome.is_complete = True
            outcome.next_status = ConversationStatus.VALIDATING
            # No reply is composed here: the ticket (and its reference) does
            # not exist yet - creating it is a DB side effect owned by
            # IntakeService/TicketService, and this engine performs no I/O.
            # IntakeService calls compose_ticket_confirmation() below once
            # ticket creation has actually succeeded, so the confirmation can
            # include the real reference instead of omitting or inventing it.
            return outcome

        # The single field the *next* inbound message should be interpreted
        # as primarily answering: the first field in schema order that is
        # not yet resolved - invalid or missing, whichever comes first (see
        # _classify_fields). An invalid answer to the field the customer was
        # just asked about must keep the conversation on that same field
        # rather than jumping ahead to the next merely-missing one, so this
        # is deliberately NOT just `missing[0]`.
        outcome.pending_field = next_unresolved.key
        next_fo = current[next_unresolved.key] if next_unresolved.key in current else None
        is_invalid = next_fo is not None and next_fo.status == FieldStatus.INVALID

        outcome.next_status = ConversationStatus.COLLECTING_INFO
        outcome.reply = await self._ai.compose_reply(
            ReplyRequest(
                kind=ReplyKind.ASK,
                complaint_label=schema.label,
                customer_name=state.customer_name,
                language_code=outcome.language_code,
                # Ask about exactly this one field per message - a
                # correction if it's invalid, otherwise a plain request.
                # `outcome.missing_fields` / `outcome.invalid_fields` above
                # still report the *full* remaining sets for the API/UI;
                # this is only what the customer-facing reply may mention.
                missing_fields=[] if is_invalid else [next_unresolved],
                invalid_fields=(
                    [
                        InvalidField(
                            key=next_unresolved.key,
                            label=next_unresolved.label,
                            error=next_fo.validation_error or "Please check this value.",
                            example=next_unresolved.example,
                        )
                    ]
                    if is_invalid
                    else []
                ),
            )
        )
        return outcome

    async def compose_ticket_confirmation(
        self, outcome: EngineOutcome, customer_name: str | None, ticket_reference: str
    ) -> ReplyDraft:
        """Compose the final ACKNOWLEDGE reply once ticket creation has succeeded.

        Called by IntakeService, never from within :meth:`advance`, because
        the real ``ticket_reference`` only exists after the caller has
        actually created the ticket. The AI provider only phrases the given
        reference and collected values into natural text - it never
        generates/invents the reference (see ``ReplyRequest.ticket_reference``)
        and never decides which fields were collected (see
        ``collected_fields`` below).
        """
        schema = self._registry.get(outcome.complaint_type)
        fields_by_key = {fo.key: fo for fo in outcome.fields}
        collected: list[CollectedFieldView] = []
        for spec in schema.fields_for():
            # Free-text narrative (problem_description) is deliberately left
            # out of the structured summary - it's the customer's own words,
            # often a full paragraph, not a concise fact to list; the engine
            # excludes it by type, not by singling out that one field key.
            if spec.type == FieldType.TEXT:
                continue
            fo = fields_by_key.get(spec.key)
            if fo is not None and fo.is_present and fo.value:
                collected.append(CollectedFieldView(key=spec.key, label=spec.label, value=fo.value))

        return await self._ai.compose_reply(
            ReplyRequest(
                kind=ReplyKind.ACKNOWLEDGE,
                complaint_label=schema.label,
                customer_name=customer_name,
                language_code=outcome.language_code,
                ticket_reference=ticket_reference,
                collected_fields=collected,
            )
        )

    # ------------------------------------------------------------------ helpers

    async def _resolve_language(self, state: ConversationState) -> str:
        """Decide which language this turn's reply should be written in.

        Policy: prefer the language of the customer's latest message; if that
        message carries no reliable language signal (e.g. it is only numbers
        or a stray word), keep the conversation's previously known language
        instead of guessing. A brand new conversation with no signal falls
        back to English. The resolved value is always what gets persisted as
        the conversation's language, so it remains the fallback for future
        ambiguous turns.
        """
        detected = await self._ai.detect_language(state.latest_message)
        if detected.code and detected.confidence >= settings.min_language_confidence:
            return detected.code
        return state.language_code or _DEFAULT_LANGUAGE

    async def _extract_and_merge(
        self,
        message: str,
        specs: list[FieldSpec],
        current: dict[str, FieldOutcome],
        pending_field: str | None,
    ) -> dict[str, FieldOutcome]:
        known = {k: fo.value for k, fo in current.items() if fo.is_present and fo.value}
        extraction = await self._ai.extract(message, specs, known, pending_field=pending_field)
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
    ) -> tuple[list[FieldSpec], list[FieldSpec], FieldSpec | None]:
        """Deterministic per-turn field state.

        Returns the full ``missing`` and ``invalid`` lists (unchanged - the
        API/UI still reports every remaining field), plus ``next_unresolved``:
        the single field, in schema order, that is either invalid or missing
        - whichever comes first. A field the customer just answered invalidly
        therefore always takes priority over a later merely-missing one, so
        the conversation never advances past a value it has already rejected.
        """
        missing: list[FieldSpec] = []
        invalid: list[FieldSpec] = []
        next_unresolved: FieldSpec | None = None
        for spec in specs:
            fo = current.get(spec.key)
            if fo is not None and fo.status == FieldStatus.INVALID:
                invalid.append(spec)
                if next_unresolved is None:
                    next_unresolved = spec
            elif spec.required and (fo is None or not fo.is_present):
                missing.append(spec)
                if next_unresolved is None:
                    next_unresolved = spec
        return missing, invalid, next_unresolved

    async def _clarify_type(
        self, state: ConversationState, outcome: EngineOutcome
    ) -> EngineOutcome:
        outcome.complaint_type = None
        outcome.awaiting_clarification = True
        outcome.next_status = ConversationStatus.OPEN
        outcome.pending_field = None  # asking about the complaint type, not a schema field
        outcome.fields = [FieldOutcome(f.key, f.value, f.status) for f in state.collected]
        outcome.reply = await self._ai.compose_reply(
            ReplyRequest(
                kind=ReplyKind.CLARIFY,
                complaint_label="your issue",
                customer_name=state.customer_name,
                language_code=outcome.language_code,
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
        outcome.pending_field = _PROBLEM_DESCRIPTION_KEY
        outcome.awaiting_clarification = True
        outcome.next_status = ConversationStatus.COLLECTING_INFO
        outcome.reply = await self._ai.compose_reply(
            ReplyRequest(
                kind=ReplyKind.CLARIFY,
                complaint_label=schema.label,
                customer_name=state.customer_name,
                language_code=outcome.language_code,
                guidance=(
                    "Ask the customer to describe what the problem is, what they were "
                    "trying to do, and what went wrong."
                ),
            )
        )
        return outcome


def _norm(value: str | None) -> str:
    return (value or "").strip().lower()

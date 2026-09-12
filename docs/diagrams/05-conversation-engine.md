# Conversation engine turn

**Scope.** What happens inside `ConversationEngine.advance()` for one customer
message. No persistence, no email, no network: the engine is pure.

**Read it** when changing classification, extraction, validation, or the rule
that only one field is asked for per message.

Sources: `app/conversation/engine.py`, `app/conversation/state.py`,
`app/domain/validation.py`, `app/domain/evidence.py`,
`app/domain/complaint_schemas/spec.py`.

```mermaid
flowchart TD
    IN["ConversationState<br/>latest message with quoted history stripped,<br/>collected fields, pending_field, language"]
    LANG["detect_language<br/>below MIN_LANGUAGE_CONFIDENCE keeps<br/>the thread's known language"]
    KNOWN{"Complaint type<br/>already known?"}
    CLASSIFY["classify against the schema registry options"]
    CONF{"Confident enough?<br/>MIN_CLASSIFICATION_CONFIDENCE"}
    CLARIFYTYPE["Reply asks what the problem is about<br/>status open, no pending field"]

    SCHEMA["Load schema, read fields_for()"]
    EXTRACT["extract against this schema's fields<br/>pending_field passed for bare answers"]

    subgraph merge["Merge, per extracted value"]
        VALIDATE["validate_field"]
        EVIDENCE{"locate_evidence finds a span<br/>in the customer's own message?"}
        REJECT["Rejected, recorded in the audit<br/>cannot overwrite anything"]
        MERGE["Merge rules:<br/>never clear on empty extraction,<br/>same value restated is not a change,<br/>a valid value may correct an earlier one,<br/>an invalid value never replaces a valid one"]
    end

    SUMMARY["summarize the inbound transcript"]
    OPEN{"Open schema, as in 'other'?"}
    ENOUGH{"Summary reaches<br/>min_description_words?"}
    CLARIFYVAGUE["Reply asks for a fuller description<br/>pending field problem_description"]

    CLASSIFYFIELDS["Compute missing and invalid in schema order,<br/>pick the first unresolved field"]
    DONE{"Anything unresolved?"}
    COMPLETE["is_complete, status validating<br/>no reply composed here:<br/>the ticket reference does not exist yet"]
    ASK["compose_reply for exactly one field:<br/>a correction if it is invalid,<br/>otherwise a plain request"]

    IN --> LANG --> KNOWN
    KNOWN -- yes --> SCHEMA
    KNOWN -- no --> CLASSIFY --> CONF
    CONF -- no --> CLARIFYTYPE
    CONF -- yes --> SCHEMA
    SCHEMA --> EXTRACT --> VALIDATE --> EVIDENCE
    EVIDENCE -- no --> REJECT
    EVIDENCE -- yes --> MERGE
    REJECT --> SUMMARY
    MERGE --> SUMMARY
    SUMMARY --> OPEN
    OPEN -- yes --> ENOUGH
    ENOUGH -- no --> CLARIFYVAGUE
    ENOUGH -- yes --> CLASSIFYFIELDS
    OPEN -- no --> CLASSIFYFIELDS
    CLASSIFYFIELDS --> DONE
    DONE -- no --> COMPLETE
    DONE -- yes --> ASK
```

Properties the shape enforces:

- The engine never branches on a field's value. It asks the schema which fields
  are required and collects them, so the deposit method is free text that
  unlocks nothing.
- An invalid answer outranks a later merely-missing field, so the conversation
  never advances past a value it has already rejected.
- The evidence gate applies to whatever provider is configured, including a
  future language model. `EXTRACTION_REQUIRE_EVIDENCE` controls it.
- Completion composes no reply. `IntakeService` calls
  `compose_ticket_confirmation` after the ticket actually exists, so the
  reference is real rather than invented.

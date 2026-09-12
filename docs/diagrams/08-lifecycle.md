# Conversation, complaint and ticket lifecycle

**Scope.** The three status enums, what moves each one, and how they interact
when support closes a ticket.

**Read it** when changing status handling, or to see why a reply to a closed
thread starts something new.

Sources: `app/domain/enums.py`, `app/services/intake_service.py`,
`app/services/ticket_service.py`, `app/api/routes/tickets.py`.

## Conversation

Re-derived every turn: `_run_turn` assigns whatever the engine's outcome says,
and `_finalize` overrides it with `completed` once a ticket exists. The path
below is the ordinary one rather than a restriction the code enforces.

```mermaid
stateDiagram-v2
    [*] --> open: first email, type not yet known
    open --> open: still too vague to classify
    open --> collecting_info: type decided, fields outstanding
    collecting_info --> collecting_info: one field asked per message
    collecting_info --> validating: every required field valid
    validating --> completed: ticket created
    completed --> completed: later reply, no collected field changed
    completed --> [*]

    note right of validating
        No reply is composed here.
        IntakeService creates the ticket first,
        so the confirmation carries a real reference.
    end note
```

`abandoned` exists in `ConversationStatus` and no code path sets it.

## Complaint

Written in two places only: `IntakeService._run_turn` sets `collecting` and
`ready`, and `TicketService.create_for_complaint` sets `ticketed`.

```mermaid
stateDiagram-v2
    [*] --> draft: model default, created with the conversation
    draft --> collecting: type known, fields outstanding
    collecting --> ready: schema requirements satisfied
    ready --> ticketed: ticket created
    ticketed --> ready: a later turn is complete again
    ticketed --> [*]

    note right of ticketed
        Set only when the ticket row is created.
        refresh_snapshot does not set it again,
        so a later complete turn leaves the
        complaint at ready while its ticket stands.
    end note
```

## Ticket

Staff owned. `PATCH /tickets/{reference}` is the only writer, and it validates
the value against `TicketStatus` without restricting which transition is
allowed, so any status can follow any other.

```mermaid
stateDiagram-v2
    [*] --> new: created when the complaint is complete

    state "any status, set directly by staff" as pool {
        new
        in_progress
        resolved
        closed
    }

    closed --> [*]

    note right of pool
        The service checks membership of the enum,
        not the path taken, so these four form a
        fully connected set rather than a pipeline.
        new is the only one the system itself sets.
    end note
```

`closed` is terminal for threading rather than for editing: a later customer
email is refused by `_is_threadable` and starts a new conversation with its own
ticket, and the closed ticket is never appended to. Staff can still move the
status afterwards.

How they meet:

- The ticket reference comes from the database's own autoincrementing key, so
  it is unique by construction and no model can invent it.
- A later reply to a completed conversation produces a new confirmation only if
  the engine actually changed a collected field that turn. Support is notified
  once, when the ticket is created.
- Staff owned fields, status and priority, are never overwritten when the
  ticket's denormalised snapshot is refreshed after a correction.

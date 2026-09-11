🇬🇧 **English** | [🇷🇺 Русский](005-commit-before-acknowledge.ru.md)

# ADR-005 — Commit state before acknowledging inbound mail

**Status:** accepted

## Context

IMAP delivers at least once. Something must decide when a message is "done",
and the ordering of that decision determines what a crash costs.

Two orderings are possible:

1. Mark the message `\Seen`, then process it.
2. Process and commit, then mark it `\Seen`.

## Decision

Order 2. A message is acknowledged to the mail server only after its database
transaction has committed. The reply is sent inside that transaction.

## Alternatives considered

- **Acknowledge first.** Simpler, and wrong: a crash between acknowledging and
  committing loses the complaint permanently. The mail server considers it
  delivered and nothing exists.
- **Two-phase commit across mailbox and database.** Not available over IMAP, and
  vastly disproportionate.

## Consequences

- The failure mode is duplicate *work*, never lost work — the correct trade for
  customer complaints.
- Duplicates must therefore be harmless, which requires idempotency (ADR-007).
- Sending the reply inside the transaction means a provider failure rolls the
  whole turn back, so the system never advances a conversation without the
  customer being asked. The cost is that a crash after sending but before
  committing can repeat a question; a repeated question is recoverable, a
  silently stalled conversation is not.
- A test asserts the ordering directly, by checking from an independent
  connection that the row is visible at the moment of acknowledgement.

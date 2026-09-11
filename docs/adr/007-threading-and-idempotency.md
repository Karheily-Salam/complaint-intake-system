🇬🇧 **English** | [🇷🇺 Русский](007-threading-and-idempotency.ru.md)

# ADR-007 — Email threading and idempotency strategy

**Status:** accepted

## Context

Two hard problems follow from using email as the interface:

1. **Which conversation does this reply belong to?** One customer may have
   several complaints open at once, so the sender's address cannot answer it.
2. **Has this email already been processed?** IMAP delivers at least once, and
   ADR-005 makes duplicates expected rather than exceptional.

## Decision

**Threading, in order of preference:**

1. `In-Reply-To` matched against the `Message-ID` of a message we sent.
2. The `References` chain, newest first.
3. An opaque `[Ref:…]` token carried in the subject line.

**Idempotency:** the inbound `Message-ID` is stored on the message row under a
unique index. A redelivered email is recognised and skipped before anything is
created.

## Alternatives considered

- **Sender address alone.** Rejected: breaks the moment a customer has two open
  complaints, which is exactly when getting it wrong is most damaging.
- **Subject-line matching only.** Rejected as a primary mechanism: clients
  rewrite subjects, and "Re: Re: Fwd:" is not an identifier. Kept as the last
  fallback because some providers rewrite Message-IDs in transit, which would
  otherwise break the chain silently.
- **A token in the reply body.** Visible, ugly, and customers trim quoted text.
- **Application-level dedupe table.** Equivalent to the unique index, with more
  moving parts.

## Consequences

- Threading survives a provider that rewrites headers, thanks to the third
  layer.
- The subject token is opaque and random rather than the row id, so it exposes
  nothing internal or enumerable.
- The unique index makes duplicate suppression a database guarantee rather than
  application logic, so it holds even under concurrency.
- Every value involved is attacker-controlled, so headers are sanitised and
  bounded at the model boundary — a subject containing a newline once made
  sending fail permanently and the message retry forever.

# ADR-004 — IMAP polling rather than webhooks or a mail API

**Status:** accepted

## Context

The system has to receive mail. The options were an inbound-webhook provider
(Mailgun, SendGrid, Postmark), a mailbox API (Gmail API, Microsoft Graph), or
plain IMAP.

At the time of the decision there was no domain, no TLS certificate, and one
small VPS.

## Decision

Poll an IMAP mailbox on an interval, send over SMTP, both behind the
`EmailProvider` interface. Implemented with the standard library only.

## Alternatives considered

- **Inbound webhook provider.** Requires owning a domain and pointing its MX
  records at the provider, plus a public HTTPS endpoint. Neither existed, so
  this was not merely less convenient — it was unavailable.
- **Gmail API / Microsoft Graph.** OAuth app registration and consent screens
  for a single mailbox. Microsoft has also disabled basic auth for personal
  accounts, so this is the *only* option there, but it is disproportionate here.
- **Running a mail server.** Rejected outright: deliverability, spam and
  security work far exceeding the value.

## Consequences

- Works with any mailbox and an app password; no domain required.
- Polling is an outbound connection, so no inbound port and no firewall change.
- Delivery latency is up to one poll interval (~60s), which is irrelevant for
  complaint intake.
- The mailbox effectively acts as the inbound queue, with at-least-once
  delivery — which is why idempotency matters (see ADR-007).
- Blocking libraries on an async event loop needed care: provider calls run in
  a worker thread and carry explicit socket timeouts, because a blackholed
  connection would otherwise hang the whole application.

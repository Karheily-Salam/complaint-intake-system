🇬🇧 **English** | [🇷🇺 Русский](001-email-only-intake.ru.md)

# ADR-001: Email-only intake instead of a web form

**Status:** accepted

## Context

Complaint intake is usually either fully manual (someone reads each email and
chases the customer for the details they left out) or replaced with a web form
that customers abandon halfway through. A form also assumes the customer knows
which fields matter, which is the part they are worst at.

Customers already write in by email, unprompted, in prose, with some details
present and others missing.

## Decision

The mailbox is the entire customer interface. There is no customer-facing form,
portal, login, or link. A customer sends an ordinary email; the system replies
in the same thread until it has what it needs.

## Alternatives considered

- **Web form.** Higher structure, worse completion, and it discards the channel
  customers already use. It also solves the easy half of the problem (capturing
  fields) while ignoring the hard half (a complaint arriving incomplete and in
  fragments).
- **Form link emailed back to the customer.** Keeps email as the entry point but
  reintroduces the abandonment problem at the worst moment.
- **Chat widget.** Requires the customer to be on the site and present; a
  complaint is often written after the fact.

## Consequences

- The interesting engineering moves into the conversation: partial information,
  arbitrary ordering, corrections, several languages, and threading.
- Validation must be conversational rather than a red field label.
- No customer input can be trusted for identity: email sender addresses are
  spoofable, which shapes the security model (see ADR-008).
- Demonstrating the product needs a mail client, not a form, which is why the
  demo is built the way it is.

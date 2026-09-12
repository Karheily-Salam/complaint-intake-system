🇬🇧 **English** | [🇷🇺 Русский](008-demo-isolation.ru.md)

# ADR-008: Public demo isolated from real customer data

**Status:** accepted

## Context

The project needs a public demo a recruiter can use without credentials, and it
also handles real complaints containing personal data: addresses, account
identifiers, and the customer's own words.

An audit found the first version serving all of it unauthenticated at
enumerable integer ids. A later review found a subtler version of the same
problem: the demo intake endpoint accepts an unverified sender address, so
claiming a real customer's address let public input modify that customer's
record.

## Decision

Two surfaces, separated in the database rather than in the UI:

- **Public**: `POST /inbox` and `/demo/*`. Every read is filtered to
  conversations flagged `is_demo` *in the query*.
- **Staff**: `/tickets`, `/conversations`, `/ops/*`. Requires `STAFF_API_KEY`,
  compared in constant time, with no default: unset means `503`, never open.

Customer identity is `(email, is_demo)`, so demo and real customers are
separate rows even when they share an address.

## Alternatives considered

- **Hide staff data in the frontend.** Not a boundary at all; the API would
  still serve it.
- **Ship an API key in the browser bundle.** A key in JavaScript is not a
  secret. The demo therefore reads demo endpoints and ticket status is
  read-only.
- **Take the demo down.** It is the most persuasive thing in the project; the
  answer was to make it safe, not to remove it.
- **User accounts and roles.** One operator. Disproportionate.

## Consequences

- A real conversation cannot be *loaded* through the public API with any id or
  reference, verified by probing a live instance with real and demo records
  side by side.
- Demo data is genuinely disposable: `scripts/seed_demo.py --reset` deletes
  demo-scoped rows only, which is tested.
- A route contract test fails the build if a new endpoint is not explicitly
  classified as public or staff, so the boundary cannot erode by omission.
- Anything typed into the public demo is stored and publicly visible by design;
  the README says so.

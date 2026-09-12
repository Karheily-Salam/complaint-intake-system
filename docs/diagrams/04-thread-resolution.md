# Thread resolution and idempotency

**Scope.** How an inbound email is matched to an existing conversation, and
what disqualifies a match. Covers the idempotency check that runs first.

**Read it** before touching threading, the demo boundary, or closed-ticket
behaviour. Related decision record: [ADR-007](../adr/007-threading-and-idempotency.md).

Sources: `app/services/intake_service.py` (`handle_inbound_email`,
`_resolve_conversation`, `_is_threadable`), `app/services/email_threading.py`,
`app/db/models/message.py`, `app/db/models/conversation.py`.

```mermaid
flowchart TD
    START["Inbound email"]
    IDEM{"Message-ID already in<br/>messages.external_message_id?"}
    SKIP["Return None<br/>caller still acknowledges it"]
    CUST["get_or_create customer<br/>identity is email plus is_demo"]

    IRT{"In-Reply-To matches a<br/>Message-ID we sent?"}
    REFS{"Any References entry,<br/>newest first, matches?"}
    TOKEN{"Subject carries a<br/>[Ref:token] we issued?"}

    CHECK1["_is_threadable"]
    CHECK2["_is_threadable"]
    CHECK3["_is_threadable"]

    subgraph gate["_is_threadable, one gate for every signal"]
        G1{"Conversation exists and<br/>belongs to this customer?"}
        G2{"Conversation is_demo?"}
        G3{"Its ticket status is closed?"}
        OK["Threadable"]
        NO["Not threadable"]
    end

    CONTINUE["Continue that conversation"]
    NEWCONV["Create a new conversation<br/>subject stripped of any inherited token<br/>same customer, new thread_token"]

    START --> IDEM
    IDEM -- yes --> SKIP
    IDEM -- no --> CUST --> IRT
    IRT -- match --> CHECK1
    IRT -- no match --> REFS
    REFS -- match --> CHECK2
    REFS -- no match --> TOKEN
    TOKEN -- match --> CHECK3
    TOKEN -- no match --> NEWCONV

    CHECK1 --> G1
    CHECK2 --> G1
    CHECK3 --> G1

    G1 -- no --> NO
    G1 -- yes --> G2
    G2 -- yes --> NO
    G2 -- no --> G3
    G3 -- yes --> NO
    G3 -- no --> OK

    OK --> CONTINUE
    NO --> NEWCONV
```

Why it is shaped this way, from the code and its comments:

- The sender address is never a threading signal on its own. One customer may
  have several complaints open at once.
- Every signal passes through the same gate, so the rules hold however the
  match was made rather than depending on each call site.
- Real email never joins a demo conversation, and the public demo endpoint
  refuses to continue a non-demo one, so the two sets stay separate in both
  directions.
- A closed ticket is never reopened. The lifecycle outranks the email headers,
  and the customer keeps their identity while the thread starts fresh.

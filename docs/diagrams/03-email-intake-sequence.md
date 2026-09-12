# Email intake sequence

**Scope.** One inbound customer email, from the IMAP notification to the point
where the message is acknowledged on the server. Ordering is the subject here.

**Read it** when changing anything in the poller or in `IntakeService`, or when
reasoning about crash safety and retries.

Sources: `app/services/email_poller.py`, `app/services/intake_service.py`,
`app/email/idle.py`, `app/email/providers/imap_smtp.py`,
`app/services/ticket_service.py`.

```mermaid
sequenceDiagram
    autonumber
    participant M as Mailbox server
    participant EP as ImapSmtpEmailProvider
    participant P as EmailPoller
    participant IS as IntakeService
    participant CE as ConversationEngine
    participant AI as AIProvider (hybrid)
    participant TS as TicketService
    participant DB as SQLite session

    M-->>EP: IDLE notification
    P->>EP: wait_for_activity(interval)
    EP-->>P: notified
    P->>EP: fetch_new()
    EP->>M: UID SEARCH UNSEEN, then FETCH
    EP-->>P: list of InboundEmail

    loop each email, independently
        P->>P: _loop_risk(inbound)
        alt self addressed or auto submitted
            P->>EP: mark_processed(message_id)
            Note over P,EP: skipped, not processed
        else genuine customer mail
            P->>IS: handle_inbound_email(inbound)
            IS->>DB: find_by_external_message_id(message_id)
            alt already processed
                IS-->>P: None
                P->>EP: mark_processed(message_id)
            else new message
                IS->>DB: get_or_create customer, resolve conversation
                IS->>DB: add inbound Message and EmailLog
                IS->>CE: advance(state)
                CE->>AI: detect_language, classify, extract, summarize
                AI-->>CE: results
                CE->>AI: compose_reply, unless complete
                CE-->>IS: EngineOutcome
                IS->>DB: persist ComplaintFields and ml_predictions
                opt outcome.is_complete
                    IS->>TS: create_for_complaint or refresh_snapshot
                    TS-->>IS: ticket with reference
                    IS->>CE: compose_ticket_confirmation(reference)
                    CE-->>IS: confirmation body
                end
                IS->>EP: send(reply)
                EP->>M: SMTP
                IS->>DB: add outbound Message and EmailLog
                opt new ticket
                    IS->>EP: send(ticket notification to support inbox)
                end
                IS->>DB: commit
                IS-->>P: IntakeResult
                P->>EP: mark_processed(message_id)
            end
        end
    end
```

The ordering that matters, all of it visible above:

- The reply is sent **inside** the transaction, before `commit`. A provider
  failure rolls the whole turn back, so the system never advances a
  conversation without the customer being asked.
- The message is acknowledged on the server **after** the commit. A crash in
  between leaves it unread, so the next poll retries it, and the idempotency
  check at the top absorbs the duplicate.
- Each email gets its own session, so one failure cannot affect another.
- A failure inside `handle_inbound_email` skips `mark_processed` entirely: the
  email stays in the mailbox.

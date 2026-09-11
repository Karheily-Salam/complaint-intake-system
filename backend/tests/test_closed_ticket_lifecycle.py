"""A closed ticket is finished, whatever the email headers say.

Support closing a ticket ends that matter. A later email from the same
customer is a new matter, even when their mail client threads it onto the old
conversation - so these tests push every threading signal the system honours
(In-Reply-To, the References chain, the subject token, the same subject line)
at a closed ticket and assert each one starts a fresh conversation instead of
reopening or appending to the closed one.

Two levels of assertion, deliberately:

* the *immediate* effect is on conversations - the new mail must land in a new
  thread and the closed one must not grow by a single message;
* the new ticket appears once that fresh conversation finishes collecting its
  fields, exactly like any first-time complaint, so the end-to-end tests drive
  it through the normal workflow rather than expecting a ticket on turn one.

The customer, however, is not new. Identity survives across tickets.
"""

from __future__ import annotations

import asyncio

import pytest

from app.core.database import SessionLocal
from app.db.models.conversation import Conversation
from app.db.models.customer import Customer
from app.db.models.ticket import Ticket
from app.email.factory import get_email_provider
from app.services.email_poller import EmailPoller

CUSTOMER = "lifecycle@example.com"

WITHDRAWAL = (
    "My withdrawal never arrived. user id U-482913, account email "
    "jane.doe@example.com, transaction TXN-9f3a12bc."
)
DEPOSIT = (
    "My deposit was not credited. user id U-900112, account email "
    "phase8.acct@example.com, sent from my Sberbank wallet on 2026-09-08, "
    "method: bank transfer."
)
FOLLOW_UP = "Now I have another problem with my withdrawal."

# Answers the rule-based engine accepts, keyed by the field it asks for.
ANSWERS = {
    "user_id": "U-482913",
    "account_email": "jane.doe@example.com",
    "withdrawal_transaction_id": "TXN-9f3a12bc",
    "source_wallet_or_account": "Sberbank wallet",
    "transaction_date": "2026-09-08",
    "deposit_method": "Bank transfer",
    "problem_description": "The money never arrived after several days of waiting.",
}


@pytest.fixture()
def mailbox(client):
    return get_email_provider()


# ------------------------------------------------------------------- helpers


def deliver(mailbox, body: str, *, subject: str = "Problem", **headers) -> str:
    """Deliver one inbound email and run a poll cycle. Returns its Message-ID."""
    email = mailbox.make_inbound(from_addr=CUSTOMER, subject=subject, body=body, **headers)
    mailbox.deliver(email)
    asyncio.run(EmailPoller(session_factory=SessionLocal).poll_once())
    return email.message_id


def tickets(staff_client) -> list[dict]:
    return sorted(staff_client.get("/api/v1/tickets").json(), key=lambda t: t["reference"])


def conversations(staff_client) -> list[dict]:
    return sorted(staff_client.get("/api/v1/conversations").json(), key=lambda c: c["id"])


def detail(staff_client, reference: str) -> dict:
    return staff_client.get(f"/api/v1/tickets/{reference}").json()


def close(staff_client, reference: str) -> None:
    resp = staff_client.patch(f"/api/v1/tickets/{reference}", json={"status": "closed"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "closed"


def last_outbound_message_id(mailbox) -> str:
    for sent in reversed(mailbox.sent_box):
        if sent.to_addr == CUSTOMER:
            return sent.message_id
    raise AssertionError("no outbound email was sent to the customer")


def drive_to_ticket(mailbox, staff_client, *, known: set[str], turns: int = 12) -> str:
    """Answer the engine's questions until a ticket outside `known` appears."""
    for _ in range(turns):
        fresh = [t["reference"] for t in tickets(staff_client) if t["reference"] not in known]
        if fresh:
            return fresh[0]
        pending = conversations(staff_client)[-1].get("pending_field")
        deliver(
            mailbox,
            ANSWERS.get(pending, ANSWERS["problem_description"]),
            subject="Re: Problem",
            in_reply_to=last_outbound_message_id(mailbox),
        )
    raise AssertionError("the new conversation never produced a ticket")


def first_ticket(mailbox, staff_client, body: str = WITHDRAWAL, subject: str = "Problem") -> dict:
    """Drive one complaint through to a ticket and return its detail."""
    deliver(mailbox, body, subject=subject)
    reference = drive_to_ticket(mailbox, staff_client, known=set())
    return detail(staff_client, reference)


def closed_thread(mailbox, staff_client) -> dict:
    """A ticket that has been closed, with its conversation detail."""
    ticket = first_ticket(mailbox, staff_client)
    close(staff_client, ticket["reference"])
    return detail(staff_client, ticket["reference"])


# ------------------------------------------------- A. closed ticket + new email


def test_email_after_close_does_not_touch_the_closed_conversation(client, mailbox, staff_client):
    before = closed_thread(mailbox, staff_client)
    before_count = len(before["conversation"]["messages"])

    deliver(mailbox, FOLLOW_UP)

    after = detail(staff_client, before["reference"])
    assert after["status"] == "closed", "the closed ticket was reopened"
    assert len(after["conversation"]["messages"]) == before_count, (
        "the closed ticket's conversation was appended to"
    )


def test_email_after_close_starts_a_new_conversation(client, mailbox, staff_client):
    closed = closed_thread(mailbox, staff_client)
    closed_id = closed["conversation"]["id"]

    deliver(mailbox, FOLLOW_UP)

    threads = conversations(staff_client)
    assert len(threads) == 2, "the follow-up should have started its own thread"
    fresh = threads[-1]
    assert fresh["id"] != closed_id
    assert any(FOLLOW_UP in m["body"] for m in fresh["messages"]), (
        "the new conversation should begin with the new inbound message"
    )
    # Nothing from the finished complaint leaked into the fresh thread.
    assert not any("TXN-9f3a12bc" in m["body"] for m in fresh["messages"])


def test_the_follow_up_produces_a_second_ticket_with_a_new_reference(
    client, mailbox, staff_client
):
    closed = closed_thread(mailbox, staff_client)
    known = {closed["reference"]}

    deliver(mailbox, FOLLOW_UP)
    second = drive_to_ticket(mailbox, staff_client, known=known)

    rows = tickets(staff_client)
    assert [t["reference"] for t in rows] == [closed["reference"], second]
    assert second != closed["reference"]
    assert second.isdigit() and int(second) > int(closed["reference"])
    assert detail(staff_client, closed["reference"])["status"] == "closed"
    assert detail(staff_client, second)["status"] == "new"
    assert detail(staff_client, second)["conversation"]["id"] != closed["conversation"]["id"]


# ------------------------------------------ B. closed ticket + reply to thread


def test_in_reply_to_does_not_reopen_a_closed_ticket(client, mailbox, staff_client):
    """The strongest threading signal: In-Reply-To on our own Message-ID."""
    closed = closed_thread(mailbox, staff_client)
    before = len(closed["conversation"]["messages"])
    ours = last_outbound_message_id(mailbox)

    deliver(mailbox, FOLLOW_UP, subject="Re: Problem", in_reply_to=ours, references=[ours])

    after = detail(staff_client, closed["reference"])
    assert after["status"] == "closed"
    assert len(after["conversation"]["messages"]) == before
    assert len(conversations(staff_client)) == 2


def test_references_chain_does_not_reopen_a_closed_ticket(client, mailbox, staff_client):
    closed = closed_thread(mailbox, staff_client)
    before = len(closed["conversation"]["messages"])
    ours = last_outbound_message_id(mailbox)

    # No In-Reply-To at all: the match can only come from the chain.
    deliver(
        mailbox,
        FOLLOW_UP,
        subject="Re: Problem",
        references=["<unrelated@elsewhere.example>", ours],
    )

    after = detail(staff_client, closed["reference"])
    assert after["status"] == "closed"
    assert len(after["conversation"]["messages"]) == before
    assert len(conversations(staff_client)) == 2


def test_subject_token_does_not_reopen_a_closed_ticket(client, mailbox, staff_client, db_session):
    """The [Ref:...] fallback is metadata too, and loses to the lifecycle rule."""
    closed = closed_thread(mailbox, staff_client)
    before = len(closed["conversation"]["messages"])
    token = db_session.get(Ticket, closed["id"]).conversation.thread_token
    assert token, "the conversation should carry a thread token"

    deliver(mailbox, FOLLOW_UP, subject=f"Re: Problem [Ref:{token}]")

    after = detail(staff_client, closed["reference"])
    assert after["status"] == "closed"
    assert len(after["conversation"]["messages"]) == before
    assert len(conversations(staff_client)) == 2


def test_the_new_thread_does_not_inherit_the_closed_thread_token(
    client, mailbox, staff_client, db_session
):
    """Otherwise the new conversation would mail under the old thread's token,
    and the customer's next reply would resolve back to the closed one."""
    closed = closed_thread(mailbox, staff_client)
    old_token = db_session.get(Ticket, closed["id"]).conversation.thread_token

    deliver(mailbox, FOLLOW_UP, subject=f"Re: Problem [Ref:{old_token}]")

    fresh = db_session.get(Conversation, conversations(staff_client)[-1]["id"])
    assert fresh.thread_token and fresh.thread_token != old_token
    assert old_token not in (fresh.subject or ""), "the old token was carried into the new thread"

    # And the mail actually sent for the new thread carries the new token.
    outbound = [e for e in mailbox.sent_bodies if e.to_addr == CUSTOMER]
    assert old_token not in outbound[-1].subject
    assert fresh.thread_token in outbound[-1].subject


# ------------------------------------------- C. closed ticket + same subject


def test_same_subject_after_close_starts_a_new_thread(client, mailbox, staff_client):
    ticket = first_ticket(mailbox, staff_client, subject="Withdrawal problem")
    close(staff_client, ticket["reference"])
    before = len(detail(staff_client, ticket["reference"])["conversation"]["messages"])

    deliver(mailbox, FOLLOW_UP, subject="Withdrawal problem")

    after = detail(staff_client, ticket["reference"])
    assert after["status"] == "closed"
    assert len(after["conversation"]["messages"]) == before
    assert len(conversations(staff_client)) == 2


# --------------------------------------------------- D. open ticket + reply


@pytest.mark.parametrize("status", ["new", "in_progress", "resolved"])
def test_reply_to_a_ticket_that_is_not_closed_continues_it(client, mailbox, staff_client, status):
    """Only `closed` ends a thread. Every other status behaves as before."""
    ticket = first_ticket(mailbox, staff_client)
    staff_client.patch(f"/api/v1/tickets/{ticket['reference']}", json={"status": status})
    before = len(detail(staff_client, ticket["reference"])["conversation"]["messages"])
    ours = last_outbound_message_id(mailbox)

    deliver(
        mailbox,
        "One more detail: the amount was wrong too.",
        subject="Re: Problem",
        in_reply_to=ours,
        references=[ours],
    )

    assert len(conversations(staff_client)) == 1, f"a '{status}' ticket must still accept replies"
    after = detail(staff_client, ticket["reference"])
    assert len(after["conversation"]["messages"]) > before
    assert len(tickets(staff_client)) == 1


def test_an_unfinished_conversation_still_threads(client, mailbox, staff_client):
    """Guards the common path: mid-collection replies have no ticket at all."""
    deliver(mailbox, "My withdrawal never arrived.")
    ours = last_outbound_message_id(mailbox)

    deliver(mailbox, "user id U-482913", subject="Re: Problem", in_reply_to=ours)

    assert len(conversations(staff_client)) == 1, "an in-progress conversation was split in two"


# ------------------------------------------------------- E. customer identity


def test_the_customer_is_reused_across_tickets(client, mailbox, staff_client, db_session):
    closed = closed_thread(mailbox, staff_client)

    deliver(mailbox, FOLLOW_UP)
    second = drive_to_ticket(mailbox, staff_client, known={closed["reference"]})

    rows = tickets(staff_client)
    assert len(rows) == 2
    assert rows[0]["customer"]["id"] == rows[1]["customer"]["id"], "customer identity was lost"
    assert detail(staff_client, second)["customer"]["email"] == CUSTOMER

    matching = (
        db_session.query(Customer)
        .filter(Customer.email == CUSTOMER, Customer.is_demo.is_(False))
        .count()
    )
    assert matching == 1, "a duplicate customer record was created"


def test_one_customer_can_hold_several_independent_tickets(client, mailbox, staff_client):
    first = closed_thread(mailbox, staff_client)

    deliver(mailbox, DEPOSIT, subject="Deposit problem")
    second = drive_to_ticket(mailbox, staff_client, known={first["reference"]})
    close(staff_client, second)

    deliver(mailbox, FOLLOW_UP)
    third = drive_to_ticket(mailbox, staff_client, known={first["reference"], second})

    rows = tickets(staff_client)
    assert [t["status"] for t in rows] == ["closed", "closed", "new"]
    threads = {detail(staff_client, t["reference"])["conversation"]["id"] for t in rows}
    assert len(threads) == 3, "each ticket must own its conversation"
    assert len({t["customer"]["id"] for t in rows}) == 1, "one customer, three tickets"
    assert third not in {first["reference"], second}


# ------------------------------------------------------------ F. idempotency


def test_redelivering_the_same_message_id_creates_no_second_thread(client, mailbox, staff_client):
    closed = closed_thread(mailbox, staff_client)

    email = mailbox.make_inbound(from_addr=CUSTOMER, subject="Problem", body=FOLLOW_UP)
    mailbox.deliver(email)
    asyncio.run(EmailPoller(session_factory=SessionLocal).poll_once())
    after_first = [c["id"] for c in conversations(staff_client)]
    messages_before = len(conversations(staff_client)[-1]["messages"])

    # The provider hands us the very same message again (a crash before the
    # acknowledgement, say). It must not start a third thread or duplicate it.
    mailbox.deliver(email)
    asyncio.run(EmailPoller(session_factory=SessionLocal).poll_once())

    assert [c["id"] for c in conversations(staff_client)] == after_first
    assert len(conversations(staff_client)[-1]["messages"]) == messages_before
    assert detail(staff_client, closed["reference"])["status"] == "closed"


def test_redelivery_after_the_new_ticket_exists_creates_no_duplicate(
    client, mailbox, staff_client
):
    closed = closed_thread(mailbox, staff_client)
    message_id = deliver(mailbox, FOLLOW_UP)
    second = drive_to_ticket(mailbox, staff_client, known={closed["reference"]})
    before = len(detail(staff_client, second)["conversation"]["messages"])

    replay = mailbox.make_inbound(
        from_addr=CUSTOMER, subject="Problem", body=FOLLOW_UP, message_id=message_id
    )
    mailbox.deliver(replay)
    asyncio.run(EmailPoller(session_factory=SessionLocal).poll_once())

    assert [t["reference"] for t in tickets(staff_client)] == [closed["reference"], second]
    assert len(detail(staff_client, second)["conversation"]["messages"]) == before


# ------------------------------------------------------------- demo isolation


def test_the_public_demo_is_unaffected(client, staff_client):
    """The demo continues a thread by explicit id, never by header, so the
    lifecycle rule (which guards real inbound threading) does not touch it."""
    created = client.post(
        "/api/v1/inbox",
        json={"from_addr": "demo@example.com", "subject": "Problem", "body": WITHDRAWAL},
    )
    assert created.status_code == 201
    conversation_id = created.json()["conversation"]["id"]

    again = client.post(
        "/api/v1/inbox",
        json={
            "from_addr": "demo@example.com",
            "body": "user id U-482913",
            "conversation_id": conversation_id,
        },
    )
    assert again.status_code == 201
    assert again.json()["conversation"]["id"] == conversation_id

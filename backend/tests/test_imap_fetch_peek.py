"""Fetching must never mark mail as read.

On a read-write mailbox, FETCH RFC822 (or BODY[]) sets \\Seen as a side effect
(RFC 3501 6.4.5). The provider used to fetch that way, so a message became
"read" the moment it was fetched - before its transaction committed. If the
turn then failed (an SMTP error, say), UID SEARCH UNSEEN never returned the
message again: it sat in the mailbox, marked read, never processed.

The older stub in test_imap_smtp_provider returns canned data regardless of
the fetch item, which is exactly why that went unnoticed. The fake below
behaves like a real server on this point, so these tests fail against the old
fetch and pass against BODY.PEEK[].
"""

from __future__ import annotations

from app.email.idle import ImapIdleConnection
from tests.test_imap_smtp_provider import make_provider

RAW = (
    b"Message-ID: <peek-1@example.com>\r\n"
    b"From: customer@example.com\r\n"
    b"To: complaints@example.com\r\n"
    b"Subject: Deposit\r\n"
    b"\r\n"
    b"My deposit was not credited.\r\n"
)


class RfcMailbox:
    """A single-message mailbox with RFC 3501 \\Seen semantics."""

    def __init__(self) -> None:
        self.seen: set[bytes] = set()
        self.messages = {b"7": RAW}
        self.fetch_items: list[str] = []

    def select(self, mailbox, readonly=False):
        return "OK", [b"1"]

    def uid(self, command, *args):
        command = command.lower()
        if command == "search":
            unseen = [uid for uid in self.messages if uid not in self.seen]
            return "OK", [b" ".join(unseen)]
        if command == "fetch":
            uid, item = args[0], args[1]
            self.fetch_items.append(item)
            # The rule under test: a non-PEEK body fetch sets \Seen.
            upper = item.upper()
            if "PEEK" not in upper and ("RFC822" in upper or "BODY[" in upper):
                self.seen.add(uid)
            return "OK", [(b"1 (UID 7 BODY[] {%d}" % len(RAW), self.messages[uid]), b")"]
        if command == "store":
            if "\\Seen" in args[-1]:
                self.seen.add(args[0])
            return "OK", [b"1"]
        raise AssertionError(f"unexpected IMAP command {command}")

    def close(self):
        pass

    def logout(self):
        pass


def provider_on(mailbox: RfcMailbox, **overrides):
    provider = make_provider(**overrides)
    provider._imap_connect = lambda: mailbox
    return provider


async def test_fetching_does_not_mark_the_message_read():
    mailbox = RfcMailbox()
    provider = provider_on(mailbox)

    fetched = await provider.fetch_new()

    assert [m.message_id for m in fetched] == ["<peek-1@example.com>"]
    assert b"7" not in mailbox.seen, "the fetch itself marked the message \\Seen"


async def test_a_failed_turn_is_retried_on_the_next_fetch():
    """The whole point: nothing acknowledged means the message comes back."""
    mailbox = RfcMailbox()
    provider = provider_on(mailbox)

    first = await provider.fetch_new()
    # Processing fails here, so mark_processed() is never called.
    second = await provider.fetch_new()

    assert [m.message_id for m in first] == [m.message_id for m in second], (
        "a message whose turn failed was never offered again - it would be lost"
    )


async def test_only_acknowledgement_marks_the_message_read():
    mailbox = RfcMailbox()
    provider = provider_on(mailbox)

    fetched = await provider.fetch_new()
    await provider.mark_processed(fetched[0].message_id)

    assert b"7" in mailbox.seen
    assert await provider.fetch_new() == []


async def test_the_fetch_item_is_a_peek():
    mailbox = RfcMailbox()
    provider = provider_on(mailbox)

    await provider.fetch_new()

    assert mailbox.fetch_items == ["(BODY.PEEK[])"]


async def test_the_reused_idle_connection_also_peeks():
    """The IDLE fast path fetches on the long-lived connection, not a fresh
    one - it must obey the same rule."""
    mailbox = RfcMailbox()
    provider = provider_on(mailbox, idle_enabled=True)
    idle = ImapIdleConnection(connect=lambda: mailbox, mailbox="INBOX", keepalive_seconds=300)
    idle._conn = mailbox  # already connected and selected, not idling
    provider._idle = idle

    fetched = await provider.fetch_new()

    assert [m.message_id for m in fetched] == ["<peek-1@example.com>"]
    assert b"7" not in mailbox.seen
    assert mailbox.fetch_items == ["(BODY.PEEK[])"]

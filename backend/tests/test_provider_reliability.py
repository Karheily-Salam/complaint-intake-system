"""The poller must never be able to hang the application, and must recover.

The failure this guards against is concrete: a connection the server accepts
and then never answers (what a silently-dropping firewall produces) blocks a
socket with no timeout forever. Because the provider is awaited on the same
event loop as the HTTP API, that would take the whole application down, and
`restart: unless-stopped` does not restart a hung-but-alive container.
"""

from __future__ import annotations

import asyncio
import threading

import pytest

from app.email.base import OutboundEmail
from app.email.factory import get_email_provider
from app.services.email_poller import EmailPoller
from tests.test_imap_smtp_provider import StubImap, StubSmtp, make_provider


@pytest.fixture()
def poller(client):
    from app.core.database import SessionLocal

    return EmailPoller(session_factory=SessionLocal)


# ------------------------------------------------------------------ timeouts


def test_imap_connect_receives_the_configured_timeout(monkeypatch):
    captured: dict = {}

    class RecordingIMAP(StubImap):
        def __init__(self, host, port, timeout=None):
            super().__init__({})
            captured.update(host=host, port=port, timeout=timeout)

        def login(self, *_):
            return "OK", []

    monkeypatch.setattr("imaplib.IMAP4_SSL", RecordingIMAP)

    provider = make_provider(imap_timeout=17)
    provider._imap_connect()

    assert captured["timeout"] == 17, "a connect without a timeout can hang forever"


def test_smtp_connect_receives_the_configured_timeout(monkeypatch):
    captured: dict = {}

    class RecordingSMTP(StubSmtp):
        def __init__(self, host, port, timeout=None):
            super().__init__()
            captured.update(host=host, port=port, timeout=timeout)

        def starttls(self):
            pass

        def login(self, *_):
            pass

    monkeypatch.setattr("smtplib.SMTP", RecordingSMTP)

    provider = make_provider(smtp_timeout=23, smtp_use_ssl=False, smtp_use_tls=True)
    provider._smtp_connect()

    assert captured["timeout"] == 23


def test_settings_expose_the_timeouts(monkeypatch):
    from app.core.config import Settings

    settings = Settings(imap_timeout_seconds=45, smtp_timeout_seconds=46)
    assert settings.imap_timeout_seconds == 45
    assert settings.smtp_timeout_seconds == 46


# --------------------------------------------------------- off the event loop


async def test_blocking_imap_work_runs_off_the_event_loop(monkeypatch):
    """The blocking body must not execute on the loop's own thread."""
    loop_thread = threading.get_ident()
    seen: dict = {}

    provider = make_provider()

    def record_and_return():
        seen["thread"] = threading.get_ident()
        return []

    monkeypatch.setattr(provider, "_fetch_new_blocking", record_and_return)
    await provider.fetch_new()

    assert seen["thread"] != loop_thread, (
        "imaplib ran on the event loop - a slow or hung mailbox would stall "
        "every HTTP request in the process"
    )


async def test_blocking_smtp_work_runs_off_the_event_loop(monkeypatch):
    loop_thread = threading.get_ident()
    seen: dict = {}
    provider = make_provider()

    def record(email_out):
        from app.email.base import SentEmail

        seen["thread"] = threading.get_ident()
        return SentEmail(
            message_id="<x@y>", to_addr=email_out.to_addr, subject=email_out.subject
        )

    monkeypatch.setattr(provider, "_send_blocking", record)
    await provider.send(OutboundEmail(to_addr="a@b.com", subject="s", body="b"))

    assert seen["thread"] != loop_thread


async def test_the_event_loop_keeps_running_during_a_slow_provider(monkeypatch):
    """Observable proof: other work progresses while the provider blocks."""
    provider = make_provider()
    ticks = 0

    def slow_blocking():
        import time

        time.sleep(0.3)
        return []

    monkeypatch.setattr(provider, "_fetch_new_blocking", slow_blocking)

    async def ticker():
        nonlocal ticks
        for _ in range(15):
            await asyncio.sleep(0.02)
            ticks += 1

    await asyncio.gather(provider.fetch_new(), ticker())

    assert ticks >= 10, (
        f"only {ticks} ticks ran while the provider was busy - the loop was blocked"
    )


# ----------------------------------------------------------- failure recovery


async def test_fetch_failure_does_not_stop_polling(poller, monkeypatch):
    mailbox = get_email_provider()
    calls = {"n": 0}
    original_fetch = mailbox.fetch_new

    async def flaky_fetch():
        calls["n"] += 1
        if calls["n"] <= 2:
            raise OSError("connection reset by peer")
        return await original_fetch()

    monkeypatch.setattr(mailbox, "fetch_new", flaky_fetch)

    # Two failing cycles are survived, not fatal.
    assert await poller.poll_once() == 0
    assert await poller.poll_once() == 0

    # The mailbox recovers and the next cycle processes normally.
    mailbox.deliver(
        mailbox.make_inbound(
            from_addr="recovered@example.com",
            subject="Withdrawal",
            body="My withdrawal is stuck, user id U-482913.",
        )
    )
    assert await poller.poll_once() == 1
    assert calls["n"] == 3


async def test_a_hanging_provider_is_abandoned_rather_than_waited_on_forever(
    poller, monkeypatch
):
    """The poller's own ceiling, in case a provider ignores its socket timeout."""
    from app.services import email_poller as poller_module

    # The ceiling has a 5s floor, so this is the shortest it can be.
    monkeypatch.setattr(poller_module.settings, "email_operation_timeout_seconds", 1)
    mailbox = get_email_provider()

    async def hangs_effectively_forever():
        await asyncio.sleep(600)
        return []

    monkeypatch.setattr(mailbox, "fetch_new", hangs_effectively_forever)

    started = asyncio.get_running_loop().time()
    # Returns on its own rather than needing to be cancelled from outside.
    assert await asyncio.wait_for(poller.poll_once(), timeout=30) == 0
    elapsed = asyncio.get_running_loop().time() - started

    assert elapsed < 15, f"poll took {elapsed:.1f}s - it was not bounded by the ceiling"


async def test_failures_are_reported_not_swallowed(poller, monkeypatch, caplog):
    import logging

    mailbox = get_email_provider()

    async def broken():
        raise OSError("imap exploded")

    monkeypatch.setattr(mailbox, "fetch_new", broken)

    with caplog.at_level(logging.ERROR):
        assert await poller.poll_once() == 0

    assert any("Failed to fetch new email" in r.message for r in caplog.records)
    # And nothing resembling a credential is in the log.
    assert "password" not in caplog.text.lower()


# ------------------------------------------------------------- UID cache bound


def test_uid_cache_is_bounded_and_evicts_oldest():
    provider = make_provider(uid_cache_size=5)

    for i in range(20):
        provider._remember_uid(f"<msg-{i}@x>", str(i).encode())

    assert len(provider._uid_by_message_id) == 5
    # Newest kept, oldest evicted.
    assert "<msg-19@x>" in provider._uid_by_message_id
    assert "<msg-0@x>" not in provider._uid_by_message_id


def test_eviction_does_not_break_acknowledgement_of_recent_mail(monkeypatch):
    """Losing an old UID is safe: the message is refetched and deduplicated."""
    provider = make_provider(uid_cache_size=2)
    stub = StubImap({})
    monkeypatch.setattr(provider, "_imap_connect", lambda: stub)

    provider._remember_uid("<old@x>", b"1")
    provider._remember_uid("<newer@x>", b"2")
    provider._remember_uid("<newest@x>", b"3")

    provider._mark_processed_blocking("<newest@x>")
    assert any(cmd[0] == "store" for cmd in stub.commands)

    # The evicted one is simply unknown - handled, not crashed.
    provider._mark_processed_blocking("<old@x>")

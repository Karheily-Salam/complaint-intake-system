"""IMAP IDLE: the trigger that removes the poll interval from the reply path.

The whole design rests on one property - a notification is a *hint*, never a
decision. These tests pin that: a spurious wake-up must be harmless, a missed
one must be recovered by the timeout, and the fetch path must stay exactly the
existing UID SEARCH UNSEEN with Message-ID idempotency behind it.

Everything here runs against fakes. No network, no credentials, no mailbox.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from app.core import timing
from app.email.base import EmailProvider, InboundEmail
from app.email.idle import ImapIdleConnection
from app.services.email_poller import EmailPoller

# --------------------------------------------------------------- fake socket


class FakeSocket:
    """A socket whose readability is driven by the test."""

    def __init__(self) -> None:
        self.ready = False
        self._pending = 0

    def pending(self) -> int:
        return self._pending

    def fileno(self) -> int:  # pragma: no cover - select is patched
        return -1


class FakeImap:
    """Enough of imaplib.IMAP4 to drive IDLE, recording every command."""

    def __init__(self, *, refuse_idle: bool = False, die_on: str | None = None) -> None:
        self.sent: list[bytes] = []
        self.lines: list[bytes] = []
        self.selected: str | None = None
        self.closed = False
        self._tag = 0
        self._sock = FakeSocket()
        self.refuse_idle = refuse_idle
        self.die_on = die_on
        self._queued_notification: bytes | None = None
        self.idling = False
        self.early_exists = False
        # imaplib files untagged responses from ordinary commands here.
        self.untagged_responses: dict[str, list[bytes]] = {}

    # -- imaplib surface --
    def _new_tag(self) -> bytes:
        self._tag += 1
        return b"T%d" % self._tag

    def select(self, mailbox, readonly=False):
        self.selected = mailbox
        return "OK", [b"1"]

    def socket(self):
        return self._sock

    def send(self, data: bytes) -> None:
        if self.die_on and self.die_on in data.decode(errors="replace"):
            raise OSError("connection reset")
        self.sent.append(data)
        if data.endswith(b"IDLE\r\n"):
            if self.refuse_idle:
                # A real refusal is *tagged*, e.g. "A7 NO IDLE not supported".
                self.lines.append(b"T%d NO IDLE not supported\r\n" % self._tag)
                return
            if self.early_exists:
                # Untagged update legitimately arriving before the continuation.
                self.lines.append(b"* 15 EXISTS\r\n")
                self.early_exists = False
            self.lines.append(b"+ idling\r\n")
            self.idling = True
            # The server only pushes updates once the client is idling.
            if self._queued_notification is not None:
                self.lines.append(self._queued_notification)
                self._queued_notification = None
                self._sock.ready = True
        elif data == b"DONE\r\n":
            self.idling = False
            self.lines.append(b"T%d OK Idle completed\r\n" % self._tag)

    def readline(self) -> bytes:
        if not self.lines:
            self._sock.ready = False
            self._sock._pending = 0
            return b""
        line = self.lines.pop(0)
        if not self.lines:
            self._sock.ready = False
            self._sock._pending = 0
        return line

    def uid(self, command, *args):
        if command.lower() == "search":
            return "OK", [b""]
        return "OK", [b"1"]

    def close(self):
        self.closed = True

    def logout(self):
        self.closed = True

    # -- test helper --
    def push_notification(self, line: bytes = b"* 14 EXISTS\r\n") -> None:
        """New mail. Delivered now if idling, otherwise once IDLE is entered."""
        if self.idling:
            self.lines.append(line)
            self._sock.ready = True
        else:
            self._queued_notification = line


@pytest.fixture()
def patched_select(monkeypatch):
    """select() reports readable iff the fake socket says so."""
    def fake_select(rlist, _w, _x, timeout):
        return ([s for s in rlist if getattr(s, "ready", False)], [], [])

    monkeypatch.setattr("app.email.idle.select.select", fake_select)
    return fake_select


def make_idle(conn: FakeImap, keepalive: int = 300) -> ImapIdleConnection:
    return ImapIdleConnection(connect=lambda: conn, mailbox="INBOX", keepalive_seconds=keepalive)


# ------------------------------------------------------- notification -> now


def test_a_notification_returns_immediately(patched_select):
    conn = FakeImap()
    idle = make_idle(conn)
    conn.push_notification()

    started = time.monotonic()
    assert idle.wait(timeout=30) is True
    elapsed = time.monotonic() - started

    assert elapsed < 0.5, "a notification must not wait on the interval"
    assert idle.last_notified_at is not None


def test_idle_is_entered_and_the_connection_is_selected(patched_select):
    conn = FakeImap()
    idle = make_idle(conn)
    conn.push_notification()
    idle.wait(timeout=5)

    assert conn.selected == "INBOX"
    assert any(c.endswith(b"IDLE\r\n") for c in conn.sent)


def test_quiet_mailbox_waits_for_the_full_timeout(patched_select):
    conn = FakeImap()
    idle = make_idle(conn)

    started = time.monotonic()
    assert idle.wait(timeout=1.0) is False
    assert time.monotonic() - started >= 0.9


def test_exit_idle_sends_done_and_leaves_the_connection_usable(patched_select):
    conn = FakeImap()
    idle = make_idle(conn)
    conn.push_notification()
    idle.wait(timeout=5)

    idle.exit_idle()

    assert b"DONE\r\n" in conn.sent
    assert idle.connection is conn
    assert not conn.closed, "the connection must be reused, not reopened"


def test_idle_is_re_entered_after_a_notification(patched_select):
    """Back into IDLE straight away - no gap where mail is unnoticed."""
    conn = FakeImap()
    idle = make_idle(conn)
    conn.push_notification()
    idle.wait(timeout=5)
    idle.exit_idle()
    conn._sock.ready = False

    conn.push_notification()
    assert idle.wait(timeout=5) is True
    assert sum(1 for c in conn.sent if c.endswith(b"IDLE\r\n")) >= 2


# ------------------------------------------------------------ robustness


def test_refused_idle_does_not_hang_or_raise(patched_select):
    conn = FakeImap(refuse_idle=True)
    idle = make_idle(conn)
    assert idle.wait(timeout=0.5) is False


def test_refused_idle_falls_back_to_polling_without_reconnecting():
    """A server without IDLE must not be hit with a fresh login every cycle."""
    logins = []

    def connect():
        conn = FakeImap(refuse_idle=True)
        logins.append(conn)
        return conn

    idle = ImapIdleConnection(connect=connect, mailbox="INBOX", keepalive_seconds=300)
    assert idle.wait(timeout=0.3) is False
    assert idle.wait(timeout=0.3) is False

    assert len(logins) == 1, f"reconnected {len(logins)} times on a non-IDLE server"
    assert not idle.idle_supported
    assert idle.connection is logins[0], "the connection is kept for ordinary fetches"


def test_untagged_exists_before_the_continuation_is_not_a_refusal(patched_select):
    """RFC 3501 lets the server push updates before '+ idling'."""
    conn = FakeImap()
    conn.early_exists = True
    idle = make_idle(conn)

    assert idle.wait(timeout=5) is True, "the early EXISTS is new mail, report it"
    assert idle.idle_supported
    assert not conn.closed


def test_mail_that_arrived_while_not_idling_is_reported_at_once(patched_select):
    """The processing-window race: EXISTS rides on the next command's response.

    Without this, a second email landing while the first was being processed
    would wait out the full fallback interval.
    """
    conn = FakeImap()
    idle = make_idle(conn)
    conn.push_notification()
    idle.wait(timeout=5)
    idle.exit_idle()

    # imaplib filed an EXISTS from the SEARCH/STORE that ran while we were busy.
    conn.untagged_responses["EXISTS"] = [b"15"]

    started = time.monotonic()
    assert idle.wait(timeout=30) is True
    assert time.monotonic() - started < 0.5
    assert "EXISTS" not in conn.untagged_responses, "consumed, so it cannot retrigger"


def test_a_rejected_login_is_not_retried_rapidly():
    """Hammering a wrong password is how a mailbox gets locked."""
    import imaplib

    attempts = []

    def bad_login():
        attempts.append(1)
        raise imaplib.IMAP4.error("AUTHENTICATIONFAILED")

    idle = ImapIdleConnection(connect=bad_login, mailbox="INBOX", keepalive_seconds=300)
    assert idle.wait(timeout=0.6) is False
    assert len(attempts) == 1, f"{len(attempts)} login attempts in one wait"


def test_a_dropped_connection_is_rebuilt(patched_select):
    """A dead socket must not end the wait loop permanently."""
    dead, alive = FakeImap(die_on="IDLE"), FakeImap()
    conns = [dead, alive]
    idle = ImapIdleConnection(
        connect=lambda: conns.pop(0), mailbox="INBOX", keepalive_seconds=300
    )
    alive.push_notification()

    assert idle.wait(timeout=5) is True
    assert idle.connection is alive


def test_reconnect_backoff_stays_short(patched_select):
    """A long backoff would make the next email wait for nothing."""
    def always_fail():
        raise OSError("refused")

    idle = ImapIdleConnection(connect=always_fail, mailbox="INBOX", keepalive_seconds=300)
    started = time.monotonic()
    assert idle.wait(timeout=1.0) is False
    # Bounded retries inside one second, not one long sleep.
    assert time.monotonic() - started < 2.0


def test_stop_requested_before_the_wait_is_still_honoured(patched_select):
    """Stop is sticky: a shutdown landing just before a wait must not be lost."""
    conn = FakeImap()
    idle = make_idle(conn)
    idle.request_stop()

    started = time.monotonic()
    assert idle.wait(timeout=30) is False
    assert time.monotonic() - started < 1.0


def test_stop_unblocks_a_wait_already_in_progress(patched_select):
    """asyncio cannot cancel a thread in select(); stopping must be cooperative."""
    import threading

    conn = FakeImap()
    idle = make_idle(conn)
    result: list[bool] = []
    worker = threading.Thread(target=lambda: result.append(idle.wait(timeout=30)))
    worker.start()
    time.sleep(0.2)

    started = time.monotonic()
    idle.request_stop()
    worker.join(timeout=3)

    assert not worker.is_alive(), "the waiting thread did not exit"
    assert time.monotonic() - started < 1.5
    assert result == [False]


def test_close_is_safe_and_idempotent(patched_select):
    conn = FakeImap()
    idle = make_idle(conn)
    conn.push_notification()
    idle.wait(timeout=5)

    idle.close()
    idle.close()
    assert conn.closed
    assert idle.connection is None


def test_keepalive_cycles_idle_without_reporting_activity(patched_select):
    """Re-issuing IDLE must not look like new mail."""
    conn = FakeImap()
    idle = make_idle(conn)
    idle._keepalive_seconds = 0  # below the 30s floor, only to expire instantly here
    assert idle.wait(timeout=0.6) is False
    assert b"DONE\r\n" in conn.sent
    assert sum(1 for c in conn.sent if c.endswith(b"IDLE\r\n")) >= 2


# ------------------------------------------------ the poller's use of it


class RecordingProvider(EmailProvider):
    """A provider whose wait can be released by the test."""

    name = "recording"
    is_simulated = True

    def __init__(self, inbox: list[InboundEmail]) -> None:
        self.inbox = list(inbox)
        self.waits: list[float] = []
        self.fetches = 0
        self.acknowledged: list[str] = []
        self.notify = True

    async def fetch_new(self):
        self.fetches += 1
        return list(self.inbox)

    async def send(self, email):  # pragma: no cover - not used here
        raise AssertionError("no mail should be sent")

    async def mark_processed(self, message_id: str) -> None:
        self.acknowledged.append(message_id)
        self.inbox = [m for m in self.inbox if m.message_id != message_id]

    async def wait_for_activity(self, timeout: float) -> bool:
        self.waits.append(timeout)
        return self.notify


def inbound(mid: str) -> InboundEmail:
    return InboundEmail(
        message_id=mid,
        from_addr="c@example.com",
        to_addr="support@example.com",
        subject="hi",
        body="My withdrawal never arrived.",
    )


async def test_default_wait_preserves_plain_polling():
    """Providers that do not override wait_for_activity behave as before."""
    started = time.monotonic()
    assert await EmailProvider.wait_for_activity(object(), 0.2) is False
    assert time.monotonic() - started >= 0.15


async def test_poller_fetches_immediately_on_every_notification():
    """No interval, no debounce: a notification goes straight to a fetch."""
    provider = RecordingProvider([])
    poller = EmailPoller(provider=provider)

    task = asyncio.create_task(poller.run_forever())
    await asyncio.sleep(0.15)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert provider.fetches >= 2, "each notification should drive a fetch"
    # The interval is only ever offered as a ceiling to the wait, never slept on.
    assert all(w == max(5, 60) for w in provider.waits)


class MailOnEveryNotification(RecordingProvider):
    """Each notification genuinely has a new message behind it."""

    def __init__(self) -> None:
        super().__init__([])
        self._n = 0

    async def wait_for_activity(self, timeout: float) -> bool:
        self.waits.append(timeout)
        self._n += 1
        self.inbox.append(inbound(f"<m{self._n}@x>"))
        return True


def _fake_handle_success(monkeypatch):
    async def fake_handle(self, email):
        class R:
            conversation = type("C", (), {"id": 1})()
            ticket_reference = None
        return R()

    monkeypatch.setattr(
        "app.services.email_poller.IntakeService.handle_inbound_email", fake_handle
    )


def _record_sleeps(monkeypatch) -> tuple[list[float], object]:
    slept: list[float] = []
    real_sleep = asyncio.sleep

    async def recording_sleep(seconds, *a, **k):
        slept.append(seconds)
        await real_sleep(0)

    monkeypatch.setattr("app.services.email_poller.asyncio.sleep", recording_sleep)
    return slept, real_sleep


async def test_nothing_is_slept_between_a_real_notification_and_the_work(monkeypatch):
    """With mail behind every notification, the only sleep is a zero yield."""
    provider = MailOnEveryNotification()
    poller = EmailPoller(provider=provider)
    _fake_handle_success(monkeypatch)
    slept, real_sleep = _record_sleeps(monkeypatch)

    task = asyncio.create_task(poller.run_forever())
    await real_sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert provider.acknowledged, "notifications should have been processed"
    assert slept and set(slept) == {0}, f"artificial delay on the hot path: {set(slept)}"


async def test_degraded_pacing_never_approaches_the_poll_interval(monkeypatch):
    """A stuck provider is paced, but a real email would wait at most ~1s."""
    provider = RecordingProvider([])
    poller = EmailPoller(provider=provider)
    slept, real_sleep = _record_sleeps(monkeypatch)

    task = asyncio.create_task(poller.run_forever())
    await real_sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert any(s > 0 for s in slept), "repeated empty wake-ups should be paced"
    assert max(slept) <= 1.0, f"degraded floor reached {max(slept)}s"


async def test_a_wait_that_keeps_failing_does_not_hot_loop(monkeypatch):
    class Broken(RecordingProvider):
        async def wait_for_activity(self, timeout: float) -> bool:
            self.waits.append(timeout)
            raise OSError("socket gone")

    provider = Broken([])
    poller = EmailPoller(provider=provider)
    slept, real_sleep = _record_sleeps(monkeypatch)

    task = asyncio.create_task(poller.run_forever())
    await real_sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert 1.0 in slept, "an erroring cycle should back off briefly"


async def test_a_stuck_provider_cannot_spin_the_event_loop():
    """Repeated empty wake-ups must self-pace rather than burn the CPU."""
    provider = RecordingProvider([])
    poller = EmailPoller(provider=provider)

    task = asyncio.create_task(poller.run_forever())
    await asyncio.sleep(0.3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # Unbounded spinning would run thousands of cycles in 0.3s.
    assert provider.fetches < 200, f"busy loop: {provider.fetches} cycles"


async def test_spurious_notification_is_harmless(monkeypatch):
    """An empty mailbox after a wake-up costs one search and nothing else."""
    provider = RecordingProvider([])
    poller = EmailPoller(provider=provider)
    processed = await poller.poll_once()
    assert processed == 0
    assert provider.acknowledged == []


async def test_multiple_messages_are_all_processed_in_one_cycle(monkeypatch):
    provider = RecordingProvider([inbound("<a@x>"), inbound("<b@x>"), inbound("<c@x>")])
    poller = EmailPoller(provider=provider)
    _fake_handle_success(monkeypatch)

    assert await poller.poll_once() == 3
    assert sorted(provider.acknowledged) == ["<a@x>", "<b@x>", "<c@x>"]


async def test_each_message_in_a_batch_gets_its_own_timings(monkeypatch):
    """First-write-wins must not leak message one's marks into message two."""
    provider = RecordingProvider([inbound("<a@x>"), inbound("<b@x>")])
    poller = EmailPoller(provider=provider)
    seen: list[dict] = []

    async def fake_handle(self, email):
        trace = timing.current_trace()
        trace.mark(timing.SMTP_STARTED)
        await asyncio.sleep(0.02)
        trace.mark(timing.SMTP_FINISHED)
        seen.append(dict(trace.marks))

        class R:
            conversation = type("C", (), {"id": 1})()
            ticket_reference = None
        return R()

    monkeypatch.setattr(
        "app.services.email_poller.IntakeService.handle_inbound_email", fake_handle
    )
    cycle = timing.start_trace(**{timing.NOTIFIED: time.monotonic()})
    await poller.poll_once()

    first, second = seen
    assert second[timing.SMTP_STARTED] > first[timing.SMTP_FINISHED], (
        "message two reported message one's SMTP time"
    )
    # Both inherit the cycle's detection mark.
    assert first[timing.NOTIFIED] == second[timing.NOTIFIED] == cycle.marks[timing.NOTIFIED]
    # And the cycle trace itself is restored afterwards, unpolluted.
    assert timing.current_trace() is cycle
    assert timing.SMTP_STARTED not in cycle.marks
    timing.clear_trace()


# ------------------------------------------------------------ instrumentation


def test_trace_records_the_stages_and_never_content():
    trace = timing.start_trace()
    trace.mark(timing.NOTIFIED, 100.0)       # T0
    trace.mark(timing.IDLE_EXITED, 100.04)   # T1
    trace.mark(timing.FETCHED, 100.2)        # T3
    trace.mark(timing.INTAKE_STARTED, 100.21)  # T4
    trace.mark(timing.SMTP_STARTED, 100.9)   # T6
    trace.mark(timing.SMTP_FINISHED, 101.5)  # T7
    trace.mark(timing.INTAKE_FINISHED, 101.6)  # T5

    summary = trace.summary()
    assert "detect_to_fetch=200ms" in summary
    assert "idle_exit=40ms" in summary
    assert "fetch_to_processing=10ms" in summary
    assert "processing_to_smtp=690ms" in summary
    assert "smtp=600ms" in summary
    assert "handler=1390ms" in summary
    assert "total=1500ms" in summary  # T0 -> T7
    for forbidden in ("@", "body", "subject", "password"):
        assert forbidden not in summary
    timing.clear_trace()


def test_total_falls_back_to_the_cycle_start_without_a_notification():
    trace = timing.start_trace()
    trace.mark(timing.POLL_STARTED, 10.0)
    trace.mark(timing.INTAKE_FINISHED, 10.75)
    assert "total=750ms" in trace.summary()
    timing.clear_trace()


def test_marking_without_a_trace_is_a_no_op():
    timing.clear_trace()
    timing.mark(timing.NOTIFIED)  # must not raise
    assert timing.current_trace() is None


def test_first_mark_wins_so_a_retry_cannot_move_t0():
    trace = timing.start_trace()
    trace.mark(timing.NOTIFIED, 10.0)
    trace.mark(timing.NOTIFIED, 99.0)
    assert trace.marks[timing.NOTIFIED] == 10.0
    timing.clear_trace()


# ------------------------------------------- the provider's hot path, end to end


async def test_fetch_after_a_notification_reuses_the_idle_connection(patched_select):
    """The single biggest latency saving: no TLS handshake + LOGIN + SELECT
    between a notification and the fetch. One login for the whole exchange."""
    from tests.test_imap_smtp_provider import make_provider

    logins: list[FakeImap] = []

    def connect():
        conn = FakeImap()
        logins.append(conn)
        return conn

    provider = make_provider(idle_enabled=True)
    provider._imap_connect = connect

    # Arrange for mail to arrive once the connection is idling.
    original = FakeImap.send

    def send_then_notify(self, data):
        original(self, data)
        if data.endswith(b"IDLE\r\n") and self.sent.count(data) == 1:
            self.push_notification()

    FakeImap.send = send_then_notify
    try:
        assert await provider.wait_for_activity(30) is True
        await provider.fetch_new()
        await provider.wait_for_activity(0.2)  # straight back into IDLE
    finally:
        FakeImap.send = original
        await provider.shutdown()

    assert len(logins) == 1, f"{len(logins)} logins - the hot path reconnected"
    conn = logins[0]
    assert b"DONE\r\n" in conn.sent, "IDLE must be left before the SEARCH"
    assert sum(1 for c in conn.sent if c.endswith(b"IDLE\r\n")) >= 2, "IDLE re-entered"


async def test_idle_disabled_is_exactly_the_old_polling(monkeypatch):
    """The kill switch: EMAIL_IDLE_ENABLED=false must not open any connection."""
    from tests.test_imap_smtp_provider import make_provider

    provider = make_provider(idle_enabled=False)
    provider._imap_connect = lambda: pytest.fail("IDLE disabled must not connect")

    started = time.monotonic()
    assert await provider.wait_for_activity(0.2) is False
    assert time.monotonic() - started >= 0.15
    assert provider._idle is None


# ------------------------------------ pacing a streak of failed IDLE sessions


def _idle_breaks_after_login(logins: list):
    """Every connection logs in fine, then fails as soon as IDLE is sent."""

    def connect():
        conn = FakeImap(die_on="IDLE")
        logins.append(conn)
        return conn

    return connect


def test_a_one_off_drop_still_reconnects_immediately(patched_select):
    """Fast recovery for an ordinary transient failure must be preserved."""
    dead, alive = FakeImap(die_on="IDLE"), FakeImap()
    conns = [dead, alive]
    idle = ImapIdleConnection(
        connect=lambda: conns.pop(0), mailbox="INBOX", keepalive_seconds=300
    )
    alive.push_notification()

    started = time.monotonic()
    assert idle.wait(timeout=5) is True
    assert time.monotonic() - started < 0.5, "a single drop must not be paced"


def test_repeated_idle_failures_after_login_are_paced(patched_select):
    """Without pacing this loop logs in hundreds of times a minute."""
    logins: list = []
    idle = ImapIdleConnection(
        connect=_idle_breaks_after_login(logins), mailbox="INBOX", keepalive_seconds=300
    )

    assert idle.wait(timeout=1.2) is False

    # Two immediate attempts are tolerated, then 1s, 2s, 4s... apart.
    assert len(logins) <= 4, f"{len(logins)} logins in 1.2s - reconnect loop not paced"


def test_pacing_carries_across_wait_calls(patched_select):
    """The poller calls wait() every cycle; a streak must not reset each time."""
    logins: list = []
    idle = ImapIdleConnection(
        connect=_idle_breaks_after_login(logins), mailbox="INBOX", keepalive_seconds=300
    )
    idle.wait(timeout=0.3)
    after_first = len(logins)

    idle.wait(timeout=0.3)

    assert len(logins) == after_first, "a fresh wait() call bypassed the pause"


def test_the_pause_is_bounded():
    idle = ImapIdleConnection(
        connect=lambda: FakeImap(), mailbox="INBOX", keepalive_seconds=300
    )
    idle._quick_failures = 50
    idle._last_failure_at = time.monotonic()

    assert 0 < idle._reconnect_pause() <= 30.0


def test_a_healthy_session_that_drops_starts_a_new_streak(patched_select):
    """A long-lived IDLE that finally drops is ordinary network life."""
    conn = FakeImap()
    idle = make_idle(conn)
    conn.push_notification()
    idle.wait(timeout=5)  # now idling
    idle._quick_failures = 5
    idle._idle_started_at = time.monotonic() - 60  # up for a minute

    idle._drop("simulated network reset")

    assert idle._quick_failures == 1
    assert idle._reconnect_pause() == 0.0, "a healthy drop must reconnect at once"


def test_a_notification_clears_the_streak(patched_select):
    conn = FakeImap()
    idle = make_idle(conn)
    idle._quick_failures = 3
    conn.push_notification()

    assert idle.wait(timeout=5) is True
    assert idle._quick_failures == 0

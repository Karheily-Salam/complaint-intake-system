"""A persistent IMAP connection held in IDLE, for near-real-time delivery.

RFC 2177 IDLE lets the server push a notification the moment mail arrives,
instead of the client asking every N seconds. That is the difference between a
~31 second average reply and a ~1 second one.

Four decisions shape this module, each one the result of a measured problem:

**The notification is only a trigger.** What arrives during IDLE is never
parsed to decide what to fetch - the connection simply wakes the caller, which
then runs the ordinary ``UID SEARCH UNSEEN``. A spurious wake-up costs one
empty search; a missed one is caught by the caller's timeout. Correctness
therefore does not depend on the notification being right, only on it being
*prompt*, which is the only property IDLE actually guarantees.

**Never put a timeout on the buffered reader.** ``imaplib`` wraps the socket
in a buffered file object, and a ``settimeout`` that fires leaves it
permanently broken - verified against the live server::

    OSError: cannot read from timed out object

So waiting is done with ``select()`` on the raw socket, plus a check of the
SSL layer's own decrypted buffer, which ``select()`` cannot see.

**The same connection does the fetching.** Exiting IDLE costs one round trip
(~40ms measured); opening a fresh authenticated connection costs a TLS
handshake plus LOGIN plus SELECT (~200-400ms). Reusing the already-selected
connection keeps that off the critical path entirely.

**Mail that arrives while we are not idling is not lost.** While the caller is
processing, the connection is outside IDLE, and the server reports any new
mail as an untagged ``EXISTS`` riding on the *next* command's response - which
imaplib quietly files away. That is checked before re-entering IDLE, otherwise
a second email landing during the first one's processing would wait out the
full fallback interval.
"""

from __future__ import annotations

import imaplib
import select
import threading
import time
from collections.abc import Callable

from app.core.logging import get_logger

logger = get_logger(__name__)

# How long a single select() waits before looping. This does NOT delay
# detection - select returns the instant data arrives - it only bounds how
# long a shutdown request or a keepalive can sit unnoticed.
_WAIT_SLICE_SECONDS = 0.5

# Reconnect pacing after a *network* failure. Deliberately short and tightly
# bounded: a long backoff would make the next customer email wait for no
# reason, which is exactly the latency this module exists to remove.
_RECONNECT_DELAY_SECONDS = 0.25
_RECONNECT_MAX_DELAY_SECONDS = 2.0

# Pacing after a connection that logged in fine but then failed at the IDLE
# level (refused by closing, dropped straight away). Every retry here is a full
# login, so unlike a network failure it cannot be retried every couple of
# seconds: a server stuck in that state would see hundreds of logins a minute,
# which is how a mailbox gets rate-limited or locked.
#
# The first quick failures still reconnect immediately, so an ordinary one-off
# drop costs nothing. Only a *streak* is paced, doubling from the base up to
# the cap - one login per cap seconds at worst, while the poller's fallback
# poll keeps mail moving in the meantime.
_IDLE_FAILURE_TOLERANCE = 2
_IDLE_FAILURE_BASE_SECONDS = 1.0
_IDLE_FAILURE_MAX_SECONDS = 30.0
# An IDLE session that stayed up at least this long before dropping was
# healthy: its drop is an ordinary transient failure and starts a new streak
# rather than extending the old one.
_HEALTHY_IDLE_SECONDS = 10.0

# Keepalive floor. RFC 2177 allows 29 minutes; below ~30s re-issuing IDLE is
# pure overhead and only risks tripping a server's rate limit.
_MIN_KEEPALIVE_SECONDS = 30

# Untagged responses that mean "the mailbox gained a message". FETCH (flag
# churn) and EXPUNGE are deliberately excluded here: they cannot carry new
# mail, and imaplib uses 'FETCH' for its own command results.
_ACTIVITY_KEYS = ("EXISTS", "RECENT")


class ImapIdleConnection:
    """One authenticated, mailbox-selected IMAP connection, usually idling.

    Not thread-safe by design: the owning provider serialises access, because
    an IMAP connection is a single command stream and interleaving a SEARCH
    with an in-flight IDLE would corrupt it.
    """

    def __init__(
        self,
        connect: Callable[[], imaplib.IMAP4],
        mailbox: str,
        keepalive_seconds: int,
    ) -> None:
        self._connect = connect
        self._mailbox = mailbox
        self._keepalive_seconds = max(_MIN_KEEPALIVE_SECONDS, keepalive_seconds)
        self._conn: imaplib.IMAP4 | None = None
        self._idling = False
        self._idle_tag: bytes | None = None
        self._idle_started_at = 0.0
        # Set when the server answered IDLE with a tagged NO/BAD. Sticky: a
        # server that does not support IDLE will not start supporting it, and
        # retrying would mean a fresh login every cycle.
        self._idle_unsupported = False
        # An EXISTS that arrived in the same read as the '+ idling' reply.
        self._activity_pending = False
        # Consecutive quick failures after a successful login, and when the
        # last one happened. Kept on the instance, not per wait(), so a streak
        # is paced across cycles rather than reset every time the poller asks.
        self._quick_failures = 0
        self._last_failure_at = 0.0
        # Sticky once set: a stop requested before a wait begins must still
        # be honoured, or shutdown can hang on a thread parked in select().
        self._stop = threading.Event()
        # Handed to the caller so it can report detection latency without this
        # module knowing anything about tracing.
        self.last_notified_at: float | None = None
        self.last_idle_exit_at: float | None = None

    # ---- lifecycle -------------------------------------------------------

    @property
    def connection(self) -> imaplib.IMAP4 | None:
        """The live connection, or None. Callers must not assume it is idling."""
        return self._conn

    @property
    def idle_supported(self) -> bool:
        return not self._idle_unsupported

    def request_stop(self) -> None:
        """Ask any in-flight wait to return promptly.

        asyncio cannot cancel a thread blocked in select(), so shutdown has to
        be cooperative: the wait loop checks this between slices.
        """
        self._stop.set()

    def close(self) -> None:
        self._stop.set()
        conn, self._conn = self._conn, None
        was_idling, self._idling = self._idling, False
        if conn is None:
            return
        if was_idling:
            try:
                conn.send(b"DONE\r\n")
            except Exception:
                pass
        for step in (conn.close, conn.logout):
            try:
                step()
            except Exception:
                pass

    def _ensure_connected(self) -> str:
        """'ok', 'network' (retry fast) or 'auth' (do not hammer)."""
        if self._conn is not None:
            return "ok"
        try:
            conn = self._connect()
            conn.select(self._mailbox)  # read-write: acknowledgement sets \Seen
        except imaplib.IMAP4.error as exc:
            # A rejected LOGIN. Retrying every couple of seconds with a wrong
            # password is how a mailbox gets locked, so this path waits out
            # the whole cycle instead.
            logger.warning("IMAP IDLE login rejected: %s", type(exc).__name__)
            return "auth"
        except Exception as exc:
            # Never the credentials, only the failure class.
            logger.warning("IMAP IDLE connect failed: %s", type(exc).__name__)
            return "network"
        self._conn = conn
        self._idling = False
        self._idle_tag = None
        logger.info("IMAP IDLE connection established")
        return "ok"

    def _drop(self, reason: str) -> None:
        now = time.monotonic()
        # Did this session prove itself before failing? A long-lived IDLE that
        # finally drops is ordinary network life and starts a fresh streak; a
        # connection that fails straight after login extends the streak.
        healthy = self._idling and now - self._idle_started_at >= _HEALTHY_IDLE_SECONDS
        self._quick_failures = 1 if healthy else self._quick_failures + 1
        self._last_failure_at = now
        logger.warning(
            "IMAP IDLE connection dropped (%s); will reconnect (failure streak %d)",
            reason,
            self._quick_failures,
        )
        conn, self._conn = self._conn, None
        self._idling = False
        self._idle_tag = None
        if conn is not None:
            for step in (conn.close, conn.logout):
                try:
                    step()
                except Exception:
                    pass

    def _reconnect_pause(self) -> float:
        """Seconds still to wait before the next login, given the streak.

        Measured from the last failure rather than served per wait() call, so
        a pause already sat out in a previous cycle is not repeated.
        """
        streak = self._quick_failures
        if streak < _IDLE_FAILURE_TOLERANCE:
            return 0.0
        backoff = min(
            _IDLE_FAILURE_BASE_SECONDS * 2 ** (streak - _IDLE_FAILURE_TOLERANCE),
            _IDLE_FAILURE_MAX_SECONDS,
        )
        return max(0.0, backoff - (time.monotonic() - self._last_failure_at))

    # ---- activity that arrived outside IDLE ---------------------------------

    def _take_untagged_activity(self, conn: imaplib.IMAP4) -> bool:
        """Consume any EXISTS/RECENT imaplib filed away from earlier commands."""
        responses = getattr(conn, "untagged_responses", None)
        if not responses:
            return False
        found = False
        for key in _ACTIVITY_KEYS:
            if responses.pop(key, None):
                found = True
        return found

    # ---- IDLE ------------------------------------------------------------

    def _enter_idle(self) -> bool:
        conn = self._conn
        if conn is None:
            return False
        try:
            self._idle_tag = conn._new_tag()
            conn.send(b"%s IDLE\r\n" % self._idle_tag)
            # The server may legitimately push untagged updates before the
            # continuation. Read through them rather than mistaking the first
            # one for a refusal.
            for _ in range(32):
                line = conn.readline()
                if not line:
                    self._drop("connection closed entering IDLE")
                    return False
                if line.startswith(b"+"):
                    self._idling = True
                    self._idle_started_at = time.monotonic()
                    return True
                if line.startswith(b"*"):
                    upper = line.upper()
                    if any(key.encode() in upper for key in _ACTIVITY_KEYS):
                        self._activity_pending = True
                    continue
                # Tagged NO/BAD: the server will not IDLE. Remember that and
                # keep the connection for ordinary fetches.
                logger.warning("IMAP server refused IDLE; falling back to polling")
                self._idle_unsupported = True
                self._idle_tag = None
                return False
            self._drop("no continuation after IDLE")
            return False
        except Exception as exc:
            self._drop(f"enter-idle {type(exc).__name__}")
            return False

    def exit_idle(self) -> None:
        """Leave IDLE so ordinary commands can run. One round trip (~40ms)."""
        conn = self._conn
        if conn is None or not self._idling:
            self._idling = False
            return
        try:
            conn.send(b"DONE\r\n")
            # Drain until the tagged completion. Untagged lines on the way are
            # only ever hints; the caller's SEARCH is about to look anyway.
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                line = conn.readline()
                if not line:
                    self._drop("connection closed during DONE")
                    return
                if self._idle_tag is not None and line.startswith(self._idle_tag):
                    break
            self._idling = False
            self._idle_tag = None
            self.last_idle_exit_at = time.monotonic()
            # A clean IDLE/DONE round trip: the connection works.
            self._quick_failures = 0
        except Exception as exc:
            self._drop(f"exit-idle {type(exc).__name__}")

    def _readable(self, conn: imaplib.IMAP4, seconds: float) -> bool:
        sock = conn.socket()
        # An SSL socket can hold already-decrypted bytes that select() will
        # never report, so check that first or a notification can sit unseen
        # until the next slice.
        pending = getattr(sock, "pending", None)
        if pending is not None and pending():
            return True
        try:
            readable, _, _ = select.select([sock], [], [], seconds)
        except (OSError, ValueError):
            return False
        return bool(readable)

    def _consume_notification(self, conn: imaplib.IMAP4) -> bool:
        """Read one pushed line. True if it looks like new mail."""
        try:
            line = conn.readline()
        except Exception as exc:
            self._drop(f"read {type(exc).__name__}")
            return False
        if not line:
            self._drop("connection closed while idling")
            return False
        # EXISTS/RECENT mean new mail. Dovecot's periodic "* OK Still here"
        # and flag changes from other clients are ignored, so they cannot
        # cause empty wake-ups.
        upper = line.upper()
        return any(key.encode() in upper for key in _ACTIVITY_KEYS)

    def _notify(self) -> bool:
        self.last_notified_at = time.monotonic()
        # The server delivered a notification: the connection works.
        self._quick_failures = 0
        return True

    # ---- the wait --------------------------------------------------------

    def wait(self, timeout: float) -> bool:
        """Wait up to ``timeout`` for activity. True if something arrived.

        Returns as soon as the server says anything - there is no added delay,
        no debounce and no batching window on this path.
        """
        deadline = time.monotonic() + max(0.0, timeout)
        delay = _RECONNECT_DELAY_SECONDS

        while not self._stop.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False

            if self._conn is None:
                # A streak of connections that logged in and then failed at
                # the IDLE level: pace the next login instead of hammering.
                pause = self._reconnect_pause()
                if pause > 0:
                    if self._stop.wait(min(pause, remaining)):
                        return False
                    continue  # re-check the deadline before logging in

            status = self._ensure_connected()
            if status == "auth":
                self._stop.wait(remaining)
                return False
            if status == "network":
                # Short, bounded retry: a customer email must not wait on a
                # long backoff. Capped so a hard outage does not spin.
                if self._stop.wait(min(delay, remaining)):
                    return False
                delay = min(delay * 2, _RECONNECT_MAX_DELAY_SECONDS)
                continue
            delay = _RECONNECT_DELAY_SECONDS

            conn = self._conn
            if conn is None:
                continue

            if not self._idling:
                # Anything that arrived while we were busy processing.
                if self._take_untagged_activity(conn):
                    return self._notify()
                if self._idle_unsupported:
                    # Plain polling on a reused connection: wait it out.
                    self._stop.wait(remaining)
                    return False
                if not self._enter_idle():
                    continue
                if self._activity_pending:
                    self._activity_pending = False
                    return self._notify()

            # Re-issue IDLE periodically. RFC 2177 allows 29 minutes, but NAT
            # and server-side idle timeouts bite far sooner, and a silently
            # dead connection is the one failure this design must avoid.
            if time.monotonic() - self._idle_started_at >= self._keepalive_seconds:
                self.exit_idle()
                continue

            if self._readable(conn, min(_WAIT_SLICE_SECONDS, remaining)):
                if self._consume_notification(conn):
                    return self._notify()
                # An unrecognised line, or a drop: loop round. If the
                # connection died, _ensure_connected rebuilds it next pass.

        return False

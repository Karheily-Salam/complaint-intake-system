"""Real email provider: IMAP polling for inbound, SMTP for outbound.

Deliberately built on the standard library only (``imaplib``, ``smtplib``,
``email``) - no extra dependency, and both protocols are supported by every
mainstream mailbox provider (Gmail, Zoho, Yandex, a hosting provider's own
mailbox, ...) via a plain "app password", with no OAuth app registration and
no domain/DNS/webhook setup required. See SERVER.md / README.md for why this
was chosen over Gmail API, Microsoft Graph, or an inbound-webhook provider.

Failure handling: a network/auth error here is allowed to raise - the caller
(``EmailPoller`` / ``IntakeService``) is responsible for not acknowledging a
message it could not fully process, so the natural retry is "try again on the
next poll interval". No bespoke backoff is implemented; at this scale a fixed
poll interval is a perfectly adequate retry mechanism and adding one would be
speculative complexity.
"""

from __future__ import annotations

import asyncio
import email
import email.policy
import html
import imaplib
import re
import smtplib
import threading
from collections import OrderedDict
from email.message import EmailMessage
from email.utils import make_msgid, parseaddr

from app.core import timing
from app.core.logging import get_logger
from app.email.base import EmailProvider, InboundEmail, OutboundEmail, SentEmail
from app.email.idle import ImapIdleConnection

logger = get_logger(__name__)

# How many recently-fetched message-id -> UID pairs to keep. One poll cycle's
# worth is all that is normally needed; the bound only matters for entries
# that never get acknowledged.
MAX_TRACKED_UIDS = 1000


class ImapSmtpEmailProvider(EmailProvider):
    name = "imap_smtp"

    def __init__(
        self,
        *,
        imap_host: str,
        imap_port: int,
        imap_username: str,
        imap_password: str,
        imap_use_ssl: bool,
        imap_mailbox: str,
        smtp_host: str,
        smtp_port: int,
        smtp_username: str,
        smtp_password: str,
        smtp_use_ssl: bool,
        smtp_use_tls: bool,
        smtp_from_addr: str,
        mail_domain: str,
        imap_timeout: int = 30,
        smtp_timeout: int = 30,
        idle_enabled: bool = False,
        idle_keepalive_seconds: int = 300,
        uid_cache_size: int = MAX_TRACKED_UIDS,
    ) -> None:
        self.imap_host = imap_host
        self.imap_port = imap_port
        self.imap_username = imap_username
        self.imap_password = imap_password
        self.imap_use_ssl = imap_use_ssl
        self.imap_mailbox = imap_mailbox
        self.imap_timeout = imap_timeout

        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.smtp_username = smtp_username
        self.smtp_password = smtp_password
        self.smtp_use_ssl = smtp_use_ssl
        self.smtp_use_tls = smtp_use_tls
        self.smtp_from_addr = smtp_from_addr
        self.mail_domain = mail_domain
        self.smtp_timeout = smtp_timeout

        # message_id -> IMAP UID, populated by fetch_new() and consumed by
        # mark_processed(). UIDs (not sequence numbers) are used throughout
        # because sequence numbers are only valid for the lifetime of a
        # single connection - fetch_new() and mark_processed() each open
        # their own.
        #
        # Bounded: this provider is a long-lived singleton, and an entry whose
        # message never gets acknowledged (a permanent failure, say) would
        # otherwise sit here for the life of the process. Oldest entries are
        # evicted first; losing one only means the message is fetched again
        # and recognised as already processed by the database.
        self._uid_by_message_id: OrderedDict[str, bytes] = OrderedDict()
        self._uid_cache_size = uid_cache_size

        # IDLE is opt-in and lazily created: nothing is connected until the
        # poller first waits, so constructing a provider stays side-effect
        # free (which the tests rely on).
        self._idle_enabled = idle_enabled
        self._idle_keepalive_seconds = idle_keepalive_seconds
        self._idle: ImapIdleConnection | None = None
        # One IMAP connection is a single command stream, so waiting and
        # fetching must never overlap on it.
        self._imap_lock = threading.Lock()

    # ---- EmailProvider (async surface) ----
    #
    # imaplib and smtplib are blocking, and everything calling these runs on
    # the asyncio event loop - the poller, and the FastAPI app sharing that
    # loop. Running them inline would stall every HTTP request for the
    # duration of a mailbox round trip, and a blackholed connection would stop
    # the whole application rather than just this poll. Each blocking body is
    # therefore executed in a worker thread.

    async def fetch_new(self) -> list[InboundEmail]:
        return await asyncio.to_thread(self._fetch_new_blocking)

    async def wait_for_activity(self, timeout: float) -> bool:
        """Block in IMAP IDLE until the server reports activity.

        Falls back to the base class's plain wait when IDLE is disabled, so
        turning the flag off restores the previous fixed-interval behaviour
        exactly.
        """
        if not self._idle_enabled:
            return await super().wait_for_activity(timeout)
        return await asyncio.to_thread(self._wait_for_activity_blocking, timeout)

    async def shutdown(self) -> None:
        idle = self._idle
        if idle is None:
            return
        # Unblock the waiting thread first; only then tear the socket down.
        idle.request_stop()
        await asyncio.to_thread(self._close_idle_blocking, idle)
        self._idle = None

    def _close_idle_blocking(self, idle: ImapIdleConnection) -> None:
        # The waiting thread notices the stop within one select slice and
        # releases the lock. Closing without it would pull the socket out from
        # under that thread; the timeout keeps a wedged thread from blocking
        # shutdown indefinitely.
        acquired = self._imap_lock.acquire(timeout=5)
        try:
            idle.close()
        finally:
            if acquired:
                self._imap_lock.release()

    async def mark_processed(self, message_id: str) -> None:
        await asyncio.to_thread(self._mark_processed_blocking, message_id)

    async def send(self, email_out: OutboundEmail) -> SentEmail:
        timing.mark(timing.SMTP_STARTED)
        try:
            return await asyncio.to_thread(self._send_blocking, email_out)
        finally:
            timing.mark(timing.SMTP_FINISHED)

    # ---- blocking implementations ----

    def _idle_connection(self) -> ImapIdleConnection:
        if self._idle is None:
            self._idle = ImapIdleConnection(
                connect=self._imap_connect,
                mailbox=self.imap_mailbox,
                keepalive_seconds=self._idle_keepalive_seconds,
            )
        return self._idle

    def _wait_for_activity_blocking(self, timeout: float) -> bool:
        with self._imap_lock:
            return self._idle_connection().wait(timeout)

    def _search_and_fetch(self, conn: imaplib.IMAP4) -> list[InboundEmail]:
        """UID SEARCH UNSEEN + fetch, on an already-selected connection."""
        status, data = conn.uid("search", None, "UNSEEN")
        if status != "OK" or not data or not data[0]:
            return []
        emails: list[InboundEmail] = []
        for uid in data[0].split():
            parsed = self._fetch_one(conn, uid)
            if parsed is not None:
                self._remember_uid(parsed.message_id, uid)
                emails.append(parsed)
        return emails

    def _fetch_new_blocking(self) -> list[InboundEmail]:
        # Reuse the already authenticated, already selected IDLE connection
        # when there is one. Leaving IDLE is a single round trip; a fresh
        # connection would cost a TLS handshake plus LOGIN plus SELECT, which
        # is the bulk of the detection-to-fetch latency.
        if self._idle_enabled and self._idle is not None:
            with self._imap_lock:
                idle = self._idle
                idle.exit_idle()
                timing.mark(timing.IDLE_EXITED)
                conn = idle.connection
                if conn is not None:
                    try:
                        return self._search_and_fetch(conn)
                    except Exception:
                        # Drop the session and fall through to a clean
                        # connection rather than failing this cycle.
                        idle.close()
                        self._idle = None

        conn = self._imap_connect()
        try:
            conn.select(self.imap_mailbox)
            return self._search_and_fetch(conn)
        finally:
            self._imap_disconnect(conn)

    def _remember_uid(self, message_id: str, uid: bytes) -> None:
        self._uid_by_message_id[message_id] = uid
        self._uid_by_message_id.move_to_end(message_id)
        while len(self._uid_by_message_id) > self._uid_cache_size:
            self._uid_by_message_id.popitem(last=False)

    def _mark_processed_blocking(self, message_id: str) -> None:
        uid = self._uid_by_message_id.pop(message_id, None)
        if uid is None:
            logger.warning("mark_processed called for unknown message_id (already handled?)")
            return
        if self._idle_enabled and self._idle is not None:
            with self._imap_lock:
                idle = self._idle
                idle.exit_idle()
                conn = idle.connection
                if conn is not None:
                    try:
                        conn.uid("store", uid, "+FLAGS", r"(\Seen)")
                        return
                    except Exception:
                        idle.close()
                        self._idle = None

        conn = self._imap_connect()
        try:
            conn.select(self.imap_mailbox)
            conn.uid("store", uid, "+FLAGS", r"(\Seen)")
        finally:
            self._imap_disconnect(conn)

    def _send_blocking(self, email_out: OutboundEmail) -> SentEmail:
        msg = EmailMessage()
        sender = email_out.from_addr or self.smtp_from_addr
        msg["From"] = sender
        msg["To"] = email_out.to_addr
        msg["Subject"] = email_out.subject
        # If mail is sent from something other than the mailbox we poll (a
        # relay identity, say), the customer's reply would land somewhere
        # nothing reads. Reply-To steers it back to the polled mailbox.
        if self.imap_username and sender.lower() != self.imap_username.lower():
            msg["Reply-To"] = self.imap_username
        message_id = make_msgid(domain=self.mail_domain)
        msg["Message-ID"] = message_id
        if email_out.in_reply_to:
            msg["In-Reply-To"] = email_out.in_reply_to
        if email_out.references:
            msg["References"] = " ".join(email_out.references)
        # RFC 3834: mark our own mail as automatic so that other responders
        # (and our own poller, if a copy ever comes back) do not reply to it.
        msg["Auto-Submitted"] = "auto-replied"
        msg.set_content(email_out.body, charset="utf-8")

        server = self._smtp_connect()
        try:
            server.send_message(msg)
        finally:
            try:
                server.quit()
            except Exception:
                pass

        return SentEmail(
            message_id=message_id,
            to_addr=email_out.to_addr,
            subject=email_out.subject,
            thread_id=email_out.thread_id,
        )

    # ---- connection helpers ----

    def _imap_connect(self) -> imaplib.IMAP4:
        # An explicit timeout is essential, not tidiness: without one the
        # socket blocks forever if the server accepts the connection and then
        # never answers, which is exactly what a silently-dropping firewall
        # produces. imaplib's default is no timeout at all.
        if self.imap_use_ssl:
            conn: imaplib.IMAP4 = imaplib.IMAP4_SSL(
                self.imap_host, self.imap_port, timeout=self.imap_timeout
            )
        else:
            conn = imaplib.IMAP4(self.imap_host, self.imap_port, timeout=self.imap_timeout)
            conn.starttls()
        conn.login(self.imap_username, self.imap_password)
        return conn

    @staticmethod
    def _imap_disconnect(conn: imaplib.IMAP4) -> None:
        try:
            conn.close()
        except Exception:
            pass
        try:
            conn.logout()
        except Exception:
            pass

    def _smtp_connect(self) -> smtplib.SMTP:
        if self.smtp_use_ssl:
            server: smtplib.SMTP = smtplib.SMTP_SSL(
                self.smtp_host, self.smtp_port, timeout=self.smtp_timeout
            )
        else:
            server = smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=self.smtp_timeout)
            if self.smtp_use_tls:
                server.starttls()
        server.login(self.smtp_username, self.smtp_password)
        return server

    # ---- parsing ----

    def _fetch_one(self, conn: imaplib.IMAP4, uid: bytes) -> InboundEmail | None:
        # BODY.PEEK[], never RFC822 or BODY[]: on a read-write mailbox those
        # set \Seen as a side effect of the fetch itself (RFC 3501 6.4.5). The
        # message would then be marked read before its transaction commits, so
        # a failed turn could never be retried - UID SEARCH UNSEEN would not
        # return it again. Only mark_processed() may set \Seen.
        status, data = conn.uid("fetch", uid, "(BODY.PEEK[])")
        if status != "OK" or not data or data[0] is None:
            logger.warning("Failed to fetch IMAP message uid=%s", uid)
            return None

        raw = data[0][1]
        msg = email.message_from_bytes(raw, policy=email.policy.default)

        message_id = (msg["Message-ID"] or "").strip()
        if not message_id:
            # Extremely rare (a message with no Message-ID header at all) -
            # synthesize a stable one from the UID so idempotency still
            # works, rather than silently dropping the email.
            message_id = f"<no-msgid-uid-{uid.decode()}@{self.mail_domain}>"

        _, from_addr = parseaddr(msg["From"] or "")
        _, to_addr = parseaddr(msg["To"] or "")
        subject = msg.get("Subject")
        in_reply_to = (msg["In-Reply-To"] or "").strip() or None
        references_raw = (msg["References"] or "").strip()
        references = references_raw.split() if references_raw else []

        body = self._extract_body(msg)

        return InboundEmail(
            message_id=message_id,
            from_addr=from_addr or "unknown@unknown.invalid",
            to_addr=to_addr or self.smtp_from_addr,
            subject=subject,
            body=body,
            in_reply_to=in_reply_to,
            references=references,
            auto_submitted=(msg["Auto-Submitted"] or "").strip() or None,
            precedence=(msg["Precedence"] or "").strip() or None,
        )

    @staticmethod
    def _extract_body(msg: email.message.Message) -> str:
        """Prefer the text/plain alternative; fall back to stripped text/html."""
        if msg.is_multipart():
            plain_part = None
            html_part = None
            for part in msg.walk():
                if part.get_content_disposition() == "attachment":
                    continue
                if part.get_content_type() == "text/plain" and plain_part is None:
                    plain_part = part
                elif part.get_content_type() == "text/html" and html_part is None:
                    html_part = part
            chosen = plain_part or html_part
            if chosen is None:
                return ""
            content = chosen.get_content()
            is_html = chosen.get_content_type() == "text/html"
        else:
            content = msg.get_content()
            is_html = msg.get_content_type() == "text/html"

        if is_html:
            content = _strip_html(content)
        return content.strip()


def _strip_html(raw_html: str) -> str:
    """Minimal, dependency-free tag stripper for a text/html-only fallback body.

    Not a general HTML sanitizer - only used when a mail client sent no
    text/plain alternative at all, purely so the extractor still has readable
    text to work with.
    """
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", raw_html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p\s*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text)

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

import email
import email.policy
import html
import imaplib
import re
import smtplib
from email.message import EmailMessage
from email.utils import make_msgid, parseaddr

from app.core.logging import get_logger
from app.email.base import EmailProvider, InboundEmail, OutboundEmail, SentEmail

logger = get_logger(__name__)


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
    ) -> None:
        self.imap_host = imap_host
        self.imap_port = imap_port
        self.imap_username = imap_username
        self.imap_password = imap_password
        self.imap_use_ssl = imap_use_ssl
        self.imap_mailbox = imap_mailbox

        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.smtp_username = smtp_username
        self.smtp_password = smtp_password
        self.smtp_use_ssl = smtp_use_ssl
        self.smtp_use_tls = smtp_use_tls
        self.smtp_from_addr = smtp_from_addr
        self.mail_domain = mail_domain

        # message_id -> IMAP UID, populated by fetch_new() and consumed by
        # mark_processed(). UIDs (not sequence numbers) are used throughout
        # because sequence numbers are only valid for the lifetime of a
        # single connection - fetch_new() and mark_processed() each open
        # their own.
        self._uid_by_message_id: dict[str, bytes] = {}

    # ---- EmailProvider ----

    async def fetch_new(self) -> list[InboundEmail]:
        conn = self._imap_connect()
        try:
            conn.select(self.imap_mailbox)
            status, data = conn.uid("search", None, "UNSEEN")
            if status != "OK" or not data or not data[0]:
                return []

            emails: list[InboundEmail] = []
            for uid in data[0].split():
                parsed = self._fetch_one(conn, uid)
                if parsed is not None:
                    self._uid_by_message_id[parsed.message_id] = uid
                    emails.append(parsed)
            return emails
        finally:
            self._imap_disconnect(conn)

    async def mark_processed(self, message_id: str) -> None:
        uid = self._uid_by_message_id.pop(message_id, None)
        if uid is None:
            logger.warning("mark_processed called for unknown message_id (already handled?)")
            return
        conn = self._imap_connect()
        try:
            conn.select(self.imap_mailbox)
            conn.uid("store", uid, "+FLAGS", r"(\Seen)")
        finally:
            self._imap_disconnect(conn)

    async def send(self, email_out: OutboundEmail) -> SentEmail:
        msg = EmailMessage()
        msg["From"] = email_out.from_addr or self.smtp_from_addr
        msg["To"] = email_out.to_addr
        msg["Subject"] = email_out.subject
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
        if self.imap_use_ssl:
            conn: imaplib.IMAP4 = imaplib.IMAP4_SSL(self.imap_host, self.imap_port)
        else:
            conn = imaplib.IMAP4(self.imap_host, self.imap_port)
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
            server: smtplib.SMTP = smtplib.SMTP_SSL(self.smtp_host, self.smtp_port, timeout=30)
        else:
            server = smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=30)
            if self.smtp_use_tls:
                server.starttls()
        server.login(self.smtp_username, self.smtp_password)
        return server

    # ---- parsing ----

    def _fetch_one(self, conn: imaplib.IMAP4, uid: bytes) -> InboundEmail | None:
        status, data = conn.uid("fetch", uid, "(RFC822)")
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

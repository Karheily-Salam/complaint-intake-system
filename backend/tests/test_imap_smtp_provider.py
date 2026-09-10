"""Unit tests for the real IMAP/SMTP provider.

No network: a stub IMAP connection replays canned RFC 5322 bytes and a stub
SMTP server captures the composed message, so parsing, threading headers,
UTF-8 handling and the "never acknowledge during fetch" rule are all covered
without a mailbox.
"""

from __future__ import annotations

import pytest

from app.core.config import Settings
from app.email.providers.imap_smtp import ImapSmtpEmailProvider

RAW_PLAIN = b"""\
Message-ID: <customer-abc123@mail.example.com>
From: Jane Doe <jane@example.com>
To: complaints@example.com
Subject: Withdrawal stuck
In-Reply-To: <ours-1@complaints.example.com>
References: <ours-0@complaints.example.com> <ours-1@complaints.example.com>
Content-Type: text/plain; charset="utf-8"
Content-Transfer-Encoding: 8bit

The transaction id is TXN-9f3a12bc.
"""

ARABIC_BODY = "مرحبا، لدي مشكلة."

RAW_MULTIPART = (
    b"""\
Message-ID: <multi-1@mail.example.com>
From: =?utf-8?B?2LnZhNmK?= <ali@example.com>
To: complaints@example.com
Subject: =?utf-8?B?2YXYtNmD2YTYqQ==?=
MIME-Version: 1.0
Content-Type: multipart/alternative; boundary="BOUND"

--BOUND
Content-Type: text/plain; charset="utf-8"
Content-Transfer-Encoding: 8bit

"""
    + ARABIC_BODY.encode("utf-8")
    + b"""
--BOUND
Content-Type: text/html; charset="utf-8"
Content-Transfer-Encoding: 8bit

<html><body><p>ignored</p></body></html>
--BOUND--
"""
)

RAW_HTML_ONLY = b"""\
Message-ID: <html-1@mail.example.com>
From: html@example.com
To: complaints@example.com
Subject: Withdrawal
Content-Type: text/html; charset="utf-8"

<html><body><p>My withdrawal &amp; deposit both failed.</p><br>User U-1</body></html>
"""

RAW_NO_MESSAGE_ID = b"""\
From: nomsgid@example.com
To: complaints@example.com
Subject: Withdrawal

No Message-ID header at all.
"""


class StubImap:
    """Minimal stand-in for imaplib.IMAP4, recording every command issued."""

    def __init__(self, messages: dict[bytes, bytes]) -> None:
        self.messages = messages
        self.commands: list[tuple] = []
        self.selected: str | None = None

    def select(self, mailbox):
        self.selected = mailbox
        return "OK", [b"1"]

    def uid(self, command, *args):
        self.commands.append((command.lower(), *args))
        if command.lower() == "search":
            return "OK", [b" ".join(self.messages)]
        if command.lower() == "fetch":
            uid = args[0]
            return "OK", [(b"1 (RFC822 {n}", self.messages[uid])]
        if command.lower() == "store":
            return "OK", [b"1"]
        raise AssertionError(f"unexpected IMAP command {command}")

    def close(self):
        pass

    def logout(self):
        pass


class StubSmtp:
    def __init__(self) -> None:
        self.sent = []

    def send_message(self, msg):
        self.sent.append(msg)

    def quit(self):
        pass


def make_provider(**overrides) -> ImapSmtpEmailProvider:
    kwargs = dict(
        imap_host="imap.example.com",
        imap_port=993,
        imap_username="bot@example.com",
        imap_password="not-a-real-password",
        imap_use_ssl=True,
        imap_mailbox="INBOX",
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_username="bot@example.com",
        smtp_password="not-a-real-password",
        smtp_use_ssl=False,
        smtp_use_tls=True,
        smtp_from_addr="complaints@example.com",
        mail_domain="complaints.example.com",
    )
    kwargs.update(overrides)
    return ImapSmtpEmailProvider(**kwargs)


@pytest.fixture()
def provider_with_inbox(monkeypatch):
    def _build(messages: dict[bytes, bytes]):
        provider = make_provider()
        stub = StubImap(messages)
        monkeypatch.setattr(provider, "_imap_connect", lambda: stub)
        return provider, stub

    return _build


# ------------------------------------------------------------------- inbound


async def test_parses_headers_and_plain_body(provider_with_inbox):
    provider, stub = provider_with_inbox({b"7": RAW_PLAIN})
    emails = await provider.fetch_new()

    assert len(emails) == 1
    email = emails[0]
    assert email.message_id == "<customer-abc123@mail.example.com>"
    assert email.from_addr == "jane@example.com"
    assert email.to_addr == "complaints@example.com"
    assert email.subject == "Withdrawal stuck"
    assert email.in_reply_to == "<ours-1@complaints.example.com>"
    assert email.references == [
        "<ours-0@complaints.example.com>",
        "<ours-1@complaints.example.com>",
    ]
    assert email.body == "The transaction id is TXN-9f3a12bc."


async def test_fetch_does_not_acknowledge_but_mark_processed_does(provider_with_inbox):
    provider, stub = provider_with_inbox({b"7": RAW_PLAIN})
    await provider.fetch_new()

    # Nothing may be flagged during fetch - only after the caller persisted it.
    assert not any(cmd[0] == "store" for cmd in stub.commands)

    await provider.mark_processed("<customer-abc123@mail.example.com>")
    store = [cmd for cmd in stub.commands if cmd[0] == "store"]
    assert store == [("store", b"7", "+FLAGS", r"(\Seen)")]


async def test_prefers_plain_text_alternative_and_decodes_utf8(provider_with_inbox):
    provider, _ = provider_with_inbox({b"1": RAW_MULTIPART})
    email = (await provider.fetch_new())[0]

    assert email.subject == "مشكلة"
    assert email.body == ARABIC_BODY
    assert "ignored" not in email.body


async def test_html_only_body_is_stripped_to_text(provider_with_inbox):
    provider, _ = provider_with_inbox({b"1": RAW_HTML_ONLY})
    email = (await provider.fetch_new())[0]

    assert "<p>" not in email.body
    assert "My withdrawal & deposit both failed." in email.body
    assert "User U-1" in email.body


async def test_message_without_message_id_gets_a_stable_synthetic_one(provider_with_inbox):
    provider, _ = provider_with_inbox({b"42": RAW_NO_MESSAGE_ID})
    first = (await provider.fetch_new())[0]
    second = (await provider.fetch_new())[0]

    # Stable across fetches, so idempotency still holds for such a message.
    assert first.message_id == second.message_id
    assert "42" in first.message_id


# ------------------------------------------------------------------ outbound


async def test_send_sets_threading_headers_and_utf8_body(monkeypatch):
    provider = make_provider()
    smtp = StubSmtp()
    monkeypatch.setattr(provider, "_smtp_connect", lambda: smtp)

    from app.email.base import OutboundEmail

    sent = await provider.send(
        OutboundEmail(
            to_addr="jane@example.com",
            subject="Re: Withdrawal stuck [Ref:abc123]",
            body="Здравствуйте! Пожалуйста, укажите ваш User ID.",
            in_reply_to="<customer-abc123@mail.example.com>",
            references=["<ours-0@x>", "<customer-abc123@mail.example.com>"],
        )
    )

    assert len(smtp.sent) == 1
    msg = smtp.sent[0]
    assert msg["To"] == "jane@example.com"
    assert msg["From"] == "complaints@example.com"
    assert msg["In-Reply-To"] == "<customer-abc123@mail.example.com>"
    assert msg["References"] == "<ours-0@x> <customer-abc123@mail.example.com>"
    assert msg["Message-ID"].endswith("@complaints.example.com>")
    # The Message-ID we report back is the one we sent, so a later reply can
    # be threaded to this conversation.
    assert sent.message_id == msg["Message-ID"]
    assert "Пожалуйста" in msg.get_content()


# -------------------------------------------------------------- configuration


def test_missing_credentials_are_reported_by_name_only():
    settings = Settings(
        email_provider="imap_smtp",
        imap_host="imap.example.com",
        imap_username="bot@example.com",
        imap_password=None,
        smtp_host=None,
        smtp_username="bot@example.com",
        smtp_password="present",
    )
    missing = settings.missing_email_settings()

    assert set(missing) == {"IMAP_PASSWORD", "SMTP_HOST"}
    # Names only - a configured secret must never leak into this list.
    assert "present" not in " ".join(missing)


def test_mock_provider_needs_no_mail_credentials():
    assert Settings(email_provider="mock").missing_email_settings() == []


def test_factory_refuses_half_configured_provider(monkeypatch):
    from app.email import factory

    monkeypatch.setattr(factory.settings, "email_provider", "imap_smtp")
    monkeypatch.setattr(factory.settings, "imap_host", None)
    monkeypatch.setattr(factory.settings, "imap_username", None)
    monkeypatch.setattr(factory.settings, "imap_password", None)
    monkeypatch.setattr(factory.settings, "smtp_host", None)
    monkeypatch.setattr(factory.settings, "smtp_username", None)
    monkeypatch.setattr(factory.settings, "smtp_password", None)
    factory.get_email_provider.cache_clear()
    try:
        with pytest.raises(ValueError) as exc:
            factory.get_email_provider()
        message = str(exc.value)
        assert "IMAP_HOST" in message and "SMTP_PASSWORD" in message
    finally:
        factory.get_email_provider.cache_clear()

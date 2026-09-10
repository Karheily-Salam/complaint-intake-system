"""Pre-flight check for the real email configuration.

Verifies that the configured mailbox actually works - *before* switching
EMAIL_PROVIDER to imap_smtp and letting the poller loose on it. Run it with
the IMAP_*/SMTP_* variables already in backend/.env while EMAIL_PROVIDER is
still "mock": the credentials are loaded, but nothing is polling yet.

    docker compose exec backend python -m scripts.check_email
    docker compose exec backend python -m scripts.check_email --send-test-to you@example.com

Checks, in order:
  1. every required variable is present (names only are ever printed)
  2. IMAP: connect, authenticate, open the mailbox, count unread messages
  3. SMTP: connect, authenticate (sends nothing)
  4. only with --send-test-to: send one real test email to that address

Credentials are never printed, logged, or included in an error message.
"""

from __future__ import annotations

import argparse
import asyncio
import imaplib
import smtplib
import sys

from app.core.config import settings


def _fail(message: str) -> None:
    print(f"  FAIL  {message}")


def _ok(message: str) -> None:
    print(f"  ok    {message}")


def check_settings() -> bool:
    print("Configuration")
    missing = settings.missing_email_settings()
    if settings.email_provider.lower() != "imap_smtp":
        # Deliberately not an error: checking the credentials *before*
        # switching the provider over is the safe order.
        print(
            f"  note  EMAIL_PROVIDER is '{settings.email_provider}'. Checking the "
            "IMAP_*/SMTP_* settings anyway."
        )
        missing = [
            name
            for name, value in {
                "IMAP_HOST": settings.imap_host,
                "IMAP_USERNAME": settings.imap_username,
                "IMAP_PASSWORD": settings.imap_password,
                "SMTP_HOST": settings.smtp_host,
                "SMTP_USERNAME": settings.smtp_username,
                "SMTP_PASSWORD": settings.smtp_password,
            }.items()
            if not value
        ]
    if missing:
        _fail(f"missing variables: {', '.join(missing)}")
        return False

    _ok(f"mailbox polled:   {settings.imap_username} ({settings.imap_host}:{settings.imap_port})")
    _ok(f"sending as:       {settings.smtp_sender} ({settings.smtp_host}:{settings.smtp_port})")
    _ok(f"tickets go to:    {settings.support_inbox_address}")

    if settings.smtp_sender.lower() != (settings.imap_username or "").lower():
        # Not fatal - the provider adds a Reply-To pointing at the polled
        # mailbox - but with most providers the From must be the authenticated
        # account anyway, so this is usually a mistake worth surfacing.
        print(
            f"  note  sending as {settings.smtp_sender}, which is not the polled "
            f"mailbox. Customer replies are steered back by Reply-To, but most "
            "providers require From to be the authenticated account: prefer "
            "leaving SMTP_FROM_ADDR unset."
        )

    if (settings.imap_username or "").lower() == settings.support_inbox_address.lower():
        # A supported single-mailbox setup, not an error: loop protection stops
        # the system answering its own notifications. Worth stating plainly,
        # though, because of how the tickets show up.
        print(
            "  note  single-mailbox setup: completed tickets are emailed to the same "
            "mailbox that is polled. Loop protection skips them (they are never "
            "read as complaints), but they arrive already marked read - look for "
            "subjects starting '[Ticket '."
        )
    return True


def check_imap() -> bool:
    print("IMAP (receiving)")
    try:
        if settings.imap_use_ssl:
            conn: imaplib.IMAP4 = imaplib.IMAP4_SSL(settings.imap_host, settings.imap_port)
        else:
            conn = imaplib.IMAP4(settings.imap_host, settings.imap_port)
            conn.starttls()
        _ok(f"connected to {settings.imap_host}:{settings.imap_port}")
    except Exception as exc:
        _fail(f"could not connect: {type(exc).__name__}: {exc}")
        return False

    try:
        conn.login(settings.imap_username, settings.imap_password)
        _ok("authenticated")
        status, _ = conn.select(settings.imap_mailbox)
        if status != "OK":
            _fail(f"mailbox {settings.imap_mailbox!r} could not be opened")
            return False
        status, data = conn.uid("search", None, "UNSEEN")
        unread = len(data[0].split()) if data and data[0] else 0
        _ok(f"mailbox {settings.imap_mailbox!r} opened, {unread} unread message(s) waiting")
        if unread:
            print(
                f"        note: the poller will process those {unread} message(s) as "
                "customer complaints once EMAIL_PROVIDER=imap_smtp."
            )
        return True
    except imaplib.IMAP4.error as exc:
        # str(exc) here is the server's rejection, never the password.
        _fail(f"login rejected: {exc}. Check IMAP_USERNAME / the app password, and that "
              "IMAP access is enabled for the account.")
        return False
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def check_smtp() -> bool:
    print("SMTP (sending)")
    try:
        if settings.smtp_use_ssl:
            server: smtplib.SMTP = smtplib.SMTP_SSL(
                settings.smtp_host, settings.smtp_port, timeout=30
            )
        else:
            server = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30)
            if settings.smtp_use_tls:
                server.starttls()
        _ok(f"connected to {settings.smtp_host}:{settings.smtp_port}")
    except Exception as exc:
        _fail(f"could not connect: {type(exc).__name__}: {exc}")
        return False

    try:
        server.login(settings.smtp_username, settings.smtp_password)
        _ok("authenticated (nothing sent)")
        return True
    except smtplib.SMTPAuthenticationError as exc:
        _fail(f"login rejected (code {exc.smtp_code}). Check SMTP_USERNAME / the app password.")
        return False
    except Exception as exc:
        _fail(f"login failed: {type(exc).__name__}: {exc}")
        return False
    finally:
        try:
            server.quit()
        except Exception:
            pass


def send_test(to_addr: str) -> bool:
    """Send one real email through the configured provider."""
    print(f"Test send -> {to_addr}")
    from app.email.base import OutboundEmail
    from app.email.providers.imap_smtp import ImapSmtpEmailProvider

    provider = ImapSmtpEmailProvider(
        imap_host=settings.imap_host,
        imap_port=settings.imap_port,
        imap_username=settings.imap_username,
        imap_password=settings.imap_password,
        imap_use_ssl=settings.imap_use_ssl,
        imap_mailbox=settings.imap_mailbox,
        smtp_host=settings.smtp_host,
        smtp_port=settings.smtp_port,
        smtp_username=settings.smtp_username,
        smtp_password=settings.smtp_password,
        smtp_use_ssl=settings.smtp_use_ssl,
        smtp_use_tls=settings.smtp_use_tls,
        smtp_from_addr=settings.smtp_sender,
        mail_domain=settings.mail_domain,
    )
    try:
        sent = asyncio.run(
            provider.send(
                OutboundEmail(
                    to_addr=to_addr,
                    subject="Complaint intake system - configuration test",
                    body=(
                        "This is a configuration test from the complaint intake "
                        "system.\n\nIf you received it, SMTP sending works.\n"
                    ),
                )
            )
        )
    except Exception as exc:
        _fail(f"send failed: {type(exc).__name__}: {exc}")
        return False
    _ok(f"sent, Message-ID {sent.message_id}")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--send-test-to",
        metavar="ADDRESS",
        help="also send one real test email to this address (use your own)",
    )
    args = parser.parse_args()

    results = [check_settings()]
    if results[0]:
        results.append(check_imap())
        results.append(check_smtp())
        if args.send_test_to and all(results):
            results.append(send_test(args.send_test_to))

    if all(results):
        print("\nAll checks passed.")
        return 0
    print("\nSome checks failed - see above. EMAIL_PROVIDER was not changed by this script.")
    return 1


if __name__ == "__main__":
    sys.exit(main())

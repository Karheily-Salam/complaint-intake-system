"""Builds the configured :class:`EmailProvider`."""

from __future__ import annotations

from functools import lru_cache

from app.core.config import settings
from app.email.base import EmailProvider


@lru_cache
def _simulated_provider() -> EmailProvider:
    from app.email.providers.mock import MockEmailProvider

    return MockEmailProvider(support_address=settings.support_inbox_address)


def get_demo_email_provider() -> EmailProvider:
    """The transport demo conversations are allowed to use.

    ``POST /inbox`` is public and takes an unverified sender address, so with a
    real transport configured it would let anyone make this system send mail to
    an address of their choosing - with values they supplied echoed back in the
    confirmation. Demo conversations therefore never reach the real transport:
    they are answered by a simulated one, which is exactly what the demo shows
    anyway (the reply is returned in the response and stored on the thread).

    When the configured provider is *already* simulated - local development,
    the test suite - that same instance is returned, so there is one mailbox to
    inspect and nothing about local behaviour changes.
    """
    configured = get_email_provider()
    return configured if configured.is_simulated else _simulated_provider()


@lru_cache
def get_email_provider() -> EmailProvider:
    provider = settings.email_provider.lower()

    if provider == "mock":
        from app.email.providers.mock import MockEmailProvider

        return MockEmailProvider(support_address=settings.support_inbox_address)

    if provider == "imap_smtp":
        missing = settings.missing_email_settings()
        if missing:
            # Names only - credential values are never included in an error.
            raise ValueError(
                "EMAIL_PROVIDER=imap_smtp requires these unset environment "
                f"variables: {', '.join(missing)}"
            )

        from app.email.providers.imap_smtp import ImapSmtpEmailProvider

        return ImapSmtpEmailProvider(
            imap_host=settings.imap_host,  # type: ignore[arg-type]
            imap_port=settings.imap_port,
            imap_username=settings.imap_username,  # type: ignore[arg-type]
            imap_password=settings.imap_password,  # type: ignore[arg-type]
            imap_use_ssl=settings.imap_use_ssl,
            imap_mailbox=settings.imap_mailbox,
            smtp_host=settings.smtp_host,  # type: ignore[arg-type]
            smtp_port=settings.smtp_port,
            smtp_username=settings.smtp_username,  # type: ignore[arg-type]
            smtp_password=settings.smtp_password,  # type: ignore[arg-type]
            smtp_use_ssl=settings.smtp_use_ssl,
            smtp_use_tls=settings.smtp_use_tls,
            smtp_from_addr=settings.smtp_sender,
            mail_domain=settings.mail_domain,
            idle_enabled=settings.email_idle_enabled,
            idle_keepalive_seconds=settings.email_idle_keepalive_seconds,
            imap_timeout=settings.imap_timeout_seconds,
            smtp_timeout=settings.smtp_timeout_seconds,
        )

    raise ValueError(
        f"Unknown EMAIL_PROVIDER '{settings.email_provider}'. Supported: mock, imap_smtp."
    )

"""In-memory email provider for local development and tests.

Simulates a real polling mailbox: ``deliver()`` injects a customer email,
``fetch_new()`` returns everything not yet acknowledged (without removing it -
mirroring a real IMAP UNSEEN search), and ``mark_processed()`` is what
actually removes an item, matching the real provider's contract (never
acknowledge before the caller has persisted the result). Outbound mail is
captured in ``sent_box``/``sent_bodies`` for assertions.
"""

from __future__ import annotations

import uuid
from collections import OrderedDict

from app.email.base import EmailProvider, InboundEmail, OutboundEmail, SentEmail


class MockEmailProvider(EmailProvider):
    name = "mock"

    def __init__(self, support_address: str) -> None:
        self.support_address = support_address
        self._inbox: OrderedDict[str, InboundEmail] = OrderedDict()
        self.sent_box: list[SentEmail] = []
        self.sent_bodies: list[OutboundEmail] = []

    # ---- simulation hooks ----

    def deliver(self, email: InboundEmail) -> None:
        """Push a customer email into the mock inbox, keyed by message_id."""
        self._inbox[email.message_id] = email

    def make_inbound(
        self,
        *,
        from_addr: str,
        body: str,
        subject: str | None = None,
        thread_id: str | None = None,
        in_reply_to: str | None = None,
        references: list[str] | None = None,
        message_id: str | None = None,
    ) -> InboundEmail:
        return InboundEmail(
            message_id=message_id or f"<in-{uuid.uuid4().hex[:12]}@mock.local>",
            from_addr=from_addr,
            to_addr=self.support_address,
            subject=subject,
            body=body,
            thread_id=thread_id,
            in_reply_to=in_reply_to,
            references=references or [],
        )

    # ---- EmailProvider ----

    async def fetch_new(self) -> list[InboundEmail]:
        return list(self._inbox.values())

    async def mark_processed(self, message_id: str) -> None:
        self._inbox.pop(message_id, None)

    async def send(self, email: OutboundEmail) -> SentEmail:
        sent = SentEmail(
            message_id=f"<out-{uuid.uuid4().hex[:12]}@mock.local>",
            to_addr=email.to_addr,
            subject=email.subject,
            thread_id=email.thread_id,
        )
        self.sent_box.append(sent)
        self.sent_bodies.append(email)
        return sent

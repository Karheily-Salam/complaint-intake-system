"""In-memory email provider for the prototype.

Simulates incoming and outgoing mail without touching Gmail, Outlook, or any
company system. Inbound mail is injected by the API (``POST /inbox``); outbound
mail is captured in ``sent_box`` and mirrored to the ``email_logs`` table by the
service layer.
"""

from __future__ import annotations

import uuid
from collections import deque

from app.email.base import EmailProvider, InboundEmail, OutboundEmail, SentEmail


class MockEmailProvider(EmailProvider):
    name = "mock"

    def __init__(self, support_address: str) -> None:
        self.support_address = support_address
        self._inbox: deque[InboundEmail] = deque()
        self.sent_box: list[SentEmail] = []
        self.sent_bodies: list[OutboundEmail] = []

    # ---- simulation hooks ----

    def deliver(self, email: InboundEmail) -> None:
        """Push a customer email into the mock inbox."""
        self._inbox.append(email)

    def make_inbound(
        self,
        *,
        from_addr: str,
        body: str,
        subject: str | None = None,
        thread_id: str | None = None,
    ) -> InboundEmail:
        return InboundEmail(
            message_id=f"in-{uuid.uuid4().hex[:12]}",
            from_addr=from_addr,
            to_addr=self.support_address,
            subject=subject,
            body=body,
            thread_id=thread_id,
        )

    # ---- EmailProvider ----

    async def fetch_new(self) -> list[InboundEmail]:
        drained = list(self._inbox)
        self._inbox.clear()
        return drained

    async def send(self, email: OutboundEmail) -> SentEmail:
        sent = SentEmail(
            message_id=f"out-{uuid.uuid4().hex[:12]}",
            to_addr=email.to_addr,
            subject=email.subject,
            thread_id=email.thread_id,
        )
        self.sent_box.append(sent)
        self.sent_bodies.append(email)
        return sent

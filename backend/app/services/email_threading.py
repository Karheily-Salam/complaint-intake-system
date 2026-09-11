"""Subject-line threading, shared by every outbound message in a conversation.

Extracted from IntakeService so the automated engine reply and a support
agent's manual reply build their subject the same way. If the two drifted, a
staff reply could lose the ``[Ref:...]`` token and the customer's response
would start a new conversation instead of continuing this one.
"""

from __future__ import annotations

import re

from app.db.models.conversation import Conversation
from app.db.models.message import Message
from app.email.base import MAX_REFERENCES

# Matches the opaque thread reference embedded in every outbound subject line.
SUBJECT_REF_RE = re.compile(r"\[Ref:([0-9a-f]{4,32})\]", re.IGNORECASE)


def thread_subject(conversation: Conversation, base: str | None = None) -> str:
    """The subject to send on ``conversation``, with Re: and the ref token.

    ``base`` overrides the stored conversation subject (a support agent may
    edit it in the composer); the threading token is re-applied either way, so
    an edited subject cannot silently break the reply chain.
    """
    subject = (base or "").strip() or conversation.subject or "Your complaint"
    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"
    if conversation.thread_token and not SUBJECT_REF_RE.search(subject):
        subject = f"{subject} [Ref:{conversation.thread_token}]"
    return subject


def subject_without_thread_token(subject: str | None) -> str:
    """A subject safe to store on a brand-new conversation.

    A reply carries the previous thread's ``[Ref:...]`` token. Storing that
    verbatim on a new conversation would be quietly wrong: thread_subject()
    treats an already-tagged subject as tagged, so the new conversation would
    send its mail under the *old* conversation's token, and the customer's next
    reply would resolve back to that old thread instead of this one.
    """
    cleaned = SUBJECT_REF_RE.sub(" ", subject or "")
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    return cleaned or "Customer complaint"


def reply_to_message(conversation: Conversation) -> Message | None:
    """The message a new reply should point ``In-Reply-To`` at.

    The customer's most recent real email, so the reply lands in their client
    threaded under what they last wrote.
    """
    for message in reversed(conversation.messages):
        if message.direction == "inbound" and message.external_message_id:
            return message
    return None


def reply_references(conversation: Conversation) -> list[str]:
    """The RFC 5322 ``References`` chain for this conversation, oldest first.

    Bounded like an inbound chain: it grows by one on every hop and each entry
    costs a lookup when the answer is threaded back.
    """
    chain = [m.external_message_id for m in conversation.messages if m.external_message_id]
    return chain[-MAX_REFERENCES:]

"""Authentication for the staff API.

Two distinct surfaces exist, and the difference is enforced here rather than
by the frontend choosing which URL to call:

- **Staff API** (`/tickets`, `/conversations`) - real customer data: email
  addresses, collected identifiers, full message bodies. Requires the shared
  secret in ``STAFF_API_KEY``.
- **Public demo** (`/inbox`, `/demo/...`) - synthetic data created by whoever
  is trying the demo, and only ever able to reach conversations flagged as
  demo. No credential, and no path to a real conversation.

A shared API key is the right weight here: there is one operator, no user
accounts to model, and nothing a session or JWT would buy. It is transported
in a header, compared in constant time, read only from the environment, and
never logged.
"""

from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status

from app.core.config import settings

API_KEY_HEADER = "X-API-Key"


def require_staff_api_key(
    x_api_key: Annotated[str | None, Header(alias=API_KEY_HEADER)] = None,
) -> None:
    """Reject anything without a valid staff key.

    Fails **closed**: if the server has no key configured, the staff endpoints
    are unavailable rather than unprotected. A misconfigured deployment must
    never be the one that quietly serves customer data to the internet.
    """
    configured = settings.staff_api_key

    if not configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Staff API is not configured on this server. Set STAFF_API_KEY "
                "to enable it."
            ),
        )

    # compare_digest keeps the comparison time independent of how much of the
    # key matched, so a wrong key leaks nothing about the right one. Both
    # sides are encoded first: the str form raises on non-ASCII input, which
    # an attacker controls.
    supplied = (x_api_key or "").encode("utf-8")
    if not secrets.compare_digest(supplied, configured.encode("utf-8")):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid API key.",
        )


StaffAuth = Depends(require_staff_api_key)

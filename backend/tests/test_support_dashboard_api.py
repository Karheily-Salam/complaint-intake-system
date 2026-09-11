"""The API behind the support dashboard.

Everything here returns real customer data - addresses, collected
identifiers, the full text of what someone wrote - so the tests are as much
about what the endpoints refuse as what they return.
"""

from __future__ import annotations

import asyncio

import pytest

from app.email.factory import get_email_provider
from app.services.email_poller import EmailPoller
from tests.conftest import STAFF_HEADERS

WITHDRAWAL = (
    "My withdrawal never arrived. user id U-482913, account email "
    "jane.doe@example.com, transaction TXN-9f3a12bc."
)
DEPOSIT = (
    "My deposit never arrived. user id U-771204, account email "
    "noah@example.com, source Sberbank wallet, date 2026-09-08, "
    "paid by bank transfer and it never showed up."
)


@pytest.fixture()
def mailbox(client):
    return get_email_provider()


def real_ticket(mailbox, address: str, body: str = WITHDRAWAL) -> None:
    """Create a genuine (non-demo) ticket through the real intake path."""
    from app.core.database import SessionLocal

    mailbox.deliver(mailbox.make_inbound(from_addr=address, subject="Problem", body=body))
    assert asyncio.run(EmailPoller(session_factory=SessionLocal).poll_once()) == 1


def demo_ticket(client, address: str, body: str = WITHDRAWAL) -> dict:
    resp = client.post(
        "/api/v1/inbox", json={"from_addr": address, "subject": "Problem", "body": body}
    )
    assert resp.status_code == 201
    return resp.json()


# --------------------------------------------------------------- access control


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/api/v1/tickets"),
        ("get", "/api/v1/tickets/000001"),
        ("patch", "/api/v1/tickets/000001"),
    ],
)
def test_unauthenticated_requests_are_rejected(client, method, path):
    body = {"status": "in_progress"} if method == "patch" else None
    resp = client.request(method, path, json=body)
    assert resp.status_code == 401


def test_unauthenticated_ticket_detail_leaks_nothing(client, mailbox):
    real_ticket(mailbox, "private@example.com")

    resp = client.get("/api/v1/tickets/000001")

    assert resp.status_code == 401
    for secret in ("private@example.com", "U-482913", "TXN-9f3a12bc", "jane.doe@example.com"):
        assert secret not in resp.text


def test_knowing_the_reference_is_not_enough(client, mailbox, staff_client):
    """Guards against IDOR: the identifier is not the authorisation."""
    real_ticket(mailbox, "victim@example.com")
    reference = staff_client.get("/api/v1/tickets").json()[0]["reference"]

    assert client.get(f"/api/v1/tickets/{reference}").status_code == 401
    # The same reference through the public demo API is a 404, not a leak.
    assert client.get(f"/api/v1/demo/tickets/{reference}").status_code == 404


def test_demo_endpoints_never_return_real_tickets(client, mailbox):
    real_ticket(mailbox, "real@example.com")
    demo_ticket(client, "demo@example.com")

    listing = client.get("/api/v1/demo/tickets")

    assert listing.status_code == 200
    assert "real@example.com" not in listing.text
    assert len(listing.json()) == 1


def test_conversation_history_requires_staff_auth(client, mailbox, staff_client):
    real_ticket(mailbox, "history@example.com")
    reference = staff_client.get("/api/v1/tickets").json()[0]["reference"]

    assert client.get(f"/api/v1/tickets/{reference}").status_code == 401

    detail = staff_client.get(f"/api/v1/tickets/{reference}").json()
    assert detail["conversation"] is not None
    assert len(detail["conversation"]["messages"]) >= 1


def test_staff_responses_never_contain_secrets(client, mailbox, staff_client):
    real_ticket(mailbox, "secrets@example.com")
    reference = staff_client.get("/api/v1/tickets").json()[0]["reference"]

    bodies = [
        staff_client.get("/api/v1/tickets").text,
        staff_client.get(f"/api/v1/tickets/{reference}").text,
    ]
    for body in bodies:
        for leak in ("STAFF_API_KEY", STAFF_HEADERS["X-API-Key"], "IMAP_PASSWORD", "password"):
            assert leak not in body


# ------------------------------------------------------------------- listing


def test_authenticated_staff_can_list_and_read(client, mailbox, staff_client):
    real_ticket(mailbox, "agent@example.com")

    listing = staff_client.get("/api/v1/tickets")
    assert listing.status_code == 200
    assert len(listing.json()) == 1
    row = listing.json()[0]
    for field in ("reference", "type", "status", "created_at", "updated_at", "customer"):
        assert field in row
    assert row["customer"]["email"] == "agent@example.com"

    detail = staff_client.get(f"/api/v1/tickets/{row['reference']}")
    assert detail.status_code == 200
    assert detail.json()["structured_data"]["fields"]["user_id"] == "U-482913"


def test_newest_tickets_come_first(client, staff_client):
    for i in range(3):
        demo_ticket(client, f"order{i}@example.com")

    references = [t["reference"] for t in staff_client.get("/api/v1/tickets").json()]
    assert references == sorted(references, reverse=True)


# --------------------------------------------------------------- search/filter


def test_search_by_ticket_reference(client, staff_client):
    demo_ticket(client, "a@example.com")
    demo_ticket(client, "b@example.com")
    wanted = staff_client.get("/api/v1/tickets").json()[0]["reference"]

    found = staff_client.get(f"/api/v1/tickets?q={wanted}").json()

    assert [t["reference"] for t in found] == [wanted]


def test_search_by_customer_email(client, staff_client):
    demo_ticket(client, "findme@example.com")
    demo_ticket(client, "other@example.com")

    found = staff_client.get("/api/v1/tickets?q=findme").json()

    assert len(found) == 1
    assert found[0]["customer"]["email"] == "findme@example.com"


def test_filter_by_status(client, staff_client):
    demo_ticket(client, "s1@example.com")
    demo_ticket(client, "s2@example.com")
    reference = staff_client.get("/api/v1/tickets").json()[0]["reference"]
    staff_client.patch(f"/api/v1/tickets/{reference}", json={"status": "resolved"})

    resolved = staff_client.get("/api/v1/tickets?status=resolved").json()
    new = staff_client.get("/api/v1/tickets?status=new").json()

    assert [t["reference"] for t in resolved] == [reference]
    assert reference not in [t["reference"] for t in new]


def test_filter_by_complaint_type(client, staff_client):
    demo_ticket(client, "w@example.com", WITHDRAWAL)
    demo_ticket(client, "d@example.com", DEPOSIT)

    withdrawals = staff_client.get("/api/v1/tickets?type=withdrawal").json()

    assert withdrawals, "expected at least one withdrawal ticket"
    assert {t["type"] for t in withdrawals} == {"withdrawal"}


def test_unknown_filter_values_are_rejected(staff_client):
    assert staff_client.get("/api/v1/tickets?status=nonsense").status_code == 422
    assert staff_client.get("/api/v1/tickets?type=nonsense").status_code == 422


# ---------------------------------------------------------------- pagination


def test_pagination_pages_through_results(client, staff_client):
    for i in range(5):
        demo_ticket(client, f"page{i}@example.com")

    first = staff_client.get("/api/v1/tickets?page=1&page_size=2")
    second = staff_client.get("/api/v1/tickets?page=2&page_size=2")
    third = staff_client.get("/api/v1/tickets?page=3&page_size=2")

    assert len(first.json()) == 2
    assert len(second.json()) == 2
    assert len(third.json()) == 1
    assert first.headers["X-Total-Count"] == "5"

    seen = [t["reference"] for r in (first, second, third) for t in r.json()]
    assert len(set(seen)) == 5, "pages overlapped or dropped rows"


def test_page_size_is_bounded(staff_client):
    assert staff_client.get("/api/v1/tickets?page_size=10000").status_code == 422
    assert staff_client.get("/api/v1/tickets?page=0").status_code == 422


def test_legacy_limit_parameter_still_works(client, staff_client):
    """Backwards compatibility with the original listing contract."""
    for i in range(3):
        demo_ticket(client, f"legacy{i}@example.com")

    assert len(staff_client.get("/api/v1/tickets?limit=2").json()) == 2
    assert staff_client.get("/api/v1/tickets?limit=100000").status_code == 422


# ------------------------------------------------------------ status updates


def test_staff_can_update_status(client, staff_client):
    demo_ticket(client, "status@example.com")
    reference = staff_client.get("/api/v1/tickets").json()[0]["reference"]

    for target in ("in_progress", "resolved", "closed", "new"):
        resp = staff_client.patch(f"/api/v1/tickets/{reference}", json={"status": target})
        assert resp.status_code == 200
        assert resp.json()["status"] == target


def test_invalid_status_is_rejected_and_nothing_changes(client, staff_client):
    demo_ticket(client, "badstatus@example.com")
    reference = staff_client.get("/api/v1/tickets").json()[0]["reference"]

    resp = staff_client.patch(f"/api/v1/tickets/{reference}", json={"status": "banana"})

    assert resp.status_code == 422
    assert staff_client.get(f"/api/v1/tickets/{reference}").json()["status"] == "new"


def test_unauthenticated_status_update_changes_nothing(client, staff_client):
    demo_ticket(client, "nochange@example.com")
    reference = staff_client.get("/api/v1/tickets").json()[0]["reference"]

    anonymous = client.patch(f"/api/v1/tickets/{reference}", json={"status": "resolved"})
    assert anonymous.status_code == 401
    assert staff_client.get(f"/api/v1/tickets/{reference}").json()["status"] == "new"


def test_updating_an_unknown_ticket_is_404(staff_client):
    resp = staff_client.patch("/api/v1/tickets/999999", json={"status": "resolved"})
    assert resp.status_code == 404

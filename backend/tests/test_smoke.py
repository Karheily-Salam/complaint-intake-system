def test_health(client):
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert set(body["complaint_types"]) == {"withdrawal", "deposit", "other"}


def test_schemas_endpoint(client):
    resp = client.get("/api/v1/schemas")
    assert resp.status_code == 200
    types = {s["type"] for s in resp.json()}
    assert types == {"withdrawal", "deposit", "other"}


def test_withdrawal_intake_flow(client):
    # First message: unstructured, partial info.
    r1 = client.post(
        "/api/v1/inbox",
        json={
            "from_addr": "jane@example.com",
            "customer_name": "Jane",
            "subject": "Cannot withdraw",
            "body": (
                "Hello, I have been trying to withdraw money since yesterday and it "
                "doesn't work. My user id is U-482913 and my account email is "
                "jane.doe@example.com."
            ),
        },
    )
    assert r1.status_code == 201, r1.text
    result = r1.json()
    conversation_id = result["conversation"]["id"]
    assert result["conversation"]["complaint"]["type"] == "withdrawal"
    assert not result["is_complete"]
    # It must not ask for fields already given.
    assert "user_id" not in result["missing_fields"]
    assert "account_email" not in result["missing_fields"]
    assert "withdrawal_transaction_id" in result["missing_fields"]

    # Second message: supply the missing transaction id.
    r2 = client.post(
        "/api/v1/inbox",
        json={
            "from_addr": "jane@example.com",
            "conversation_id": conversation_id,
            "body": "The withdrawal transaction id is TXN-9f3a12bc.",
        },
    )
    assert r2.status_code == 201, r2.text
    result2 = r2.json()
    assert result2["is_complete"] is True
    assert result2["ticket_reference"] is not None

    # Ticket shows up on the dashboard endpoint.
    tickets = client.get("/api/v1/tickets").json()
    assert any(t["reference"] == result2["ticket_reference"] for t in tickets)

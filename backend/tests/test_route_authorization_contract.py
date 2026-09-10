"""Every API route must have a deliberate authorization decision.

The audit found the ticket and conversation endpoints serving customer data to
anyone. That is fixed, but nothing stopped the next endpoint from repeating it
- protection lived in whether someone remembered to add the dependency.

This test inverts that: the policy below is the source of truth, and a route
that is not in it fails the suite. Adding an endpoint therefore forces an
explicit choice about who may call it, and the diff makes that choice visible
in review.

It works from the OpenAPI schema and from real unauthenticated requests rather
than from framework internals, so it tests what a caller actually experiences
and does not break when FastAPI changes how routers are stored.
"""

from __future__ import annotations

import pytest

from app.api.security import API_KEY_HEADER

# Routes anyone may call. Each entry records why that is safe.
PUBLIC_ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/"): "service banner, no data",
    ("GET", "/api/v1/health"): "liveness and provider status, no customer data",
    ("GET", "/api/v1/schemas"): "complaint schema definitions (configuration)",
    ("GET", "/api/v1/schemas/{complaint_type}"): "one schema definition",
    ("POST", "/api/v1/inbox"): "public demo intake; creates demo records only",
    ("GET", "/api/v1/demo/tickets"): "demo tickets only, filtered in the query",
    ("GET", "/api/v1/demo/tickets/{reference}"): "demo tickets only",
    ("GET", "/api/v1/demo/conversations/{conversation_id}"): "demo conversations only",
}

# Routes that expose real customer data or mutate real state.
STAFF_ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/v1/tickets"): "real ticket listing, includes customer email",
    ("GET", "/api/v1/tickets/{reference}"): "real ticket with full conversation",
    ("PATCH", "/api/v1/tickets/{reference}"): "mutates real ticket state",
    ("GET", "/api/v1/conversations"): "real conversation listing",
    ("GET", "/api/v1/conversations/{conversation_id}"): "real conversation with messages",
    ("GET", "/api/v1/ops/stats"): "operational counts and poller state",
}

# FastAPI generates these; they are documentation, not application endpoints.
FRAMEWORK_PATHS = {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}


def declared_routes() -> set[tuple[str, str]]:
    return set(PUBLIC_ROUTES) | set(STAFF_ROUTES)


def actual_routes(client) -> dict[tuple[str, str], dict]:
    """Every operation the application exposes, from its own OpenAPI schema."""
    spec = client.get("/openapi.json").json()
    return {
        (method.upper(), path): operation
        for path, operations in spec["paths"].items()
        if path not in FRAMEWORK_PATHS
        for method, operation in operations.items()
    }


def concrete(path: str) -> str:
    """Turn a templated path into a requestable one."""
    return (
        path.replace("{complaint_type}", "withdrawal")
        .replace("{reference}", "000001")
        .replace("{conversation_id}", "1")
    )


def request(client, method: str, path: str):
    body = {"status": "resolved"} if method in {"POST", "PATCH", "PUT"} else None
    return client.request(method, concrete(path), json=body)


# ------------------------------------------------------------------ the policy


def test_every_route_has_an_explicit_policy(client):
    """A new endpoint must be classified before the suite will pass."""
    undeclared = set(actual_routes(client)) - declared_routes()

    assert not undeclared, (
        "These routes have no entry in PUBLIC_ROUTES or STAFF_ROUTES:\n  "
        + "\n  ".join(f"{m} {p}" for m, p in sorted(undeclared))
        + "\n\nClassify each one deliberately: if it can expose real customer data "
        "or change real state, add it to STAFF_ROUTES and put its router behind "
        "the staff dependency; otherwise record why it is safe to be public."
    )


def test_the_policy_does_not_list_routes_that_no_longer_exist(client):
    """Keeps the policy honest instead of accumulating stale entries."""
    missing = declared_routes() - set(actual_routes(client))

    assert not missing, "policy lists routes that do not exist: " + ", ".join(
        f"{m} {p}" for m, p in sorted(missing)
    )


# -------------------------------------------------------------- staff routes


@pytest.mark.parametrize(("method", "path"), sorted(STAFF_ROUTES))
def test_staff_routes_reject_anonymous_requests(client, method, path):
    resp = request(client, method, path)
    assert resp.status_code == 401, (
        f"{method} {path} is staff-only ({STAFF_ROUTES[(method, path)]}) but "
        f"returned {resp.status_code} without a key"
    )


@pytest.mark.parametrize(("method", "path"), sorted(STAFF_ROUTES))
def test_staff_routes_document_the_api_key(client, method, path):
    """Structural cross-check: the key is part of the published contract."""
    operation = actual_routes(client)[(method, path)]
    names = {p["name"] for p in operation.get("parameters", [])}
    assert API_KEY_HEADER in names, f"{method} {path} does not declare {API_KEY_HEADER}"


def test_staff_routes_accept_a_valid_key(staff_client):
    """Guards against the boundary being enforced by breaking the endpoint."""
    assert staff_client.get("/api/v1/tickets").status_code == 200
    assert staff_client.get("/api/v1/conversations").status_code == 200


# ------------------------------------------------------------- public routes


@pytest.mark.parametrize(("method", "path"), sorted(PUBLIC_ROUTES))
def test_public_routes_do_not_require_a_key(client, method, path):
    """Guards the other direction: the demo must not silently become gated."""
    resp = request(client, method, path)
    assert resp.status_code not in (401, 403), (
        f"{method} {path} is public but returned {resp.status_code} without a key"
    )


def test_public_routes_never_declare_the_api_key(client):
    for key in sorted(PUBLIC_ROUTES):
        operation = actual_routes(client)[key]
        names = {p["name"] for p in operation.get("parameters", [])}
        assert API_KEY_HEADER not in names, f"{key} unexpectedly requires {API_KEY_HEADER}"

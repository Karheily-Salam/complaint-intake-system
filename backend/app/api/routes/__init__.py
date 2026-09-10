from fastapi import APIRouter

from app.api.routes import conversations, demo, health, inbox, ops, schemas, tickets

api_router = APIRouter()

# Public: no customer data, or synthetic data only.
api_router.include_router(health.router, tags=["health"])
api_router.include_router(schemas.router, prefix="/schemas", tags=["schemas"])
api_router.include_router(inbox.router, prefix="/inbox", tags=["inbox"])
api_router.include_router(demo.router, prefix="/demo", tags=["demo"])

# Staff: real customer data and ticket mutation, behind the staff API key
# (declared on each router, so a new route inherits it by default).
api_router.include_router(conversations.router, prefix="/conversations", tags=["conversations"])
api_router.include_router(tickets.router, prefix="/tickets", tags=["tickets"])
api_router.include_router(ops.router, prefix="/ops", tags=["ops"])

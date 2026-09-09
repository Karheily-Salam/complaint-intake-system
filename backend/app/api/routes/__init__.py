from fastapi import APIRouter

from app.api.routes import conversations, health, inbox, schemas, tickets

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(schemas.router, prefix="/schemas", tags=["schemas"])
api_router.include_router(inbox.router, prefix="/inbox", tags=["inbox"])
api_router.include_router(conversations.router, prefix="/conversations", tags=["conversations"])
api_router.include_router(tickets.router, prefix="/tickets", tags=["tickets"])

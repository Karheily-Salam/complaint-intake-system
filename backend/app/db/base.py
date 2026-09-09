"""Import-all module so Alembic autogenerate and ``create_all`` see every model.

Import :data:`Base` and all models from here (never from individual modules) when
you need the full metadata.
"""

from __future__ import annotations

from app.db.base_class import Base  # noqa: F401
from app.db.models.complaint import Complaint  # noqa: F401
from app.db.models.complaint_field import ComplaintField  # noqa: F401
from app.db.models.conversation import Conversation  # noqa: F401
from app.db.models.customer import Customer  # noqa: F401
from app.db.models.email_log import EmailLog  # noqa: F401
from app.db.models.message import Message  # noqa: F401
from app.db.models.ticket import Ticket  # noqa: F401

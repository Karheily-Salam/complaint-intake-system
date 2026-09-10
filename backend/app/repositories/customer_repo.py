from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.customer import Customer


class CustomerRepository:
    """Customer lookup, scoped by whether the caller is demo or real.

    ``is_demo`` is part of identity, not a filter applied afterwards: the
    public demo endpoint accepts an unverified sender address, so a lookup by
    email alone would return - and then let the caller modify - the record of
    a real customer who happens to use that address.
    """

    def __init__(self, db: Session) -> None:
        self.db = db

    def get_by_email(self, email: str, *, is_demo: bool = False) -> Customer | None:
        return self.db.scalar(
            select(Customer).where(
                Customer.email == email, Customer.is_demo.is_(is_demo)
            )
        )

    def get_or_create(
        self, email: str, name: str | None = None, *, is_demo: bool = False
    ) -> Customer:
        customer = self.get_by_email(email, is_demo=is_demo)
        if customer is None:
            customer = Customer(email=email, name=name, is_demo=is_demo)
            self.db.add(customer)
            self.db.flush()
        elif name and not customer.name:
            # Only ever reached for a customer in the caller's own scope, so
            # demo input cannot name a real customer.
            customer.name = name
        return customer

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.customer import Customer


class CustomerRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_by_email(self, email: str) -> Customer | None:
        return self.db.scalar(select(Customer).where(Customer.email == email))

    def get_or_create(self, email: str, name: str | None = None) -> Customer:
        customer = self.get_by_email(email)
        if customer is None:
            customer = Customer(email=email, name=name)
            self.db.add(customer)
            self.db.flush()
        elif name and not customer.name:
            customer.name = name
        return customer

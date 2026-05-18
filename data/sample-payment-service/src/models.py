"""Data models for the payment service — exercises pydantic + sqlalchemy
detection. Trivial fields; the resolver only captures identity in v1."""

from pydantic import BaseModel
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class Charge(BaseModel):
    id: str
    amount: int
    currency: str = "USD"


class ChargeRecord(Base):
    __tablename__ = "charges"
    # Field-level extraction lands in a later step; this class only needs
    # to exist as a Base subclass to register as a DataModel(sqlalchemy_orm).

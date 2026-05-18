"""Tiny persistence layer — exercises QueryResolver (raw SQL via
session.execute) and KafkaResolver (producer.send for a domain event)."""

from kafka import KafkaProducer
from sqlalchemy import text
from sqlalchemy.orm import Session

producer = KafkaProducer(bootstrap_servers="localhost:9092")


def get_charge_row(session: Session, charge_id: str) -> dict | None:
    row = session.execute(
        text("SELECT id, amount FROM charges WHERE id = :id"),
        {"id": charge_id},
    ).first()
    if row is None:
        return None
    return {"id": row[0], "amount": row[1]}


def record_payment(session: Session, charge_id: str, amount: int) -> None:
    session.execute(
        text("INSERT INTO payments (charge_id, amount) VALUES (:cid, :amt)"),
        {"cid": charge_id, "amt": amount},
    )
    producer.send("payments.created", value=str(amount).encode())

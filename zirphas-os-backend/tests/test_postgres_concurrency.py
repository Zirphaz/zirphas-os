"""Real row-lock regressions; opt in with TEST_POSTGRES_URL.

Each test owns a random PostgreSQL schema and uses independent sessions in worker
threads. No existing application schema, migrations or data are changed.
"""

import os
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema, DropSchema

from app.models import CashEntry, Client, Device, Part, Payment, ServiceOrder, User
from app.models.base import Base
from app.models.enums import OrderStatus as S, Role
from app.services.orders import (
    create_order, decide_quote, get_order, record_payment, set_part,
    transition_order, update_quote,
)


@pytest.fixture
def postgres_engine():
    url = os.environ.get("TEST_POSTGRES_URL")
    if not url:
        pytest.skip("TEST_POSTGRES_URL is required for real PostgreSQL concurrency tests")
    schema = f"test_concurrency_{uuid4().hex}"
    management = create_engine(url, pool_pre_ping=True)
    if management.dialect.name != "postgresql":
        management.dispose()
        pytest.fail("TEST_POSTGRES_URL must point to PostgreSQL")
    engine = None
    created = False
    try:
        with management.begin() as connection:
            connection.execute(CreateSchema(schema))
        created = True
        engine = create_engine(
            url, pool_pre_ping=True, pool_size=5,
            connect_args={"options": (
                f"-csearch_path={schema} -clock_timeout=10000 -cstatement_timeout=20000"
            )},
        )
        Base.metadata.create_all(engine)
        yield engine
    finally:
        if engine is not None:
            engine.dispose()
        if created:
            with management.begin() as connection:
                connection.execute(DropSchema(schema, cascade=True))
        management.dispose()


@pytest.fixture
def postgres_data(postgres_engine):
    with Session(postgres_engine, expire_on_commit=False) as db, db.begin():
        tech = User(name="Técnico", email="tech@example.test", password_hash="unused", role=Role.TECH)
        front = User(name="Recepção", email="front@example.test", password_hash="unused", role=Role.FRONT_DESK)
        client = Client(name="Cliente", phone="11999999999")
        part = Part(name="Tela", sku="SCREEN", quantity=1,
                    cost_price=Decimal("30.00"), sale_price=Decimal("90.00"))
        db.add_all([tech, front, client, part])
        db.flush()
        device = Device(client_id=client.id, kind="celular", brand="Marca",
                        model="Modelo", imei_serial="SERIAL-CONCURRENCY")
        db.add(device)
        db.flush()
        order_ids = []
        for _ in range(2):
            order, _token = create_order(
                db, device_id=device.id, problem_description="Tela quebrada",
                physical_checklist={"liga": True},
            )
            transition_order(db, order, S.ANALYZING, tech)
            update_quote(db, order, labor_amount=Decimal("50.00"),
                         commission_amount=Decimal("0.00"), technical_report="Trocar tela.", actor=tech)
            set_part(db, order, part_id=part.id, quantity=1, actor=tech)
            transition_order(db, order, S.AWAITING_APPROVAL, tech)
            decide_quote(db, order, approved=True, quote_version=order.quote_version)
            assert order.status == S.REPAIRING
            order_ids.append(order.id)
        return SimpleNamespace(
            tech_id=tech.id, front_id=front.id, part_id=part.id, order_ids=order_ids,
        )


def _concurrently(worker, arguments):
    barrier = Barrier(len(arguments), timeout=10)
    with ThreadPoolExecutor(max_workers=len(arguments)) as pool:
        futures = [pool.submit(worker, argument, barrier) for argument in arguments]
        return [future.result(timeout=30) for future in futures]


def test_last_piece_completion_and_delivery_are_serialized(postgres_engine, postgres_data):
    data = postgres_data

    def complete(order_id, barrier):
        with Session(postgres_engine) as db:
            try:
                order = get_order(db, order_id, lock=True)
                tech = db.get(User, data.tech_id)
                # Both sessions cache the original stock before contending. The
                # completion query must refresh the row after obtaining its lock.
                assert order.parts[0].part.quantity == 1
                barrier.wait()
                transition_order(db, order, S.COMPLETED, tech)
                db.commit()
                return (200, order_id)
            except HTTPException as exc:
                db.rollback()
                return (exc.status_code, order_id)

    results = _concurrently(complete, data.order_ids)
    assert sorted(status for status, _ in results) == [200, 409]
    winner_id = next(order_id for status, order_id in results if status == 200)
    loser_id = next(order_id for status, order_id in results if status == 409)
    with Session(postgres_engine) as db, db.begin():
        assert db.get(Part, data.part_id).quantity == 0
        assert db.get(ServiceOrder, loser_id).status == S.REPAIRING
        assert db.get(ServiceOrder, loser_id).completed_at is None
        winner = get_order(db, winner_id, lock=True)
        assert winner.status == S.COMPLETED
        assert winner.parts_cost == Decimal("30.00")
        assert winner.net_profit == Decimal("110.00")
        record_payment(db, winner, amount=winner.total_amount, method="pix",
                       idempotency_key="full-payment", actor=db.get(User, data.front_id))

    def deliver(order_id, barrier):
        with Session(postgres_engine) as db:
            try:
                barrier.wait()
                order = get_order(db, order_id, lock=True)
                transition_order(db, order, S.DELIVERED, db.get(User, data.front_id))
                db.commit()
                return 200
            except HTTPException as exc:
                db.rollback()
                return exc.status_code

    assert sorted(_concurrently(deliver, [winner_id, winner_id])) == [200, 409]
    with Session(postgres_engine) as db:
        assert db.scalar(select(func.count(CashEntry.id))) == 1
        assert db.scalar(select(CashEntry.amount)) == Decimal("140.00")
        assert db.get(ServiceOrder, winner_id).status == S.DELIVERED
        assert db.get(Part, data.part_id).quantity == 0


def _payment_worker(engine, data):
    def pay(request, barrier):
        amount, key = request
        with Session(engine) as db:
            try:
                barrier.wait()
                order = get_order(db, data.order_ids[0], lock=True)
                payment = record_payment(
                    db, order, amount=Decimal(amount), method="pix",
                    idempotency_key=key, actor=db.get(User, data.front_id),
                )
                payment_id = payment.id
                db.commit()
                return (200, payment_id)
            except HTTPException as exc:
                db.rollback()
                return (exc.status_code, None)
    return pay


def test_concurrent_identical_payments_return_same_record(postgres_engine, postgres_data):
    results = _concurrently(
        _payment_worker(postgres_engine, postgres_data),
        [("100.00", "same-request"), ("100.00", "same-request")],
    )
    assert [status for status, _ in results] == [200, 200]
    assert results[0][1] == results[1][1]
    with Session(postgres_engine) as db:
        assert db.scalar(select(func.count(Payment.id))) == 1
        assert db.scalar(select(func.sum(Payment.amount))) == Decimal("100.00")


def test_concurrent_distinct_payments_cannot_overpay(postgres_engine, postgres_data):
    results = _concurrently(
        _payment_worker(postgres_engine, postgres_data),
        [("100.00", "request-a"), ("100.00", "request-b")],
    )
    assert sorted(status for status, _ in results) == [200, 409]
    with Session(postgres_engine) as db:
        assert db.scalar(select(func.count(Payment.id))) == 1
        assert db.scalar(select(func.sum(Payment.amount))) == Decimal("100.00")
        assert db.scalar(select(func.count(CashEntry.id))) == 0


def test_concurrent_reused_key_with_different_amount_conflicts(postgres_engine, postgres_data):
    results = _concurrently(
        _payment_worker(postgres_engine, postgres_data),
        [("80.00", "conflicting-key"), ("90.00", "conflicting-key")],
    )
    assert sorted(status for status, _ in results) == [200, 409]
    with Session(postgres_engine) as db:
        assert db.scalar(select(func.count(Payment.id))) == 1
        assert db.scalar(select(Payment.amount)) in {Decimal("80.00"), Decimal("90.00")}


def test_completion_persists_maximum_cost_and_commission_loss(postgres_engine, postgres_data):
    maximum = Decimal("9999999999.99")
    data = postgres_data
    with Session(postgres_engine) as db, db.begin():
        admin = User(name="Administrador", email="admin@example.test",
                     password_hash="unused", role=Role.ADMIN)
        db.add(admin)
        part = db.get(Part, data.part_id)
        part.cost_price = maximum
        part.sale_price = Decimal("0.00")
        db.flush()
        tech = db.get(User, data.tech_id)
        existing_order = db.get(ServiceOrder, data.order_ids[0])
        order, _token = create_order(
            db, device_id=existing_order.device_id, problem_description="Reparo sem cobrança",
            physical_checklist={"liga": True},
        )
        transition_order(db, order, S.ANALYZING, tech)
        update_quote(db, order, labor_amount=Decimal("0.00"), commission_amount=maximum,
                     technical_report="Reparo assumido pela assistência.", actor=admin)
        set_part(db, order, part_id=part.id, quantity=1, actor=tech)
        transition_order(db, order, S.AWAITING_APPROVAL, tech)
        decide_quote(db, order, approved=True, quote_version=order.quote_version)
        transition_order(db, order, S.COMPLETED, tech)
        order_id = order.id
    with Session(postgres_engine) as db:
        completed = db.get(ServiceOrder, order_id)
        assert completed.status == S.COMPLETED
        assert completed.total_amount == Decimal("0.00")
        assert completed.parts_cost == maximum
        assert completed.commission_amount == maximum
        assert completed.net_profit == Decimal("-19999999999.98")
        assert db.get(Part, data.part_id).quantity == 0

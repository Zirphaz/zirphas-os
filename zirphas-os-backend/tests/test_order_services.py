from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session

from app.models import CashEntry, Client, Device, Part, Payment, ServiceOrder, User
from app.models.base import Base
from app.models.enums import OrderStatus as S, Role
from app.services.orders import (
    create_order, decide_quote, get_order, record_payment, set_part,
    transition_order, update_quote,
)


@pytest.fixture
def domain_db():
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        yield session
    engine.dispose()


@pytest.fixture
def domain_data(domain_db):
    db = domain_db
    actors = [User(
        name=name, email=f"{name}@example.test", password_hash="unused",
        role=role,
    ) for name, role in (
        ("admin", Role.ADMIN), ("tech", Role.TECH),
        ("other_tech", Role.TECH), ("front", Role.FRONT_DESK),
    )]
    client = Client(name="Cliente", phone="11999999999", document="123")
    part = Part(name="Tela", sku="TELA-1", quantity=2,
                cost_price=Decimal("30.00"), sale_price=Decimal("90.00"))
    db.add_all([*actors, client, part])
    db.flush()
    device = Device(client_id=client.id, kind="celular", brand="Marca",
                    model="Modelo", imei_serial="IMEI-123")
    db.add(device)
    db.flush()
    return SimpleNamespace(
        admin=actors[0], tech=actors[1], other_tech=actors[2], front=actors[3],
        device=device, part=part,
    )


def _new_order(db, data):
    order, _ = create_order(
        db, device_id=data.device.id, problem_description="Tela quebrada",
        physical_checklist={"carcaca_integra": False, "liga": True},
    )
    return order


def _quoted_order(db, data, *, quantity=1):
    order = _new_order(db, data)
    transition_order(db, order, S.ANALYZING, data.tech)
    update_quote(
        db, order, labor_amount=Decimal("50.00"), commission_amount=Decimal("10.00"),
        technical_report="Trocar tela.", actor=data.admin,
    )
    set_part(db, order, part_id=data.part.id, quantity=quantity, actor=data.tech)
    transition_order(db, order, S.AWAITING_APPROVAL, data.tech)
    return order


def _approved_order(db, data, *, quantity=1):
    order = _quoted_order(db, data, quantity=quantity)
    decide_quote(db, order, approved=True, quote_version=order.quote_version)
    return order


def test_technician_claim_and_ownership(domain_db, domain_data):
    db, data = domain_db, domain_data
    order = _new_order(db, data)
    with pytest.raises(HTTPException) as error:
        transition_order(db, order, S.ANALYZING, data.admin)
    assert error.value.status_code == 409
    assert order.tech_id is None
    transition_order(db, order, S.ANALYZING, data.tech)
    assert order.tech_id == data.tech.id
    for actor in (data.other_tech, data.front):
        with pytest.raises(HTTPException) as error:
            update_quote(db, order, labor_amount=Decimal("50.00"),
                         commission_amount=Decimal("0.00"),
                         technical_report="Diagnóstico", actor=actor)
        assert error.value.status_code == 403


def test_quote_requires_current_version_and_explicit_non_tech_decision(domain_db, domain_data):
    db, data = domain_db, domain_data
    order = _quoted_order(db, data)
    version = order.quote_version
    with pytest.raises(HTTPException) as error:
        decide_quote(db, order, approved=True, quote_version=version - 1)
    assert error.value.status_code == 409
    with pytest.raises(HTTPException) as error:
        decide_quote(db, order, approved=True, quote_version=version, actor=data.tech)
    assert error.value.status_code == 403
    with pytest.raises(HTTPException) as error:
        transition_order(db, order, S.REPAIRING, data.admin)
    assert error.value.status_code == 409
    decide_quote(db, order, approved=False, quote_version=version)
    assert order.status == S.ANALYZING
    assert order.approved_at is None
    update_quote(db, order, labor_amount=Decimal("40.00"), commission_amount=Decimal("10.00"),
                 technical_report="Novo orçamento", actor=data.tech)
    assert order.quote_version == version + 1


def test_approved_quote_is_immutable_even_to_administrator(domain_db, domain_data):
    db, data = domain_db, domain_data
    order = _approved_order(db, data)
    total, version = order.total_amount, order.quote_version
    with pytest.raises(HTTPException) as error:
        update_quote(db, order, labor_amount=Decimal("99.00"), commission_amount=Decimal("0.00"),
                     technical_report="Changed", actor=data.admin)
    assert error.value.status_code == 409
    with pytest.raises(HTTPException) as error:
        set_part(db, order, part_id=data.part.id, quantity=0, actor=data.admin)
    assert error.value.status_code == 409
    assert (order.total_amount, order.quote_version) == (total, version)


def test_missing_stock_waits_then_can_start_after_restock(domain_db, domain_data):
    db, data = domain_db, domain_data
    data.part.quantity = 0
    order = _approved_order(db, data)
    assert order.status == S.AWAITING_PARTS
    with pytest.raises(HTTPException) as error:
        transition_order(db, order, S.REPAIRING, data.tech)
    assert error.value.status_code == 409
    data.part.quantity = 1
    db.flush()
    transition_order(db, order, S.REPAIRING, data.tech)
    assert data.part.quantity == 1  # Reservation/consumption happens only at completion.


def test_completion_snapshots_cost_and_profit_once(domain_db, domain_data):
    db, data = domain_db, domain_data
    order = _approved_order(db, data)
    assert order.parts[0].unit_price == Decimal("90.00")
    data.part.cost_price = Decimal("35.00")
    data.part.sale_price = Decimal("100.00")
    db.flush()
    transition_order(db, order, S.COMPLETED, data.tech)
    assert data.part.quantity == 1
    assert order.total_amount == Decimal("140.00")
    assert order.parts_cost == Decimal("35.00")
    assert order.net_profit == Decimal("95.00")
    assert order.parts[0].unit_cost == Decimal("35.00")
    data.part.cost_price = Decimal("70.00")
    db.flush()
    db.expire(order)
    assert order.parts_cost == Decimal("35.00")
    assert order.net_profit == Decimal("95.00")
    with pytest.raises(HTTPException) as error:
        transition_order(db, order, S.COMPLETED, data.tech)
    assert error.value.status_code == 409
    assert data.part.quantity == 1


def test_competing_orders_cannot_consume_last_part_twice(domain_db, domain_data):
    db, data = domain_db, domain_data
    data.part.quantity = 1
    first = _approved_order(db, data)
    second = _approved_order(db, data)
    assert first.status == second.status == S.REPAIRING
    transition_order(db, first, S.COMPLETED, data.tech)
    first_id, second_id, part_id = first.id, second.id, data.part.id
    db.commit()
    second = get_order(db, second_id, lock=True)
    with pytest.raises(HTTPException) as error:
        transition_order(db, second, S.COMPLETED, data.tech)
    assert error.value.status_code == 409
    db.rollback()
    assert db.get(Part, part_id).quantity == 0
    assert db.get(ServiceOrder, second_id).status == S.REPAIRING
    assert db.get(ServiceOrder, second_id).net_profit is None
    assert db.get(ServiceOrder, first_id).status == S.COMPLETED


def test_insufficient_second_part_does_not_consume_first(domain_db, domain_data):
    db, data = domain_db, domain_data
    extra = Part(name="Bateria", sku="BAT-1", quantity=1,
                 cost_price=Decimal("20.00"), sale_price=Decimal("60.00"))
    db.add(extra)
    db.flush()
    order = _new_order(db, data)
    transition_order(db, order, S.ANALYZING, data.tech)
    update_quote(db, order, labor_amount=Decimal("50.00"), commission_amount=Decimal("0.00"),
                 technical_report="Trocar peças", actor=data.tech)
    set_part(db, order, part_id=data.part.id, quantity=1, actor=data.tech)
    set_part(db, order, part_id=extra.id, quantity=1, actor=data.tech)
    transition_order(db, order, S.AWAITING_APPROVAL, data.tech)
    decide_quote(db, order, approved=True, quote_version=order.quote_version)
    extra.quantity = 0
    db.commit()
    order_id, part_id = order.id, data.part.id
    with pytest.raises(HTTPException) as error:
        transition_order(db, get_order(db, order_id, lock=True), S.COMPLETED, data.tech)
    assert error.value.status_code == 409
    db.rollback()
    assert db.get(Part, part_id).quantity == 2
    assert db.get(ServiceOrder, order_id).parts_cost == Decimal("0.00")
    assert db.get(ServiceOrder, order_id).completed_at is None


def test_payments_are_idempotent_capped_and_cash_is_recognized_once(domain_db, domain_data):
    db, data = domain_db, domain_data
    order = _approved_order(db, data)
    args = dict(amount=Decimal("40.00"), method="pix", idempotency_key="request-1", actor=data.front)
    first = record_payment(db, order, **args)
    assert record_payment(db, order, **args).id == first.id
    assert db.scalar(select(func.count(Payment.id))) == 1
    with pytest.raises(HTTPException) as error:
        record_payment(db, order, **{**args, "amount": Decimal("50.00")})
    assert error.value.status_code == 409
    with pytest.raises(HTTPException) as error:
        record_payment(db, order, **{**args, "amount": Decimal("101.00"), "idempotency_key": "too-much"})
    assert error.value.status_code == 409
    transition_order(db, order, S.COMPLETED, data.tech)
    with pytest.raises(HTTPException) as error:
        transition_order(db, order, S.DELIVERED, data.front)
    assert error.value.status_code == 409
    assert db.scalar(select(func.count(CashEntry.id))) == 0
    record_payment(db, order, **{**args, "amount": Decimal("100.00"), "idempotency_key": "request-2"})
    transition_order(db, order, S.DELIVERED, data.front)
    assert record_payment(db, order, **args).id == first.id
    assert db.scalar(select(func.count(CashEntry.id))) == 1
    assert db.scalar(select(CashEntry.amount)) == Decimal("140.00")
    with pytest.raises(HTTPException):
        transition_order(db, order, S.DELIVERED, data.front)
    assert db.scalar(select(func.count(CashEntry.id))) == 1


def test_payment_flush_is_rolled_back_by_caller(domain_db, domain_data):
    db, data = domain_db, domain_data
    order = _approved_order(db, data)
    db.commit()
    record_payment(db, order, amount=Decimal("10.00"), method="pix",
                   idempotency_key="rollback-me", actor=data.front)
    assert db.scalar(select(func.count(Payment.id))) == 1
    db.rollback()
    assert db.scalar(select(func.count(Payment.id))) == 0


def test_same_idempotency_key_can_be_used_on_distinct_orders(domain_db, domain_data):
    db, data = domain_db, domain_data
    first, second = _approved_order(db, data), _approved_order(db, data)
    for order in (first, second):
        record_payment(db, order, amount=Decimal("10.00"), method="pix",
                       idempotency_key="same-client-key", actor=data.front)
    assert db.scalar(select(func.count(Payment.id))) == 2

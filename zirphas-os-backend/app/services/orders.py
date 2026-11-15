"""Transactional order operations; callers own commit/rollback and lock the order.

Inventory is locked in primary-key order at completion. Approval freezes selling
prices, while completion snapshots actual part costs and recognizes the margin.
"""

import hashlib
import secrets
from decimal import Decimal, InvalidOperation

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.os_state_machine import InvalidTransition, StateContext, transition_state
from app.models import (
    CashEntry,
    Device,
    OrderEvent,
    OrderPart,
    Part,
    Payment,
    ServiceOrder,
    User,
)
from app.models.base import utcnow
from app.models.enums import OrderStatus, Role

CENT = Decimal("0.01")
ZERO = Decimal("0.00")
MAX_MONEY = Decimal("9999999999.99")


def _money(value: Decimal, *, positive: bool = False) -> Decimal:
    try:
        amount = Decimal(str(value))
        rounded = amount.quantize(CENT)
    except (InvalidOperation, TypeError, ValueError):
        raise HTTPException(422, "Informe um valor monetário válido.") from None
    if not amount.is_finite() or amount != rounded or amount < 0 or amount > MAX_MONEY:
        raise HTTPException(422, "Valores devem ser não negativos, com até duas casas decimais.")
    if positive and amount == 0:
        raise HTTPException(422, "O pagamento deve ser maior que zero.")
    return rounded


def get_order(db: Session, order_id: int, lock: bool = False) -> ServiceOrder:
    statement = select(ServiceOrder).where(ServiceOrder.id == order_id)
    if lock:
        statement = statement.with_for_update().execution_options(populate_existing=True)
    order = db.execute(statement).unique().scalar_one_or_none()
    if order is None:
        raise HTTPException(404, "Ordem de serviço não encontrada.")
    return order


def _authorize_technical(order: ServiceOrder, actor: User, *, claim: bool = False) -> None:
    if actor.role == Role.ADMIN:
        return
    if actor.role != Role.TECH:
        raise HTTPException(403, "Esta operação exige um técnico ou administrador.")
    if order.tech_id != actor.id and not (claim and order.tech_id is None):
        raise HTTPException(403, "Esta OS está atribuída a outro técnico.")


def _authorize_reception(actor: User) -> None:
    if actor.role not in {Role.ADMIN, Role.FRONT_DESK}:
        raise HTTPException(403, "Esta operação exige recepção ou administrador.")


def _total_paid(db: Session, order: ServiceOrder) -> Decimal:
    return Decimal(
        db.scalar(select(func.coalesce(func.sum(Payment.amount), 0)).where(
            Payment.order_id == order.id
        ))
    )


def _stock_available(db: Session, order: ServiceOrder, *, lock: bool = False) -> bool:
    needed = {row.part_id: row.quantity for row in order.parts}
    if not needed:
        return True
    statement = select(Part.id, Part.quantity).where(Part.id.in_(needed)).order_by(Part.id)
    if lock:
        statement = statement.with_for_update()
    quantities = dict(db.execute(statement).all())
    return all(quantities.get(part_id, -1) >= quantity for part_id, quantity in needed.items())


def _context(
    db: Session, order: ServiceOrder, *, stock: bool | None = None,
    decision: bool | None = None, has_technician: bool | None = None,
) -> StateContext:
    return StateContext(
        has_device_identity=bool(order.device.imei_serial and order.device.imei_serial.strip()),
        has_physical_checklist=bool(order.physical_checklist),
        has_technician=order.tech_id is not None if has_technician is None else has_technician,
        has_technical_report=bool(order.technical_report and order.technical_report.strip()),
        quote_is_approved=(decision is True or (
            order.approved_at is not None and order.approved_quote_version == order.quote_version
        )),
        stock_available=_stock_available(db, order) if stock is None else stock,
        fully_paid=_total_paid(db, order) == order.total_amount,
        approval_decision=decision,
    )


def _validate_transition(order: ServiceOrder, target: OrderStatus, context: StateContext) -> None:
    try:
        transition_state(order.status, target, context)
    except InvalidTransition as exc:
        raise HTTPException(409, str(exc)) from exc


def _change_status(
    db: Session, order: ServiceOrder, target: OrderStatus, actor: User | None,
    note: str | None = None,
) -> None:
    previous = order.status
    order.status = target
    order.updated_at = utcnow()
    db.add(OrderEvent(
        order_id=order.id, from_status=previous, to_status=target,
        note=note or f"Status alterado para {target.value}.", actor_id=actor.id if actor else None,
    ))


def create_order(
    db: Session, *, device_id: int, problem_description: str, physical_checklist: dict,
) -> tuple[ServiceOrder, str]:
    device = db.get(Device, device_id)
    if device is None:
        raise HTTPException(404, "Aparelho não encontrado.")
    if not device.imei_serial or not device.imei_serial.strip():
        raise HTTPException(422, "Informe o IMEI ou número de série do aparelho.")
    if not isinstance(physical_checklist, dict) or not physical_checklist:
        raise HTTPException(422, "Preencha o checklist físico de entrada.")
    if not problem_description or not problem_description.strip():
        raise HTTPException(422, "Descreva o problema informado pelo cliente.")
    raw_token = secrets.token_urlsafe(32)
    order = ServiceOrder(
        device_id=device_id, status=OrderStatus.INTAKE,
        problem_description=problem_description.strip(), physical_checklist=physical_checklist,
        tracking_token_hash=hashlib.sha256(raw_token.encode()).hexdigest(),
        labor_amount=ZERO, total_amount=ZERO, commission_amount=ZERO, quote_version=1,
    )
    db.add(order)
    db.flush()
    db.add(OrderEvent(
        order_id=order.id, from_status=None, to_status=OrderStatus.INTAKE,
        note="Aparelho recebido e checklist registrado.", actor_id=None,
    ))
    db.flush()
    return order, raw_token


def _ensure_quote_editable(db: Session, order: ServiceOrder, actor: User) -> None:
    _authorize_technical(order, actor)
    if order.status != OrderStatus.ANALYZING:
        raise HTTPException(409, "O orçamento só pode ser editado durante a análise.")
    if order.approved_at is not None or _total_paid(db, order) > ZERO:
        raise HTTPException(409, "Orçamentos aprovados ou pagos não podem ser alterados.")


def _recalculate_quote(order: ServiceOrder) -> None:
    order.total_amount = _money(order.labor_amount + sum(
        (row.unit_price * row.quantity for row in order.parts), ZERO
    ))
    order.quote_version += 1
    order.approved_quote_version = None
    order.approved_at = None
    order.approval_source = None
    order.updated_at = utcnow()


def update_quote(
    db: Session, order: ServiceOrder, *, labor_amount: Decimal,
    commission_amount: Decimal, technical_report: str, actor: User,
) -> ServiceOrder:
    _ensure_quote_editable(db, order, actor)
    labor = _money(labor_amount)
    commission = _money(commission_amount)
    if actor.role == Role.TECH and commission != order.commission_amount:
        raise HTTPException(403, "Apenas o administrador pode alterar a comissão.")
    if not technical_report or not technical_report.strip():
        raise HTTPException(422, "Informe o laudo técnico.")
    order.labor_amount = labor
    order.commission_amount = commission
    order.technical_report = technical_report.strip()
    _recalculate_quote(order)
    db.flush()
    return order


def set_part(
    db: Session, order: ServiceOrder, *, part_id: int, quantity: int, actor: User,
) -> ServiceOrder:
    _ensure_quote_editable(db, order, actor)
    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 0:
        raise HTTPException(422, "A quantidade deve ser um inteiro não negativo.")
    part = db.get(Part, part_id)
    if part is None:
        raise HTTPException(404, "Peça não encontrada.")
    existing = next((row for row in order.parts if row.part_id == part_id), None)
    if quantity == 0:
        if existing is None:
            return order
        order.parts.remove(existing)
        db.delete(existing)
    elif existing is not None:
        existing.quantity = quantity
        existing.unit_price = part.sale_price
    else:
        order.parts.append(OrderPart(
            order_id=order.id, part_id=part_id, quantity=quantity,
            unit_price=part.sale_price, unit_cost=part.cost_price,
        ))
    _recalculate_quote(order)
    db.flush()
    return order


def decide_quote(
    db: Session, order: ServiceOrder, *, approved: bool, quote_version: int,
    actor: User | None = None,
) -> ServiceOrder:
    if actor is not None:
        _authorize_reception(actor)
    if order.status != OrderStatus.AWAITING_APPROVAL:
        raise HTTPException(409, "Esta OS não está aguardando aprovação.")
    if quote_version != order.quote_version:
        raise HTTPException(409, "O orçamento foi atualizado. Consulte a versão atual antes de decidir.")
    available = _stock_available(db, order, lock=True)
    target = (
        OrderStatus.REPAIRING if available else OrderStatus.AWAITING_PARTS
    ) if approved else OrderStatus.ANALYZING
    _validate_transition(order, target, _context(db, order, stock=available, decision=approved))
    if approved:
        order.approved_quote_version = order.quote_version
        order.approved_at = utcnow()
        order.approval_source = "customer" if actor is None else actor.role.value
        note = f"Orçamento v{order.quote_version} aprovado."
    else:
        order.approved_quote_version = None
        order.approved_at = None
        order.approval_source = None
        note = f"Orçamento v{order.quote_version} recusado; OS devolvida à análise."
    _change_status(db, order, target, actor, note)
    db.flush()
    return order


def transition_order(
    db: Session, order: ServiceOrder, target: OrderStatus, actor: User,
) -> ServiceOrder:
    if target == OrderStatus.DELIVERED:
        _authorize_reception(actor)
    else:
        _authorize_technical(order, actor, claim=(
            order.status == OrderStatus.INTAKE and target == OrderStatus.ANALYZING
        ))
    if order.status == OrderStatus.AWAITING_APPROVAL:
        raise HTTPException(409, "Use a decisão de orçamento para aprovar ou recusar esta OS.")
    claiming = (
        order.status == OrderStatus.INTAKE and target == OrderStatus.ANALYZING
        and actor.role == Role.TECH
    )
    locked_parts: dict[int, Part] | None = None
    if target == OrderStatus.COMPLETED:
        part_ids = sorted(row.part_id for row in order.parts)
        locked_parts = {
            part.id: part for part in db.scalars(
                select(Part).where(Part.id.in_(part_ids)).order_by(Part.id)
                .with_for_update().execution_options(populate_existing=True)
            )
        } if part_ids else {}
        stock = all(
            row.part_id in locked_parts and locked_parts[row.part_id].quantity >= row.quantity
            for row in order.parts
        )
    else:
        stock = None
    context = _context(db, order, stock=stock, has_technician=True if claiming else None)
    _validate_transition(order, target, context)
    if claiming:
        order.tech_id = actor.id
    if target == OrderStatus.COMPLETED:
        costs = ZERO
        for row in sorted(order.parts, key=lambda item: item.part_id):
            part = locked_parts[row.part_id]
            row.unit_cost = part.cost_price
            costs += row.unit_cost * row.quantity
            part.quantity -= row.quantity
        order.parts_cost = _money(costs)
        order.net_profit = order.total_amount - costs - order.commission_amount
        order.completed_at = utcnow()
    elif target == OrderStatus.DELIVERED:
        if db.scalar(select(CashEntry.id).where(CashEntry.order_id == order.id)) is not None:
            raise HTTPException(409, "O recebimento desta OS já foi lançado no fluxo de caixa.")
        order.delivered_at = utcnow()
        db.add(CashEntry(order_id=order.id, amount=order.total_amount))
    _change_status(db, order, target, actor)
    db.flush()
    return order


def _matching_payment(
    payment: Payment, *, order: ServiceOrder, amount: Decimal, method: str,
) -> Payment:
    if payment.order_id != order.id or payment.amount != amount or payment.method != method:
        raise HTTPException(409, "A chave de idempotência já foi usada com outros dados.")
    return payment


def record_payment(
    db: Session, order: ServiceOrder, *, amount: Decimal, method: str,
    idempotency_key: str, actor: User,
) -> Payment:
    _authorize_reception(actor)
    amount = _money(amount, positive=True)
    method = method.strip()
    if not method or not idempotency_key or not idempotency_key.strip():
        raise HTTPException(422, "Informe a forma de pagamento e a chave de idempotência.")
    statement = select(Payment).where(
        Payment.order_id == order.id, Payment.idempotency_key == idempotency_key
    )
    existing = db.scalar(statement)
    if existing is not None:
        return _matching_payment(existing, order=order, amount=amount, method=method)
    if order.status not in {
        OrderStatus.AWAITING_PARTS, OrderStatus.REPAIRING, OrderStatus.COMPLETED,
    } or order.approved_quote_version != order.quote_version or order.approved_at is None:
        raise HTTPException(409, "O pagamento exige orçamento aprovado e OS ainda não entregue.")
    if _total_paid(db, order) + amount > order.total_amount:
        raise HTTPException(409, "O pagamento ultrapassa o saldo restante da OS.")
    payment = Payment(
        order_id=order.id, amount=amount, method=method,
        idempotency_key=idempotency_key, received_by=actor.id,
    )
    # The caller's FOR UPDATE order lock serializes payment amount checks and
    # per-order idempotency checks in the same transaction.
    db.add(payment)
    db.flush()
    return payment

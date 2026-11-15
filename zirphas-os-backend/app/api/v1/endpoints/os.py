import hashlib
import secrets
from html import escape

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from app.api.dependencies import CurrentUser, DatabaseSession, require_roles
from app.core.config import get_settings
from app.models import OrderEvent, OrderStatus, Role, ServiceOrder, User
from app.schemas.orders import (
    AssignRequest,
    CreatedOrder,
    OrderCreate,
    OrderRead,
    PartLink,
    PaymentCreate,
    PaymentRead,
    QuoteDecision,
    QuoteUpdate,
    TransitionRequest,
)
from app.services import orders
from app.services.presentation import order_view

router = APIRouter(prefix="/orders", tags=["Ordens de serviço"])
reception = require_roles(Role.ADMIN, Role.FRONT_DESK)


def tracking_url(token):
    return f"{get_settings().public_base_url.rstrip('/')}/status/{token}"


@router.get("", response_model=list[OrderRead], response_model_exclude_none=True)
def list_orders(db: DatabaseSession, user: CurrentUser, status: OrderStatus | None = None,
                tech_id: int | None = None, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    query = select(ServiceOrder).order_by(ServiceOrder.created_at, ServiceOrder.id).offset(offset).limit(limit)
    if status:
        query = query.where(ServiceOrder.status == status)
    if tech_id:
        query = query.where(ServiceOrder.tech_id == tech_id)
    return [order_view(order, user) for order in db.scalars(query)]


@router.post("", response_model=CreatedOrder, response_model_exclude_none=True, status_code=201)
def create_order(payload: OrderCreate, db: DatabaseSession, user: User = Depends(reception)):
    order, token = orders.create_order(db, device_id=payload.device_id,
                                      problem_description=payload.problem_description,
                                      physical_checklist=payload.physical_checklist.model_dump())
    return CreatedOrder(order=order_view(order, user), tracking_url=tracking_url(token))


@router.get("/{order_id}", response_model=OrderRead, response_model_exclude_none=True)
def get_order(order_id: int, db: DatabaseSession, user: CurrentUser):
    return order_view(orders.get_order(db, order_id), user)


@router.post("/{order_id}/assign", response_model=OrderRead, response_model_exclude_none=True)
def assign_technician(order_id: int, payload: AssignRequest, db: DatabaseSession,
                      user: User = Depends(require_roles(Role.ADMIN))):
    order = orders.get_order(db, order_id, lock=True)
    if order.status not in (OrderStatus.INTAKE, OrderStatus.ANALYZING):
        raise HTTPException(409, "Atribua o técnico antes de enviar o orçamento.")
    tech = db.get(User, payload.tech_id)
    if not tech or not tech.is_active or tech.role != Role.TECH:
        raise HTTPException(422, "Informe um técnico ativo.")
    order.tech_id = tech.id
    db.add(OrderEvent(order_id=order.id, from_status=order.status, to_status=order.status,
                      note="Técnico responsável atribuído.", actor_id=user.id))
    db.flush()
    db.expire(order, ["events"])
    return order_view(order, user)


@router.post("/{order_id}/transition", response_model=OrderRead, response_model_exclude_none=True)
def transition(order_id: int, payload: TransitionRequest, db: DatabaseSession, user: CurrentUser):
    order = orders.get_order(db, order_id, lock=True)
    orders.transition_order(db, order, payload.status, user)
    return order_view(order, user)


@router.put("/{order_id}/quote", response_model=OrderRead, response_model_exclude_none=True)
def update_quote(order_id: int, payload: QuoteUpdate, db: DatabaseSession, user: CurrentUser):
    order = orders.get_order(db, order_id, lock=True)
    if user.role == Role.TECH and payload.commission_amount is not None and payload.commission_amount != order.commission_amount:
        raise HTTPException(403, "Somente o administrador define a comissão.")
    values = payload.model_dump()
    values["commission_amount"] = payload.commission_amount if payload.commission_amount is not None else order.commission_amount
    orders.update_quote(db, order, **values, actor=user)
    return order_view(order, user)


@router.put("/{order_id}/parts/{part_id}", response_model=OrderRead, response_model_exclude_none=True)
def set_part(order_id: int, part_id: int, payload: PartLink, db: DatabaseSession, user: CurrentUser):
    order = orders.get_order(db, order_id, lock=True)
    orders.set_part(db, order, part_id=part_id, quantity=payload.quantity, actor=user)
    return order_view(order, user)


@router.post("/{order_id}/decision", response_model=OrderRead, response_model_exclude_none=True)
def decide_quote(order_id: int, payload: QuoteDecision, db: DatabaseSession, user: User = Depends(reception)):
    order = orders.get_order(db, order_id, lock=True)
    orders.decide_quote(db, order, **payload.model_dump(), actor=user)
    return order_view(order, user)


@router.post("/{order_id}/payments", response_model=PaymentRead, status_code=201)
def record_payment(order_id: int, payload: PaymentCreate, db: DatabaseSession, user: User = Depends(reception)):
    order = orders.get_order(db, order_id, lock=True)
    return orders.record_payment(db, order, **payload.model_dump(), actor=user)


@router.get("/{order_id}/payments", response_model=list[PaymentRead])
def list_payments(order_id: int, db: DatabaseSession, user: User = Depends(reception)):
    return orders.get_order(db, order_id).payments


@router.post("/{order_id}/tracking-link")
def rotate_tracking_link(order_id: int, db: DatabaseSession, user: User = Depends(reception)):
    order = orders.get_order(db, order_id, lock=True)
    token = secrets.token_urlsafe(32)
    order.tracking_token_hash = hashlib.sha256(token.encode()).hexdigest()
    db.add(OrderEvent(order_id=order.id, from_status=order.status, to_status=order.status,
                      note="Link de acompanhamento renovado; link anterior revogado.", actor_id=user.id))
    db.flush()
    return {"tracking_url": tracking_url(token)}


@router.get("/{order_id}/receipt", response_class=HTMLResponse)
def receipt(order_id: int, db: DatabaseSession, user: User = Depends(reception)):
    order = orders.get_order(db, order_id)
    paid = sum(p.amount for p in order.payments)
    def brl(value):
        return f"R$ {value:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
    rows = "".join(f"<tr><td>{p.id}</td><td>{p.created_at:%d/%m/%Y %H:%M} UTC</td><td>{escape(p.method)}</td><td>{brl(p.amount)}</td></tr>" for p in order.payments)
    return HTMLResponse(f"""<!doctype html><html lang="pt-BR"><meta charset="utf-8"><title>Recibo OS {order.id}</title>
    <style>body{{font:16px system-ui;max-width:800px;margin:60px auto;padding:24px;color:#17223b}}h1{{color:#146b63}}table{{width:100%;border-collapse:collapse}}td,th{{text-align:left;padding:12px;border-bottom:1px solid #ddd}}footer{{margin-top:40px;color:#64748b}}</style>
    <h1>ZirphasOS</h1><h2>Recibo de pagamentos · OS #{order.id:05d}</h2>
    <p>Cliente: {escape(order.device.client.name)}</p><p>Aparelho: {escape(order.device.brand)} {escape(order.device.model)}</p>
    <table><thead><tr><th>Pagamento</th><th>Data</th><th>Meio</th><th>Valor</th></tr></thead><tbody>{rows}</tbody></table>
    <p>Total do serviço: <strong>{brl(order.total_amount)}</strong></p><p>Valor recebido: <strong>{brl(paid)}</strong></p>
    <p>Saldo: <strong>{brl(order.total_amount - paid)}</strong></p><footer>Recibo não fiscal. Use a função Imprimir do navegador para salvar em PDF.</footer></html>""",
    headers={"Cache-Control": "no-store"})

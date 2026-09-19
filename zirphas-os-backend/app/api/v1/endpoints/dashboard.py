from collections import Counter
from decimal import Decimal

from fastapi import APIRouter, Depends
from fastapi.encoders import jsonable_encoder
from sqlalchemy import func, select

from app.api.dependencies import DatabaseSession, require_roles
from app.models import CashEntry, OrderStatus, Part, Payment, Role, ServiceOrder, User

router = APIRouter(prefix="/dashboard", tags=["Gestão financeira"], dependencies=[Depends(require_roles(Role.ADMIN))])


@router.get("")
def dashboard(db: DatabaseSession):
    zero = Decimal("0.00")
    cash_total = db.scalar(select(func.coalesce(func.sum(CashEntry.amount), 0)))
    advances = db.scalar(select(func.coalesce(func.sum(Payment.amount), 0)).join(ServiceOrder).where(
        ServiceOrder.status != OrderStatus.DELIVERED))
    all_orders = db.scalars(select(ServiceOrder).order_by(ServiceOrder.id)).all()
    finished = [o for o in all_orders if o.completed_at]
    status_counts = Counter(o.status.value for o in all_orders)
    technicians = db.scalars(select(User).where(User.role == Role.TECH).order_by(User.id)).all()
    productivity = []
    for tech in technicians:
        assigned = [o for o in all_orders if o.tech_id == tech.id]
        completed = [o for o in assigned if o.completed_at]
        turnaround = [(o.completed_at - o.created_at).total_seconds() / 3600 for o in completed]
        productivity.append({"tech_id": tech.id, "name": tech.name, "completed_orders": len(completed),
                             "open_orders": sum(o.status not in (OrderStatus.COMPLETED, OrderStatus.DELIVERED) for o in assigned),
                             "average_completion_hours": round(sum(turnaround) / len(turnaround), 2) if turnaround else None})
    low_stock = db.scalars(select(Part).where(Part.quantity <= Part.min_quantity).order_by(Part.quantity, Part.id)).all()
    daily = db.execute(select(func.date(CashEntry.created_at), func.sum(CashEntry.amount)).group_by(
        func.date(CashEntry.created_at)).order_by(func.date(CashEntry.created_at).desc()).limit(30)).all()
    return jsonable_encoder({
        "currency": "BRL", "cash_recognition": "delivery", "cash_total": cash_total,
        "advance_payments": advances,
        "completed_revenue": sum((o.total_amount for o in finished), zero),
        "parts_cost": sum((o.parts_cost for o in finished), zero),
        "commissions": sum((o.commission_amount for o in finished), zero),
        "net_profit": sum((o.net_profit for o in finished), zero),
        "orders_by_status": {s.value: status_counts[s.value] for s in OrderStatus},
        "cash_by_day": [{"date": str(day), "amount": amount} for day, amount in daily],
        "technical_productivity": productivity,
        "critical_stock": [{"id": p.id, "name": p.name, "sku": p.sku, "quantity": p.quantity, "min_quantity": p.min_quantity} for p in low_stock],
        "service_margins": [{"order_id": o.id, "revenue": o.total_amount, "parts_cost": o.parts_cost,
                              "commission": o.commission_amount, "net_profit": o.net_profit,
                              "margin_percent": round(o.net_profit / o.total_amount * 100, 2) if o.total_amount else None} for o in finished],
    }, custom_encoder={Decimal: str})

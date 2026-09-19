from decimal import Decimal

from app.models import Role
from app.models.enums import STATUS_LABELS, OrderStatus
from app.schemas.orders import AttachmentRead, EventRead, LinkedPartRead, OrderDeviceRead, OrderRead, PublicOrder


def order_view(order, user) -> OrderRead:
    admin = user.role == Role.ADMIN
    paid = sum((payment.amount for payment in order.payments), Decimal("0.00"))
    return OrderRead(
        id=order.id, device_id=order.device_id, device=OrderDeviceRead.model_validate(order.device), tech_id=order.tech_id,
        status=order.status, status_label=STATUS_LABELS[order.status],
        problem_description=order.problem_description, technical_report=order.technical_report,
        physical_checklist=order.physical_checklist, labor_amount=order.labor_amount,
        total_amount=order.total_amount, commission_amount=order.commission_amount if admin else None,
        parts_cost=order.parts_cost if admin else None, net_profit=order.net_profit if admin else None,
        paid_amount=paid, balance_due=max(Decimal("0.00"), order.total_amount - paid),
        quote_version=order.quote_version, approved_quote_version=order.approved_quote_version,
        created_at=order.created_at, updated_at=order.updated_at,
        completed_at=order.completed_at, delivered_at=order.delivered_at,
        parts=[LinkedPartRead(part_id=p.part_id, name=p.part.name, quantity=p.quantity,
                              unit_price=p.unit_price, unit_cost=p.unit_cost if admin else None) for p in order.parts],
        attachments=[AttachmentRead.model_validate(a) for a in order.attachments],
        events=[EventRead.model_validate(e) for e in order.events],
    )


def public_view(order) -> PublicOrder:
    # Draft diagnoses and prices are not offers; publish only after submission.
    published = order.status not in (OrderStatus.INTAKE, OrderStatus.ANALYZING)
    return PublicOrder(
        id=order.id, status=order.status, status_label=STATUS_LABELS[order.status],
        device=f"{order.device.brand} {order.device.model}",
        technical_report=order.technical_report if published else None,
        labor_amount=order.labor_amount if published else Decimal("0.00"),
        total_amount=order.total_amount if published else Decimal("0.00"),
        quote_version=order.quote_version,
        can_decide=order.status == OrderStatus.AWAITING_APPROVAL,
        approved_at=order.approved_at, updated_at=order.updated_at,
        parts=[{"name": p.part.name, "quantity": p.quantity, "unit_price": str(p.unit_price)} for p in order.parts] if published else [],
    )

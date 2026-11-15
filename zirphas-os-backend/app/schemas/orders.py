from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import Field

from app.models.enums import OrderStatus
from app.schemas.common import InputModel, Money, OutputModel


class PhysicalChecklist(InputModel):
    powers_on: bool = Field(strict=True)
    screen_condition: str = Field(min_length=1, max_length=500)
    case_condition: str = Field(min_length=1, max_length=500)
    accessories: list[str] = Field(default_factory=list, max_length=20)
    notes: str = Field(default="", max_length=2000)


class OrderCreate(InputModel):
    device_id: int = Field(gt=0)
    problem_description: str = Field(min_length=3, max_length=10000)
    physical_checklist: PhysicalChecklist


class TransitionRequest(InputModel):
    status: OrderStatus


class AssignRequest(InputModel):
    tech_id: int = Field(gt=0)


class QuoteUpdate(InputModel):
    labor_amount: Money
    commission_amount: Money | None = None
    technical_report: str = Field(min_length=3, max_length=10000)


class PartLink(InputModel):
    quantity: int = Field(ge=0, le=1000000, strict=True)


class QuoteDecision(InputModel):
    approved: bool = Field(strict=True)
    quote_version: int = Field(ge=1)


class PaymentCreate(InputModel):
    amount: Money = Field(gt=0)
    method: Literal["cash", "pix", "credit_card", "debit_card", "bank_transfer"]
    idempotency_key: str = Field(min_length=8, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")


class PaymentRead(OutputModel):
    id: int
    order_id: int
    amount: Money
    method: str
    idempotency_key: str
    received_by: int
    created_at: datetime


class AttachmentRead(OutputModel):
    id: int
    order_id: int
    original_name: str
    media_type: str
    created_at: datetime


class EventRead(OutputModel):
    from_status: OrderStatus | None
    to_status: OrderStatus
    note: str
    actor_id: int | None
    created_at: datetime


class LinkedPartRead(OutputModel):
    part_id: int
    name: str
    quantity: int
    unit_price: Money
    unit_cost: Money | None = None


class OrderDeviceRead(OutputModel):
    id: int
    kind: str
    brand: str
    model: str
    imei_serial: str


class OrderRead(OutputModel):
    id: int
    device_id: int
    device: OrderDeviceRead
    tech_id: int | None
    status: OrderStatus
    status_label: str
    problem_description: str
    technical_report: str | None
    physical_checklist: dict
    labor_amount: Money
    total_amount: Money
    commission_amount: Money | None = None
    parts_cost: Money | None = None
    net_profit: Decimal | None = None
    paid_amount: Money
    balance_due: Money
    quote_version: int
    approved_quote_version: int | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None
    delivered_at: datetime | None
    parts: list[LinkedPartRead]
    attachments: list[AttachmentRead]
    events: list[EventRead]


class CreatedOrder(OutputModel):
    order: OrderRead
    tracking_url: str


class PublicOrder(OutputModel):
    id: int
    status: OrderStatus
    status_label: str
    device: str
    technical_report: str | None
    labor_amount: Money
    total_amount: Money
    quote_version: int
    can_decide: bool
    approved_at: datetime | None
    updated_at: datetime
    parts: list[dict]

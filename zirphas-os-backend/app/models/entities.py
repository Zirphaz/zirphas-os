from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, DateTime, Enum, ForeignKey, Integer, JSON, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, utcnow
from app.models.enums import OrderStatus, Role


def enum_column(enum, name):
    return Enum(enum, name=name, values_callable=lambda cls: [item.value for item in cls],
                native_enum=False, create_constraint=True, validate_strings=True)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[Role] = mapped_column(enum_column(Role, "user_role"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    token_version: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Client(Base):
    __tablename__ = "clients"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    phone: Mapped[str] = mapped_column(String(32))
    document: Mapped[str | None] = mapped_column(String(32), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Device(Base):
    __tablename__ = "devices"
    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="RESTRICT"), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    brand: Mapped[str] = mapped_column(String(80))
    model: Mapped[str] = mapped_column(String(120))
    imei_serial: Mapped[str] = mapped_column(String(100), unique=True)
    client: Mapped[Client] = relationship()


class Part(Base):
    __tablename__ = "parts_inventory"
    __table_args__ = (
        CheckConstraint("quantity >= 0", name="positive_quantity"),
        CheckConstraint("cost_price >= 0 AND sale_price >= 0", name="positive_prices"),
        CheckConstraint("min_quantity >= 0", name="positive_minimum"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    sku: Mapped[str] = mapped_column(String(80), unique=True)
    quantity: Mapped[int] = mapped_column(Integer, default=0)
    cost_price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    sale_price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    min_quantity: Mapped[int] = mapped_column(Integer, default=1)


class ServiceOrder(Base):
    __tablename__ = "service_orders"
    __table_args__ = (
        CheckConstraint("labor_amount >= 0 AND total_amount >= 0 AND commission_amount >= 0 AND parts_cost >= 0", name="positive_amounts"),
        CheckConstraint("quote_version >= 0", name="positive_version"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="RESTRICT"), index=True)
    tech_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    status: Mapped[OrderStatus] = mapped_column(enum_column(OrderStatus, "order_status"), default=OrderStatus.INTAKE, index=True)
    problem_description: Mapped[str] = mapped_column(Text)
    technical_report: Mapped[str | None] = mapped_column(Text)
    physical_checklist: Mapped[dict] = mapped_column(JSON)
    labor_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"))
    total_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"))
    commission_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"))
    parts_cost: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"))
    net_profit: Mapped[Decimal | None] = mapped_column(Numeric(13, 2))
    quote_version: Mapped[int] = mapped_column(Integer, default=0)
    approved_quote_version: Mapped[int | None] = mapped_column(Integer)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approval_source: Mapped[str | None] = mapped_column(String(32))
    tracking_token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    device: Mapped[Device] = relationship()
    technician: Mapped[User | None] = relationship()
    parts: Mapped[list[OrderPart]] = relationship(back_populates="order", cascade="all, delete-orphan", order_by="OrderPart.part_id")
    payments: Mapped[list[Payment]] = relationship(back_populates="order", order_by="Payment.id")
    events: Mapped[list[OrderEvent]] = relationship(order_by="OrderEvent.id")
    attachments: Mapped[list[Attachment]] = relationship(order_by="Attachment.id")


class OrderPart(Base):
    __tablename__ = "os_parts"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="positive_quantity"),
        CheckConstraint("unit_cost >= 0 AND unit_price >= 0", name="positive_prices"),
    )
    order_id: Mapped[int] = mapped_column(ForeignKey("service_orders.id", ondelete="RESTRICT"), primary_key=True)
    part_id: Mapped[int] = mapped_column(ForeignKey("parts_inventory.id", ondelete="RESTRICT"), primary_key=True)
    quantity: Mapped[int] = mapped_column(Integer)
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    order: Mapped[ServiceOrder] = relationship(back_populates="parts")
    part: Mapped[Part] = relationship()


class Payment(Base):
    __tablename__ = "payments"
    __table_args__ = (
        UniqueConstraint("order_id", "idempotency_key"),
        CheckConstraint("amount > 0", name="positive_amount"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("service_orders.id", ondelete="RESTRICT"), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    method: Mapped[str] = mapped_column(String(24))
    idempotency_key: Mapped[str] = mapped_column(String(100))
    received_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    order: Mapped[ServiceOrder] = relationship(back_populates="payments")


class CashEntry(Base):
    __tablename__ = "cash_entries"
    __table_args__ = (CheckConstraint("amount >= 0", name="positive_amount"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("service_orders.id", ondelete="RESTRICT"), unique=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class OrderEvent(Base):
    __tablename__ = "order_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("service_orders.id", ondelete="RESTRICT"), index=True)
    from_status: Mapped[OrderStatus | None] = mapped_column(enum_column(OrderStatus, "event_from_status"))
    to_status: Mapped[OrderStatus] = mapped_column(enum_column(OrderStatus, "event_to_status"))
    note: Mapped[str] = mapped_column(Text)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Attachment(Base):
    __tablename__ = "attachments"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("service_orders.id", ondelete="RESTRICT"), index=True)
    storage_name: Mapped[str] = mapped_column(String(100), unique=True)
    original_name: Mapped[str] = mapped_column(String(255))
    media_type: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

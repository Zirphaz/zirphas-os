import hashlib
import re

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.api.dependencies import DatabaseSession
from app.models import ServiceOrder
from app.schemas.orders import PublicOrder, QuoteDecision
from app.services.orders import decide_quote
from app.services.presentation import public_view

router = APIRouter(prefix="/public", tags=["Acompanhamento do cliente"])


def find_public_order(db, token, *, lock=False):
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
        raise HTTPException(404, "Link de acompanhamento inválido ou revogado.")
    digest = hashlib.sha256(token.encode()).hexdigest()
    query = select(ServiceOrder).where(ServiceOrder.tracking_token_hash == digest)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    order = db.scalar(query)
    if order is None:
        raise HTTPException(404, "Link de acompanhamento inválido ou revogado.")
    return order


@router.get("/status/{token}", response_model=PublicOrder)
def get_status(token: str, db: DatabaseSession):
    return public_view(find_public_order(db, token))


@router.post("/status/{token}/decision", response_model=PublicOrder)
def customer_decision(token: str, payload: QuoteDecision, db: DatabaseSession):
    order = find_public_order(db, token, lock=True)
    decide_quote(db, order, **payload.model_dump())
    return public_view(order)

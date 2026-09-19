from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select

from app.api.dependencies import CurrentUser, DatabaseSession, require_roles
from app.models import Part, Role
from app.schemas.inventory import PartCreate, PartRead, PartUpdate, StockAdjustment

router = APIRouter(prefix="/inventory", tags=["Estoque"])


@router.get("", response_model=list[PartRead], response_model_exclude_none=True)
def list_parts(db: DatabaseSession, user: CurrentUser, low_stock: bool = False,
               limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    query = select(Part).order_by(Part.id).offset(offset).limit(limit)
    if low_stock:
        query = query.where(Part.quantity <= Part.min_quantity)
    rows = []
    for part in db.scalars(query):
        data = PartRead.model_validate(part)
        if user.role != Role.ADMIN:
            data.cost_price = None
        rows.append(data)
    return rows


@router.post("", response_model=PartRead, status_code=201, dependencies=[Depends(require_roles(Role.ADMIN))])
def create_part(payload: PartCreate, db: DatabaseSession):
    part = Part(**payload.model_dump())
    db.add(part)
    db.flush()
    return part


def locked_part(db, part_id):
    part = db.scalar(select(Part).where(Part.id == part_id).with_for_update())
    if not part:
        raise HTTPException(404, "Peça não encontrada.")
    return part


@router.put("/{part_id}", response_model=PartRead, dependencies=[Depends(require_roles(Role.ADMIN))])
def update_part(part_id: int, payload: PartUpdate, db: DatabaseSession):
    part = locked_part(db, part_id)
    for key, value in payload.model_dump().items():
        setattr(part, key, value)
    db.flush()
    return part


@router.post("/{part_id}/adjustments", response_model=PartRead, dependencies=[Depends(require_roles(Role.ADMIN))])
def adjust_stock(part_id: int, payload: StockAdjustment, db: DatabaseSession):
    part = locked_part(db, part_id)
    if not payload.delta or part.quantity + payload.delta < 0:
        raise HTTPException(409, "Ajuste deve ser diferente de zero e não pode tornar o estoque negativo.")
    part.quantity += payload.delta
    db.flush()
    return part

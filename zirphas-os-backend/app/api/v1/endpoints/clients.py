from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select

from app.api.dependencies import DatabaseSession, require_roles
from app.models import Client, Device, Role
from app.schemas.clients import ClientCreate, ClientRead, DeviceCreate, DeviceRead

router = APIRouter(prefix="/clients", tags=["Clientes e aparelhos"],
                   dependencies=[Depends(require_roles(Role.ADMIN, Role.FRONT_DESK))])


def find_client(db, client_id):
    client = db.get(Client, client_id)
    if not client:
        raise HTTPException(404, "Cliente não encontrado.")
    return client


@router.get("", response_model=list[ClientRead])
def list_clients(db: DatabaseSession, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    return db.scalars(select(Client).order_by(Client.id).offset(offset).limit(limit)).all()


@router.post("", response_model=ClientRead, status_code=201)
def create_client(payload: ClientCreate, db: DatabaseSession):
    client = Client(**payload.model_dump())
    db.add(client)
    db.flush()
    return client


@router.get("/{client_id}", response_model=ClientRead)
def get_client(client_id: int, db: DatabaseSession):
    return find_client(db, client_id)


@router.put("/{client_id}", response_model=ClientRead)
def update_client(client_id: int, payload: ClientCreate, db: DatabaseSession):
    client = find_client(db, client_id)
    for key, value in payload.model_dump().items():
        setattr(client, key, value)
    db.flush()
    return client


@router.delete("/{client_id}", status_code=204)
def delete_client(client_id: int, db: DatabaseSession):
    client = find_client(db, client_id)
    if db.scalar(select(Device.id).where(Device.client_id == client_id).limit(1)):
        raise HTTPException(409, "Cliente possui aparelhos vinculados; preserve o histórico de atendimento.")
    db.delete(client)
    db.flush()
    return Response(status_code=204)


@router.get("/{client_id}/devices", response_model=list[DeviceRead])
def list_devices(client_id: int, db: DatabaseSession):
    find_client(db, client_id)
    return db.scalars(select(Device).where(Device.client_id == client_id).order_by(Device.id)).all()


@router.post("/{client_id}/devices", response_model=DeviceRead, status_code=201)
def create_device(client_id: int, payload: DeviceCreate, db: DatabaseSession):
    find_client(db, client_id)
    device = Device(client_id=client_id, **payload.model_dump())
    db.add(device)
    db.flush()
    return device

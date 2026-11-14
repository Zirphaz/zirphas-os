from datetime import datetime

from pydantic import Field

from app.schemas.common import InputModel, OutputModel, ShortText


class ClientCreate(InputModel):
    name: ShortText
    phone: str = Field(min_length=8, max_length=32)
    document: str | None = Field(default=None, min_length=5, max_length=32)


class ClientRead(OutputModel):
    id: int
    name: str
    phone: str
    document: str | None
    created_at: datetime


class DeviceCreate(InputModel):
    kind: str = Field(min_length=1, max_length=40)
    brand: str = Field(min_length=1, max_length=80)
    model: str = Field(min_length=1, max_length=120)
    imei_serial: str = Field(min_length=3, max_length=100)


class DeviceRead(OutputModel):
    id: int
    client_id: int
    kind: str
    brand: str
    model: str
    imei_serial: str

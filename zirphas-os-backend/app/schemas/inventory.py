from pydantic import Field

from app.schemas.common import InputModel, Money, OutputModel, ShortText


class PartCreate(InputModel):
    name: ShortText
    sku: str = Field(min_length=1, max_length=80)
    quantity: int = Field(default=0, ge=0, le=1000000, strict=True)
    cost_price: Money
    sale_price: Money
    min_quantity: int = Field(default=1, ge=0, le=1000000, strict=True)


class PartRead(OutputModel):
    id: int
    name: str
    sku: str
    quantity: int
    sale_price: Money
    min_quantity: int
    cost_price: Money | None = None


class StockAdjustment(InputModel):
    delta: int = Field(ge=-1000000, le=1000000, strict=True)


class PartUpdate(InputModel):
    name: ShortText
    cost_price: Money
    sale_price: Money
    min_quantity: int = Field(ge=0, le=1000000, strict=True)

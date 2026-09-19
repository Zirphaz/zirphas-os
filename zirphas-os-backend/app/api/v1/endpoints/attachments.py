from io import BytesIO
from pathlib import Path
from uuid import uuid4
import warnings

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse
from PIL import Image, ImageOps, UnidentifiedImageError

from app.api.dependencies import CurrentUser, DatabaseSession, require_roles
from app.core.config import get_settings
from app.models import Attachment, OrderStatus, Role, User
from app.schemas.orders import AttachmentRead
from app.services.orders import get_order
from app.services.uploads import register_pending_upload

router = APIRouter(prefix="/orders", tags=["Fotos de entrada"])


@router.post("/{order_id}/attachments", response_model=AttachmentRead, status_code=201)
def upload_photo(order_id: int, file: UploadFile, db: DatabaseSession,
                 user: User = Depends(require_roles(Role.ADMIN, Role.FRONT_DESK))):
    order = get_order(db, order_id, lock=True)
    if order.status not in (OrderStatus.INTAKE, OrderStatus.ANALYZING):
        raise HTTPException(409, "Fotos de entrada ficam bloqueadas após o envio do orçamento.")
    if len(order.attachments) >= 20:
        raise HTTPException(409, "Limite de 20 fotos por OS atingido.")
    settings = get_settings()
    content = file.file.read(settings.max_upload_bytes + 1)
    if len(content) > settings.max_upload_bytes:
        raise HTTPException(413, "A foto excede o tamanho permitido.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(content)) as source:
                if source.format not in {"JPEG", "PNG", "WEBP"}:
                    raise ValueError("Formato não permitido")
                if source.width * source.height > 20_000_000:
                    raise ValueError("Resolução excessiva")
                source.load()
                sanitized = ImageOps.exif_transpose(source).convert("RGB")
                output = BytesIO()
                sanitized.save(output, format="JPEG", quality=90)
                sanitized.close()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise HTTPException(422, "Envie uma foto JPEG, PNG ou WebP válida, com até 20 megapixels.") from None
    directory = Path(settings.upload_dir)
    directory.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid4().hex}.jpg"
    target = directory / filename
    register_pending_upload(db, target)
    target.write_bytes(output.getvalue())
    original_name = (file.filename or "foto").replace("\\", "/").rsplit("/", 1)[-1][:255]
    photo = Attachment(order_id=order.id, storage_name=filename, original_name=original_name,
                       media_type="image/jpeg")
    db.add(photo)
    db.flush()
    return photo


@router.get("/{order_id}/attachments/{attachment_id}")
def download_photo(order_id: int, attachment_id: int, db: DatabaseSession, user: CurrentUser):
    photo = db.get(Attachment, attachment_id)
    if not photo or photo.order_id != order_id:
        raise HTTPException(404, "Foto não encontrada.")
    target = Path(get_settings().upload_dir) / photo.storage_name
    if not target.is_file():
        raise HTTPException(404, "Arquivo da foto indisponível.")
    return FileResponse(target, media_type=photo.media_type, filename=f"os-{order_id}-{photo.id}.jpg",
                        headers={"Cache-Control": "no-store"})

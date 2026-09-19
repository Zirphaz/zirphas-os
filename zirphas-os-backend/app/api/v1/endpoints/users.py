from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.dependencies import DatabaseSession, require_roles
from app.core.security import hash_password
from app.models import User
from app.models.enums import Role
from app.schemas.auth import UserCreate, UserRead, UserUpdate

router = APIRouter(prefix="/users", tags=["Funcionários"])
AdminUser = Annotated[User, Depends(require_roles(Role.ADMIN))]


@router.get("", response_model=list[UserRead])
def list_users(
    db: DatabaseSession,
    _admin: AdminUser,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
):
    return db.scalars(select(User).order_by(User.id).offset(offset).limit(limit)).all()


@router.post("", response_model=UserRead, status_code=201)
def create_user(payload: UserCreate, db: DatabaseSession, _admin: AdminUser):
    if db.scalar(select(User.id).where(User.email == payload.email)):
        raise HTTPException(status_code=409, detail="E-mail já cadastrado.")
    user = User(
        name=payload.name,
        email=str(payload.email),
        password_hash=hash_password(payload.password),
        role=payload.role,
        is_active=True,
        token_version=0,
    )
    db.add(user)
    try:
        db.flush()
    except IntegrityError:
        raise HTTPException(status_code=409, detail="E-mail já cadastrado.") from None
    return user


@router.patch("/{user_id}", response_model=UserRead)
def update_user(user_id: int, payload: UserUpdate, db: DatabaseSession, _admin: AdminUser):
    # Serialize admin changes to preserve at least one active administrator.
    active_admins = db.scalars(
        select(User)
        .where(User.role == Role.ADMIN, User.is_active.is_(True))
        .order_by(User.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).all()
    user = db.scalar(
        select(User).where(User.id == user_id).with_for_update()
        .execution_options(populate_existing=True)
    )
    if user is None:
        raise HTTPException(status_code=404, detail="Funcionário não encontrado.")
    values = payload.model_dump(exclude_unset=True)
    loses_admin_access = values.get("role", user.role) != Role.ADMIN or not values.get(
        "is_active", user.is_active
    )
    if user.role == Role.ADMIN and user.is_active and loses_admin_access and len(active_admins) <= 1:
        raise HTTPException(status_code=409, detail="Mantenha ao menos um administrador ativo.")
    invalidate = any(
        field in values and values[field] != getattr(user, field)
        for field in ("email", "role", "is_active")
    )
    password = values.pop("password", None)
    if password is not None:
        user.password_hash = hash_password(password)
        invalidate = True
    for field, value in values.items():
        setattr(user, field, value)
    if invalidate:
        user.token_version += 1
    try:
        db.flush()
    except IntegrityError:
        raise HTTPException(status_code=409, detail="E-mail já cadastrado.") from None
    return user

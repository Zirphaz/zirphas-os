from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select

from app.api.dependencies import CurrentUser, DatabaseSession
from app.core.config import get_settings
from app.core.security import DUMMY_PASSWORD_HASH, create_access_token, verify_password
from app.models import User
from app.schemas.auth import Token, UserRead

router = APIRouter(prefix="/auth", tags=["Autenticação"])


@router.post("/token", response_model=Token)
def login(db: DatabaseSession, form: Annotated[OAuth2PasswordRequestForm, Depends()]):
    user = db.scalar(select(User).where(User.email == form.username.strip().lower()))
    valid_password = len(form.password) <= 128 and verify_password(
        form.password, user.password_hash if user else DUMMY_PASSWORD_HASH
    )
    if not user or not valid_password or not user.is_active:
        raise HTTPException(
            status_code=401,
            detail="E-mail ou senha inválidos.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return Token(
        access_token=create_access_token(user.id, user.role, user.token_version),
        expires_in=get_settings().access_token_expire_minutes * 60,
    )


@router.get("/me", response_model=UserRead)
def read_current_user(user: CurrentUser):
    return user

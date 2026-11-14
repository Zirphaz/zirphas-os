from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select

from app.api.dependencies import CurrentUser, DatabaseSession
from app.core.config import get_settings
from app.core.login_throttle import login_throttle
from app.core.security import DUMMY_PASSWORD_HASH, create_access_token, verify_password
from app.core.security_log import log_security_event
from app.models import User
from app.schemas.auth import Token, UserRead

router = APIRouter(prefix="/auth", tags=["Autenticação"])


@router.post("/token", response_model=Token)
def login(request: Request, db: DatabaseSession, form: Annotated[OAuth2PasswordRequestForm, Depends()]):
    email = form.username.strip().lower()
    # The direct peer address is used on purpose: X-Forwarded-For is client-controlled
    # unless a trusted proxy rewrites it, and trusting it would let attackers rotate keys.
    client_ip = request.client.host if request.client else "unknown"

    retry_after = login_throttle.retry_after(client_ip, email)
    if retry_after is not None:
        log_security_event("auth.login_throttled", client_ip=client_ip, email=email, retry_after=retry_after)
        raise HTTPException(
            status_code=429,
            detail="Muitas tentativas de login. Tente novamente mais tarde.",
            headers={"Retry-After": str(retry_after)},
        )

    user = db.scalar(select(User).where(User.email == email))
    valid_password = len(form.password) <= 128 and verify_password(
        form.password, user.password_hash if user else DUMMY_PASSWORD_HASH
    )
    if not user or not valid_password or not user.is_active:
        login_throttle.record_failure(client_ip, email)
        log_security_event("auth.login_failure", client_ip=client_ip, email=email)
        raise HTTPException(
            status_code=401,
            detail="E-mail ou senha inválidos.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    login_throttle.reset(client_ip, email)
    log_security_event("auth.login_success", client_ip=client_ip, email=email, user_id=user.id)
    return Token(
        access_token=create_access_token(user.id, user.role, user.token_version),
        expires_in=get_settings().access_token_expire_minutes * 60,
    )


@router.get("/me", response_model=UserRead)
def read_current_user(user: CurrentUser):
    return user

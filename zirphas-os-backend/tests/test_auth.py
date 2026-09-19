"""Security boundaries: roles, account revocation, and administrator continuity."""

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.v1.endpoints import auth, users
from app.core.config import get_settings
from app.core.security import create_access_token, hash_password
from app.db import get_db
from app.models import User
from app.models.base import Base
from app.models.enums import Role

PASSWORD = "Long enough test password!"


@pytest.fixture
def auth_context():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions.begin() as db:
        for role in Role:
            db.add(User(
                name=role.value,
                email=f"{role.value}@example.com",
                role=role,
                password_hash=hash_password(PASSWORD),
                token_version=0,
            ))
    app = FastAPI()
    app.include_router(auth.router, prefix="/api/v1")
    app.include_router(users.router, prefix="/api/v1")

    def session_dependency():
        with sessions() as db:
            try:
                yield db
                db.commit()
            except Exception:
                db.rollback()
                raise

    app.dependency_overrides[get_db] = session_dependency
    with TestClient(app) as client:
        yield client, sessions
    engine.dispose()


def login(client, role=Role.ADMIN):
    response = client.post(
        "/api/v1/auth/token",
        data={"username": f"{role.value}@example.com", "password": PASSWORD},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_login_rejects_unknown_and_wrong_password_without_exposing_account(auth_context):
    client, _ = auth_context
    responses = [
        client.post("/api/v1/auth/token", data={"username": email, "password": "incorrect"})
        for email in ("admin@example.com", "unknown@example.com")
    ]
    assert all(response.status_code == 401 for response in responses)
    assert responses[0].json() == responses[1].json()
    assert responses[0].headers["www-authenticate"] == "Bearer"


def test_employee_management_requires_admin_and_omits_password_hash(auth_context):
    client, _ = auth_context
    assert client.get("/api/v1/users").status_code == 401
    assert client.get("/api/v1/users", headers=login(client, Role.TECH)).status_code == 403
    response = client.get("/api/v1/users", headers=login(client))
    assert response.status_code == 200
    assert len(response.json()) == 3
    assert all("password_hash" not in user for user in response.json())


@pytest.mark.parametrize("patch", [
    {"role": "front_desk"},
    {"is_active": False},
    {"password": "A replacement password!"},
])
def test_account_changes_revoke_existing_tokens(auth_context, patch):
    client, sessions = auth_context
    old_headers = login(client, Role.TECH)
    with sessions() as db:
        tech_id = db.scalar(select(User.id).where(User.role == Role.TECH))
    response = client.patch(f"/api/v1/users/{tech_id}", headers=login(client), json=patch)
    assert response.status_code == 200, response.text
    assert client.get("/api/v1/auth/me", headers=old_headers).status_code == 401


def test_database_role_is_authoritative_over_claim(auth_context):
    client, sessions = auth_context
    with sessions() as db:
        technician = db.scalar(select(User).where(User.role == Role.TECH))
        token = create_access_token(technician.id, Role.ADMIN, technician.token_version)
    assert client.get(
        "/api/v1/users", headers={"Authorization": f"Bearer {token}"}
    ).status_code == 403


@pytest.mark.parametrize("patch", [{"is_active": False}, {"role": "tech"}])
def test_cannot_remove_last_administrator(auth_context, patch):
    client, sessions = auth_context
    headers = login(client)
    with sessions() as db:
        admin_id = db.scalar(select(User.id).where(User.role == Role.ADMIN))
    assert client.patch(
        f"/api/v1/users/{admin_id}", headers=headers, json=patch
    ).status_code == 409
    assert client.get("/api/v1/users", headers=headers).status_code == 200


def test_duplicate_email_rolls_back_other_changes(auth_context):
    client, sessions = auth_context
    headers = login(client)
    with sessions() as db:
        tech_id = db.scalar(select(User.id).where(User.role == Role.TECH))
    response = client.patch(f"/api/v1/users/{tech_id}", headers=headers, json={
        "email": "admin@example.com", "name": "Should roll back"
    })
    assert response.status_code == 409
    with sessions() as db:
        user = db.get(User, tech_id)
        assert user.name == "tech"
        assert user.email == "tech@example.com"
        assert user.token_version == 0


def test_invalid_expired_and_wrong_audience_tokens_are_rejected(auth_context):
    client, sessions = auth_context
    with sessions() as db:
        admin = db.scalar(select(User).where(User.role == Role.ADMIN))
        valid = create_access_token(admin.id, Role.ADMIN, admin.token_version)
    payload = jwt.decode(valid, options={"verify_signature": False})
    expired = jwt.encode({**payload, "exp": 0}, get_settings().secret_key, algorithm="HS256")
    wrong_audience = jwt.encode(
        {**payload, "aud": "another-app"}, get_settings().secret_key, algorithm="HS256"
    )
    tampered = valid[:-12] + "INVALIDVALUE"
    for token in (expired, wrong_audience, tampered):
        assert client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
        ).status_code == 401

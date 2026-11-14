"""HTTP tests run independently of local .env and production credentials."""
import os
from pathlib import Path

os.environ["DATABASE_URL"] = "sqlite://"
os.environ["SECRET_KEY"] = "test-only-key-with-more-than-thirty-two-characters"
os.environ["PUBLIC_BASE_URL"] = "http://testserver"

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings
from app.core.login_throttle import login_throttle
from app.core.security import create_access_token, hash_password
from app.db import get_db
from app.main import app
from app.models import Base, Role, User


@pytest.fixture(autouse=True)
def reset_login_throttle():
    login_throttle.clear()
    yield
    login_throttle.clear()


@pytest.fixture
def api(tmp_path):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    @event.listens_for(engine, "connect")
    def foreign_keys(connection, record):
        connection.execute("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    headers = {}
    with factory.begin() as db:
        password_hash = hash_password("Testing strong password!")
        for role in Role:
            user = User(name=role.value, email=f"{role.value}@example.com", role=role,
                        password_hash=password_hash, is_active=True, token_version=0)
            db.add(user)
            db.flush()
            headers[role.value] = {"Authorization": "Bearer " + create_access_token(user.id, role, user.token_version)}
        user = User(name="Second tech", email="tech2@example.com", role=Role.TECH,
                    password_hash=password_hash, is_active=True, token_version=0)
        db.add(user)
        db.flush()
        headers["other_tech"] = {"Authorization": "Bearer " + create_access_token(user.id, user.role, user.token_version)}

    def get_test_db():
        with factory() as db:
            try:
                yield db
                db.commit()
            except Exception:
                db.rollback()
                raise

    settings = get_settings()
    original_dir = settings.upload_dir
    settings.upload_dir = tmp_path / "photos"
    app.dependency_overrides[get_db] = get_test_db
    with TestClient(app) as client:
        yield client, headers, factory
    app.dependency_overrides.clear()
    settings.upload_dir = original_dir
    engine.dispose()

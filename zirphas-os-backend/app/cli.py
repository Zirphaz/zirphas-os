"""Explicit first-administrator creation: python -m app.cli create-admin."""

import argparse
import getpass
import os

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.security import hash_password
from app.db import SessionLocal
from app.models import User
from app.models.enums import Role
from app.schemas.auth import UserCreate


def create_admin() -> None:
    name = os.environ.get("ADMIN_NAME") or input("Nome do administrador: ").strip()
    email = os.environ.get("ADMIN_EMAIL") or input("E-mail: ").strip()
    password = os.environ.get("ADMIN_PASSWORD")
    if not password:
        password = getpass.getpass("Senha (mínimo 12 caracteres): ")
        if password != getpass.getpass("Confirme a senha: "):
            raise SystemExit("As senhas não coincidem.")
    try:
        payload = UserCreate(name=name, email=email, password=password, role=Role.ADMIN)
    except ValidationError as exc:
        # Never print the Pydantic error object: it can include a plaintext password.
        details = "; ".join(
            f"{'.'.join(map(str, error['loc']))}: {error['msg']}"
            for error in exc.errors(include_input=False)
        )
        raise SystemExit(f"Dados inválidos: {details}") from None
    try:
        with SessionLocal.begin() as db:
            if db.scalar(select(User.id).where(User.email == payload.email)):
                raise SystemExit("Este e-mail já está cadastrado; nenhuma conta foi alterada.")
            db.add(User(
                name=payload.name,
                email=str(payload.email),
                password_hash=hash_password(payload.password),
                role=Role.ADMIN,
                is_active=True,
                token_version=0,
            ))
    except IntegrityError:
        raise SystemExit("Este e-mail já está cadastrado; nenhuma conta foi alterada.") from None
    print("Administrador criado. Entre pela API com seu e-mail e senha.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Administração do ZirphasOS")
    parser.add_argument("command", choices=["create-admin"])
    parser.parse_args()
    create_admin()


if __name__ == "__main__":
    main()

import os
from pathlib import Path

import pytest
from argon2 import PasswordHasher

PASSWORD = "test-admin-pass"
PASSWORD_HASH = PasswordHasher().hash(PASSWORD)


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ENV_FILE", str(tmp_path / "missing.env"))
    monkeypatch.setenv("SESSION_SECRET", "s" * 48)
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", PASSWORD_HASH)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MASTER_KEY_FILE", str(tmp_path / "master.key"))
    monkeypatch.setenv("WALLET_ROOT", str(tmp_path / "wallets"))
    monkeypatch.setenv("COOKIE_SECURE", "false")
    from app.config import get_settings
    from app.main import create_app

    get_settings.cache_clear()
    app = create_app()
    from fastapi.testclient import TestClient

    with TestClient(app) as test_client:
        yield test_client


def sign_in(client, password: str = PASSWORD) -> str:
    page = client.get("/login")
    assert page.status_code == 200
    token = page.text.split('name="csrf-token" content="', 1)[1].split('"', 1)[0]
    response = client.post(
        "/api/login",
        json={"password": password},
        headers={"X-CSRF-Token": token},
    )
    assert response.status_code == 200, response.text
    return response.json()["csrf_token"]

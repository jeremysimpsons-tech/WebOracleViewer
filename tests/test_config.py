import os
from pathlib import Path

from app.admin_password import hash_is_stored
from app.config import _apply_env_file


def test_env_file_keeps_dollar_signs(tmp_path: Path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("ADMIN_PASSWORD_HASH='$argon2id$v=19$m=1,t=1'\nBIND_PORT=8080\n", encoding="utf-8")
    monkeypatch.delenv("ADMIN_PASSWORD_HASH", raising=False)
    monkeypatch.setenv("BIND_PORT", "9999")
    _apply_env_file(env)
    assert os.environ["ADMIN_PASSWORD_HASH"] == "$argon2id$v=19$m=1,t=1"
    assert os.environ["BIND_PORT"] == "9999"


def test_hash_check_accepts_only_an_argon2_line(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text("ADMIN_PASSWORD_HASH=\n", encoding="utf-8")
    assert hash_is_stored(env) is False
    env.write_text("ADMIN_PASSWORD_HASH='$argon2id$v=19$m=1'\n", encoding="utf-8")
    assert hash_is_stored(env) is True


def test_wallet_directory_must_stay_inside_the_root(tmp_path: Path):
    from app.errors import AppError
    from app.store import resolve_wallet_dir

    root = tmp_path / "wallets"
    root.mkdir()
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    try:
        resolve_wallet_dir(str(outside), root)
    except AppError as exc:
        assert "must be inside" in exc.message
    else:
        raise AssertionError("expected the wallet path to be rejected")

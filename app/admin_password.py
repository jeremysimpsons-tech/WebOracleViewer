"""Set or rotate the admin password hash in .env."""

from __future__ import annotations

import getpass
import os
import sys
from pathlib import Path

from argon2 import PasswordHasher

from app.config import PROJECT_ROOT, load_env, upsert_env_value


def hash_is_stored(path: Path) -> bool:
    if not path.is_file():
        return False
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("export "):
            line = line[7:].strip()
        if not line.startswith("ADMIN_PASSWORD_HASH="):
            continue
        value = line.split("=", 1)[1].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        return value.startswith("$argon2")
    return False


def main() -> None:
    path = PROJECT_ROOT / ".env"
    explicit = os.environ.get("ENV_FILE")
    if explicit:
        path = Path(explicit)
        if not path.is_file() and path.name == "missing.env":
            sys.exit("ENV_FILE does not point at a real environment file.")
    if len(sys.argv) > 1 and sys.argv[1] == "--check":
        raise SystemExit(0 if hash_is_stored(path) else 1)
    load_env()
    password = os.environ.get("ADMIN_PASSWORD", "")
    prompted = False
    if not password:
        prompted = True
        try:
            password = getpass.getpass("Web UI password (at least 12 characters): ")
            again = getpass.getpass("Repeat web UI password: ")
        except EOFError:
            sys.exit(
                "No password was entered. In a terminal, run: .venv/bin/python -m app.admin_password"
            )
        if password != again:
            sys.exit("The passwords did not match.")
    if len(password) < 12:
        sys.exit("Use at least 12 characters for the admin password.")
    hashed = PasswordHasher().hash(password)
    try:
        upsert_env_value(path, "ADMIN_PASSWORD_HASH", hashed)
    except OSError as exc:
        sys.exit(f"Could not write {path}: {exc}")
    if not hash_is_stored(path):
        sys.exit(f"The password hash was not saved in {path}.")
    print(f"Saved the web UI password hash in {path}.")
    print("Restart the service so the new password is used.")
    if not prompted:
        print("Unset ADMIN_PASSWORD in the environment. It was not written to .env.")


if __name__ == "__main__":
    main()

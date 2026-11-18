from tests.conftest import PASSWORD, sign_in


def _classic(name: str = "Lab") -> dict:
    return {
        "name": name,
        "username": "HR",
        "password": "s3cret-db-password",
        "mode": "classic",
        "host": "db.example.com",
        "port": 1521,
        "service_name": "ORCLPDB1",
        "protocol": "tcp",
        "ssl_server_dn_match": True,
        "read_only": False,
        "role": "",
    }


def test_login_page_and_api_require_a_session(client):
    home = client.get("/", follow_redirects=False)
    assert home.status_code == 303
    assert home.headers["location"] == "/login"
    page = client.get("/login")
    assert "script-src 'self'" in page.headers["content-security-policy"]
    assert client.get("/api/connections").status_code == 401
    assert client.get("/healthz").json()["status"] == "ok"


def test_wrong_password_and_missing_csrf(client):
    page = client.get("/login")
    token = page.text.split('name="csrf-token" content="', 1)[1].split('"', 1)[0]
    bad = client.post("/api/login", json={"password": "not-the-password"}, headers={"X-CSRF-Token": token})
    assert bad.status_code == 401
    assert bad.json()["detail"] == "Incorrect password."
    missing = client.post("/api/login", json={"password": PASSWORD})
    assert missing.status_code == 403


def test_saved_password_is_encrypted_and_not_returned(client):
    token = sign_in(client)
    created = client.post("/api/connections", json=_classic(), headers={"X-CSRF-Token": token})
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["password_set"] is True
    assert "password" not in body
    assert "s3cret-db-password" not in created.text
    stored = client.get("/api/connections", headers={"X-CSRF-Token": token}).json()
    assert stored["connections"][0]["name"] == "Lab"
    text = (client.app.state.settings.data_dir / "connections.json").read_text(encoding="utf-8")
    assert "s3cret-db-password" not in text
    assert "password_enc" in text


def test_sql_without_a_database_session_is_rejected(client):
    token = sign_in(client)
    response = client.post(
        "/api/sql/execute",
        json={"sql": "SELECT USER FROM dual"},
        headers={"X-CSRF-Token": token},
    )
    assert response.status_code == 409
    assert "Connect a database session" in response.json()["detail"]


def test_unreachable_host_is_a_clear_error(monkeypatch):
    import socket

    from app.db import open_connection
    from app.errors import AppError

    def fail_connect(**_kwargs):
        raise socket.gaierror(-5, "No address associated with hostname")

    monkeypatch.setattr("app.db.oracledb.connect", fail_connect)
    record = {
        "name": "Lab",
        "username": "HR",
        "mode": "classic",
        "host": "missing.example",
        "port": 1521,
        "service_name": "ORCLPDB1",
        "sid": "",
        "protocol": "tcp",
        "role": "",
        "ssl_server_dn_match": True,
    }
    try:
        open_connection(record, {"password": "s3cret-db-password", "wallet_password": ""}, None)
    except AppError as exc:
        assert exc.status == 400
        assert "missing.example" in exc.message
        assert "s3cret-db-password" not in exc.message
    else:
        raise AssertionError("unreachable host should be an application error")


def test_duplicate_name_and_sign_out(client):
    token = sign_in(client)
    headers = {"X-CSRF-Token": token}
    assert client.post("/api/connections", json=_classic(), headers=headers).status_code == 200
    again = client.post("/api/connections", json=_classic(), headers=headers)
    assert again.status_code == 409
    assert client.post("/api/logout", headers=headers).status_code == 200
    assert client.get("/api/workspace").status_code == 401

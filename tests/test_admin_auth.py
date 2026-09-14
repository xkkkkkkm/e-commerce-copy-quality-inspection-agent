"""Authentication behavior with an in-memory test double and opt-in real MySQL.

The test double implements only session persistence; no SQLite fallback is used.
RUN_MYSQL_TESTS=1 runs the same contracts against the project's configured MySQL.
"""

import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app import admin_auth
from db.auth_models import AdminSession
from db.session import SessionLocal, engine, get_db


class SessionDouble:
    def __init__(self):
        self.rows = {}

    def get(self, model, key):
        assert model is AdminSession
        return self.rows.get(key)

    def add(self, session):
        self.rows[session.token_hash] = session

    def delete(self, session):
        self.rows.pop(session.token_hash, None)

    def commit(self):
        pass


@pytest.fixture(params=["double", "mysql"])
def auth(request, monkeypatch):
    if request.param == "mysql" and os.getenv("RUN_MYSQL_TESTS") != "1":
        pytest.skip("requires project MySQL; set RUN_MYSQL_TESTS=1")
    username = "admin_test_" + uuid4().hex
    password = "test-password-中文"
    monkeypatch.setenv("ADMIN_USERNAME", username)
    monkeypatch.setenv("ADMIN_PASSWORD", password)
    monkeypatch.setenv("ADMIN_SESSION_HOURS", "8")
    monkeypatch.setenv("ADMIN_COOKIE_SECURE", "false")
    admin_auth.get_auth_settings.cache_clear()
    with admin_auth._failures_lock:
        admin_auth._failures.clear()
    if request.param == "mysql":
        AdminSession.__table__.create(engine, checkfirst=True)
        db = SessionLocal()
    else:
        db = SessionDouble()
    test_app = FastAPI()
    test_app.include_router(admin_auth.router)
    mutations = []

    @test_app.api_route("/api/admin/protected", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    def protected(username: str = Depends(admin_auth.require_admin)):
        mutations.append(username)
        return {"username": username}

    def override_db():
        yield db

    test_app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(test_app) as client:
            yield SimpleNamespace(
                client=client, app=test_app, db=db, username=username, password=password, mutations=mutations,
            )
    finally:
        if request.param == "mysql":
            db.rollback()
            # Never clear another administrator's sessions or existing product tables.
            db.execute(delete(AdminSession).where(AdminSession.username == username))
            db.commit()
            db.close()
        admin_auth.get_auth_settings.cache_clear()
        with admin_auth._failures_lock:
            admin_auth._failures.clear()


def login(auth, client=None, **kwargs):
    return (client or auth.client).post(
        "/api/admin/auth/login", json={"username": auth.username, "password": auth.password}, **kwargs,
    )


def test_login_cookie_roundtrip_and_hash_only_persistence(auth):
    assert auth.client.get("/api/admin/auth/me").status_code == 401
    response = login(auth, headers={"Origin": "http://testserver"})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie and "Max-Age=28800" in cookie
    assert "Secure" not in cookie
    token = auth.client.cookies.get(admin_auth.COOKIE_NAME)
    record = auth.db.get(AdminSession, admin_auth._token_hash(token))
    assert record.username == auth.username
    assert record.token_hash != token
    assert not hasattr(record, "token") and not hasattr(record, "password")
    identity = auth.client.get("/api/admin/auth/me")
    assert identity.status_code == 200
    assert identity.json() == response.json()
    assert identity.headers["cache-control"] == "no-store"
    assert identity.json()["csrf_token"] != token
    assert auth.client.get("/api/admin/protected").status_code == 200


def test_invalid_credentials_use_same_unauthorized_response(auth):
    wrong_password = auth.client.post(
        "/api/admin/auth/login", json={"username": auth.username, "password": "wrong"},
    )
    wrong_username = auth.client.post(
        "/api/admin/auth/login", json={"username": "不存在的账号", "password": auth.password},
    )
    assert wrong_password.status_code == wrong_username.status_code == 401
    assert wrong_password.json() == wrong_username.json()
    assert admin_auth.COOKIE_NAME not in auth.client.cookies


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_mutations_require_valid_csrf_before_running_handler(auth, method):
    csrf = login(auth).json()["csrf_token"]
    for headers in ({}, {"X-CSRF-Token": "wrong"}, {"X-CSRF-Token": "☃".encode().hex()}):
        assert auth.client.request(method, "/api/admin/protected", headers=headers).status_code == 403
    assert auth.mutations == []
    response = auth.client.request(method, "/api/admin/protected", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200
    assert auth.mutations == [auth.username]


def test_cross_origin_login_and_mutations_are_rejected(auth):
    for origin in ("https://evil.example", "null", "http://testserver.evil.example", "http://testserver:81"):
        assert login(auth, headers={"Origin": origin}).status_code == 403
    csrf = login(auth).json()["csrf_token"]
    assert auth.client.post(
        "/api/admin/protected", headers={"Origin": "https://evil.example", "X-CSRF-Token": csrf},
    ).status_code == 403
    assert auth.client.post(
        "/api/admin/protected", headers={"Origin": "http://testserver:80", "X-CSRF-Token": csrf},
    ).status_code == 200


def test_expired_or_tampered_session_is_unauthorized(auth):
    login(auth)
    token = auth.client.cookies.get(admin_auth.COOKIE_NAME)
    record = auth.db.get(AdminSession, admin_auth._token_hash(token))
    record.expires_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1)
    auth.db.commit()
    assert auth.client.get("/api/admin/auth/me").status_code == 401
    auth.client.cookies.clear()
    auth.client.cookies.set(admin_auth.COOKIE_NAME, "a" * 43)
    assert auth.client.get("/api/admin/auth/me").status_code == 401
    auth.client.cookies.set(admin_auth.COOKIE_NAME, "invalid-token")
    assert auth.client.get("/api/admin/auth/me").status_code == 401


def test_logout_revokes_only_current_session_and_requires_csrf(auth):
    with TestClient(auth.app) as other:
        login(auth, client=other)
        csrf = login(auth).json()["csrf_token"]
        token = auth.client.cookies.get(admin_auth.COOKIE_NAME)
        assert auth.client.post("/api/admin/auth/logout").status_code == 403
        assert auth.client.get("/api/admin/auth/me").status_code == 200
        assert auth.client.post("/api/admin/auth/logout", headers={"X-CSRF-Token": csrf}).status_code == 200
        assert auth.db.get(AdminSession, admin_auth._token_hash(token)) is None
        assert admin_auth.COOKIE_NAME not in auth.client.cookies
        assert other.get("/api/admin/auth/me").status_code == 200
        auth.client.cookies.set(admin_auth.COOKIE_NAME, token)
        assert auth.client.get("/api/admin/auth/me").status_code == 401


def test_new_login_rotates_browser_session_and_csrf(auth):
    first = login(auth).json()
    old_token = auth.client.cookies.get(admin_auth.COOKIE_NAME)
    second = login(auth).json()
    assert first["csrf_token"] != second["csrf_token"]
    assert auth.db.get(AdminSession, admin_auth._token_hash(old_token)) is None
    assert auth.client.post(
        "/api/admin/protected", headers={"X-CSRF-Token": first["csrf_token"]},
    ).status_code == 403
    assert auth.client.get("/api/admin/auth/me").json() == second


def test_login_failures_are_throttled(auth, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(admin_auth.time, "monotonic", lambda: now[0])
    for _ in range(admin_auth._FAILURE_LIMIT):
        assert auth.client.post(
            "/api/admin/auth/login", json={"username": auth.username, "password": "wrong"},
        ).status_code == 401
    blocked = login(auth)
    assert blocked.status_code == 429
    assert int(blocked.headers["retry-after"]) > 0
    now[0] += admin_auth._FAILURE_WINDOW + 1
    assert login(auth).status_code == 200


def test_secure_cookie_is_configurable(auth, monkeypatch):
    monkeypatch.setenv("ADMIN_COOKIE_SECURE", "true")
    admin_auth.get_auth_settings.cache_clear()
    with TestClient(auth.app, base_url="https://testserver") as secure_client:
        response = login(auth, client=secure_client)
        assert "Secure" in response.headers["set-cookie"]
        assert secure_client.get("/api/admin/auth/me").status_code == 200


def test_throttle_memory_is_bounded(monkeypatch):
    monkeypatch.setattr(admin_auth, "_MAX_TRACKED_IPS", 3)
    with admin_auth._failures_lock:
        admin_auth._failures.clear()
    try:
        for index in range(10):
            admin_auth._record_login_failure(f"test-ip-{index}")
        assert len(admin_auth._failures) == 3
    finally:
        with admin_auth._failures_lock:
            admin_auth._failures.clear()

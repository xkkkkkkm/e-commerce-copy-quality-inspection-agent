"""One administrator, environment credentials and revocable MySQL sessions."""

import hashlib
import hmac
import json
import os
import re
import secrets
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from threading import Lock
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, SecretStr
from sqlalchemy.orm import Session
from sqlalchemy import delete

from db.auth_models import AdminSession
from db.session import get_db


router = APIRouter(prefix="/api/admin/auth", tags=["admin-auth"])
COOKIE_NAME = "admin_session"
_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}\Z")
_PASSWORD_ROUNDS = 210_000
_FAILURE_WINDOW = 300
_FAILURE_LIMIT = 5
_MAX_TRACKED_IPS = 1024
_failures: OrderedDict[str, tuple[int, float]] = OrderedDict()
_failures_lock = Lock()
_auth_fingerprint: tuple[str, str] | None = None
_auth_valid_after: datetime | None = None


@dataclass(frozen=True)
class AuthSettings:
    username: str
    password_salt: bytes = field(repr=False)
    password_hash: bytes = field(repr=False)
    session_hours: int
    cookie_secure: bool


@lru_cache(maxsize=1)
def get_auth_settings() -> AuthSettings:
    global _auth_fingerprint, _auth_valid_after
    username = os.getenv("ADMIN_USERNAME", "admin")
    password = os.getenv("ADMIN_PASSWORD", "admin12345")
    session_hours = int(os.getenv("ADMIN_SESSION_HOURS", "8"))
    if not username or len(username) > 128 or not password or session_hours < 1:
        raise RuntimeError("Invalid administrator authentication configuration")
    if os.getenv("APP_ENV", "local").lower() in {"prod", "production"} and password in {
        "admin12345", "change_me", "password", "admin",
    }:
        raise RuntimeError("ADMIN_PASSWORD must be changed for production")
    fingerprint = (username, hashlib.sha256(password.encode()).hexdigest())
    if _auth_fingerprint is not None and _auth_fingerprint != fingerprint:
        # MySQL DATETIME commonly stores second precision while this process
        # records microseconds. Leave a one-second boundary cushion so a newly
        # authenticated session is not mistaken for a pre-rotation session.
        _auth_valid_after = (datetime.now(timezone.utc) - timedelta(seconds=1)).replace(tzinfo=None)
    _auth_fingerprint = fingerprint
    salt = secrets.token_bytes(32)
    return AuthSettings(
        username=username,
        password_salt=salt,
        password_hash=hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PASSWORD_ROUNDS),
        session_hours=session_hours,
        cookie_secure=os.getenv("ADMIN_COOKIE_SECURE", "false").lower() in {"true", "1", "yes"},
    )


class LoginBody(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: SecretStr = Field(min_length=1, max_length=4096)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def _csrf_token(token: str) -> str:
    # The database digest cannot be used to recover this or the bearer cookie.
    return hmac.new(token.encode("ascii"), b"admin-session-csrf-v1", hashlib.sha256).hexdigest()


def _origin_parts(value: str) -> tuple[str, str | None, int | None]:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Invalid origin")
    if parsed.username or parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("Invalid origin")
    return parsed.scheme.lower(), parsed.hostname.lower(), parsed.port or (443 if parsed.scheme == "https" else 80)


def _check_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if origin is None:
        return
    try:
        matches = _origin_parts(origin) == _origin_parts(str(request.base_url))
    except ValueError:
        matches = False
    if not matches:
        raise HTTPException(status_code=403, detail="请求来源不受信任")


def _check_login_limit(ip: str) -> None:
    now = time.monotonic()
    with _failures_lock:
        entry = _failures.get(ip)
        if entry is not None:
            count, started_at = entry
            if now - started_at >= _FAILURE_WINDOW:
                del _failures[ip]
            elif count >= _FAILURE_LIMIT:
                retry_after = max(1, int(_FAILURE_WINDOW - (now - started_at)))
                raise HTTPException(status_code=429, detail="登录尝试过多，请稍后重试", headers={"Retry-After": str(retry_after)})
    shared = _shared_login_window(ip)
    if shared and shared[0] >= _FAILURE_LIMIT:
        retry_after = max(1, int(_FAILURE_WINDOW - (time.time() - shared[1])))
        raise HTTPException(status_code=429, detail="登录尝试过多，请稍后重试", headers={"Retry-After": str(retry_after)})


def _shared_login_window(ip: str, *, increment: bool = False, clear: bool = False):
    """Best-effort fixed-window limit shared by workers in one container.

    A configured writable file uses an OS advisory lock. If unavailable, the
    bounded in-process limiter remains active. Multiple hosts should use an
    external gateway limit in addition to this local guard.
    """
    path = os.getenv("ADMIN_THROTTLE_FILE", "")
    if not path:
        return None
    try:
        import fcntl
        with open(path, "a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            handle.seek(0)
            try:
                entries = json.load(handle)
            except (ValueError, TypeError):
                entries = {}
            if not isinstance(entries, dict):
                entries = {}
            now = time.time()
            entries = {key: value for key, value in entries.items()
                       if isinstance(value, list) and len(value) == 2 and now - value[1] < _FAILURE_WINDOW}
            if clear:
                entries.pop(ip, None)
            elif increment:
                count, started_at = entries.get(ip, [0, now])
                entries[ip] = [count + 1, started_at]
            if len(entries) > _MAX_TRACKED_IPS:
                entries = dict(sorted(entries.items(), key=lambda item: item[1][1])[-_MAX_TRACKED_IPS:])
            handle.seek(0)
            json.dump(entries, handle, separators=(",", ":"))
            handle.truncate()
            return entries.get(ip)
    except (ImportError, OSError, ValueError, TypeError):
        return None


def _record_login_failure(ip: str) -> None:
    now = time.monotonic()
    with _failures_lock:
        count, started_at = _failures.get(ip, (0, now))
        if now - started_at >= _FAILURE_WINDOW:
            count, started_at = 0, now
        _failures[ip] = (count + 1, started_at)
        _failures.move_to_end(ip)
        while len(_failures) > _MAX_TRACKED_IPS:
            _failures.popitem(last=False)
    _shared_login_window(ip, increment=True)


def require_admin(request: Request, db: Session = Depends(get_db)) -> str:
    """Protect an admin endpoint; unsafe methods additionally require CSRF."""
    token = request.cookies.get(COOKIE_NAME, "")
    if not _TOKEN_PATTERN.fullmatch(token):
        raise HTTPException(status_code=401, detail="请先登录管理后台")
    settings = get_auth_settings()
    session = db.get(AdminSession, _token_hash(token))
    if session is None or session.expires_at <= datetime.now(timezone.utc).replace(tzinfo=None):
        raise HTTPException(status_code=401, detail="请先登录管理后台")
    if _auth_valid_after is not None and session.created_at and session.created_at < _auth_valid_after:
        raise HTTPException(status_code=401, detail="请先登录管理后台")
    if not hmac.compare_digest(session.username.encode(), settings.username.encode()):
        raise HTTPException(status_code=401, detail="请先登录管理后台")
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        _check_origin(request)
        csrf = request.headers.get("x-csrf-token", "")
        if not hmac.compare_digest(csrf.encode(), _csrf_token(token).encode()):
            raise HTTPException(status_code=403, detail="CSRF 校验失败，请刷新页面后重试")
    request.state.admin_session = session
    return session.username


@router.post("/login")
def login(body: LoginBody, request: Request, response: Response, db: Session = Depends(get_db)):
    _check_origin(request)
    ip = request.client.host if request.client else "unknown"
    _check_login_limit(ip)
    settings = get_auth_settings()
    candidate = hashlib.pbkdf2_hmac(
        "sha256", body.password.get_secret_value().encode(), settings.password_salt, _PASSWORD_ROUNDS,
    )
    username_matches = hmac.compare_digest(
        hashlib.sha256(body.username.encode()).digest(), hashlib.sha256(settings.username.encode()).digest(),
    )
    password_matches = hmac.compare_digest(candidate, settings.password_hash)
    if not (username_matches and password_matches):
        _record_login_failure(ip)
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=settings.session_hours)
    session = AdminSession(
        token_hash=_token_hash(token), username=settings.username, expires_at=expires_at.replace(tzinfo=None),
        created_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    # Re-authentication rotates this browser's existing session only.
    old_token = request.cookies.get(COOKIE_NAME, "")
    if _TOKEN_PATTERN.fullmatch(old_token):
        old_session = db.get(AdminSession, _token_hash(old_token))
        if old_session is not None:
            db.delete(old_session)
    db.add(session)
    db.commit()
    with _failures_lock:
        _failures.pop(ip, None)
    _shared_login_window(ip, clear=True)
    response.set_cookie(
        COOKIE_NAME, token, httponly=True, secure=settings.cookie_secure, samesite="strict",
        max_age=settings.session_hours * 3600, expires=expires_at, path="/",
    )
    response.headers["Cache-Control"] = "no-store"
    return {"username": session.username, "csrf_token": _csrf_token(token)}


@router.get("/me")
def me(request: Request, response: Response, username: str = Depends(require_admin)):
    response.headers["Cache-Control"] = "no-store"
    return {"username": username, "csrf_token": _csrf_token(request.cookies[COOKIE_NAME])}


@router.post("/logout")
def logout(
    request: Request, response: Response, username: str = Depends(require_admin), db: Session = Depends(get_db),
):
    db.delete(request.state.admin_session)
    db.commit()
    response.delete_cookie(
        COOKIE_NAME, path="/", httponly=True, secure=get_auth_settings().cookie_secure, samesite="strict",
    )
    response.headers["Cache-Control"] = "no-store"
    return {"status": "ok"}


@router.post("/logout-all")
@router.post("/revoke-all")
def logout_all(
    response: Response, username: str = Depends(require_admin), db: Session = Depends(get_db),
):
    """Revoke every session for the administrator (password rotation helper)."""
    db.execute(delete(AdminSession).where(AdminSession.username == username))
    db.commit()
    response.delete_cookie(COOKIE_NAME, path="/", httponly=True,
                           secure=get_auth_settings().cookie_secure, samesite="strict")
    response.headers["Cache-Control"] = "no-store"
    return {"status": "ok", "revoked": True}

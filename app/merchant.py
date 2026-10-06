"""Merchant self-service API: invited accounts can upload and inspect their own catalog."""
import csv
import hashlib
import io
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel, Field, SecretStr
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.admin_auth import require_admin
from app.catalog_schemas import ProductCreate
from db.auth_models import MerchantAccount, MerchantSession
from db.catalog_models import ManagedProduct
from db.session import get_db
from services import catalog

router = APIRouter(prefix="/api/merchant", tags=["merchant"])
COOKIE = "merchant_session"


def _digest(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    return salt.hex() + ":" + hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 210_000).hex()


def _valid(password: str, stored: str) -> bool:
    try:
        salt_hex, digest = stored.split(":", 1)
        candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), 210_000).hex()
        return secrets.compare_digest(candidate, digest)
    except (ValueError, TypeError):
        return False


class Invite(BaseModel):
    username: str = Field(min_length=3, max_length=128)
    password: SecretStr = Field(min_length=12, max_length=4096)
    merchant_name: str = Field(min_length=1, max_length=128)


class Login(BaseModel):
    username: str
    password: SecretStr


def merchant_session(request: Request, db: Session = Depends(get_db)) -> MerchantAccount:
    token = request.cookies.get(COOKIE)
    if not token:
        raise HTTPException(401, "请先登录商家端")
    row = db.scalar(select(MerchantSession).where(MerchantSession.token_hash == hashlib.sha256(token.encode()).hexdigest()))
    if not row or row.expires_at < datetime.now(timezone.utc).replace(tzinfo=None):
        raise HTTPException(401, "商家会话已失效")
    account = db.get(MerchantAccount, row.username)
    if not account or not account.enabled:
        raise HTTPException(403, "商家账号已停用")
    return account


@router.post("/invite", dependencies=[Depends(require_admin)])
def invite(data: Invite, db: Session = Depends(get_db)):
    if db.get(MerchantAccount, data.username):
        raise HTTPException(409, "商家账号已存在")
    db.add(MerchantAccount(username=data.username, password_hash=_digest(data.password.get_secret_value()), merchant_name=data.merchant_name.strip()))
    db.commit()
    return {"username": data.username, "merchant_name": data.merchant_name.strip()}


@router.post("/auth/login")
def login(data: Login, response: Response, db: Session = Depends(get_db)):
    account = db.get(MerchantAccount, data.username)
    if not account or not account.enabled or not _valid(data.password.get_secret_value(), account.password_hash):
        raise HTTPException(401, "用户名或密码错误")
    token = secrets.token_urlsafe(32)
    db.add(MerchantSession(token_hash=hashlib.sha256(token.encode()).hexdigest(), username=account.username,
                           merchant_name=account.merchant_name,
                           expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=8)))
    db.commit()
    response.set_cookie(COOKIE, token, httponly=True, samesite="lax", secure=False, max_age=28800)
    return {"username": account.username, "merchant_name": account.merchant_name}


@router.post("/auth/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    token = request.cookies.get(COOKIE)
    if token:
        db.execute(delete(MerchantSession).where(MerchantSession.token_hash == hashlib.sha256(token.encode()).hexdigest()))
        db.commit()
    response.delete_cookie(COOKIE)
    return {"ok": True}


def _payload(row: dict, merchant_name: str) -> ProductCreate:
    attrs = row.get("attributes", "{}")
    if isinstance(attrs, str):
        import json
        attrs = json.loads(attrs or "{}")
    return ProductCreate(product_id=row["product_id"], merchant_name=merchant_name,
                         category=row["category"], title=row["title"], description=row.get("description", ""), attributes=attrs)


@router.post("/products/import")
async def import_products(file: UploadFile = File(...), account: MerchantAccount = Depends(merchant_session), db: Session = Depends(get_db)):
    raw = await file.read()
    if len(raw) > 10 * 1024 * 1024:
        raise HTTPException(413, "导入文件不能超过 10 MB")
    try:
        if (file.filename or "").lower().endswith(".csv"):
            rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
        else:
            import json
            value = json.loads(raw)
            rows = value if isinstance(value, list) else value.get("products", [])
        if not rows or len(rows) > 1000:
            raise ValueError("文件必须包含 1-1000 条商品")
    except Exception as exc:
        raise HTTPException(422, f"文件格式无效: {exc}") from exc
    results = []
    for index, row in enumerate(rows, 1):
        try:
            product = catalog.create_product(db, _payload(row, account.merchant_name), account.username)
            results.append({"row": index, "ok": True, "product_id": product["product_id"]})
        except Exception as exc:
            db.rollback()
            results.append({"row": index, "ok": False, "error": str(exc)})
    return {"total": len(rows), "created": sum(x["ok"] for x in results), "failed": sum(not x["ok"] for x in results), "items": results}


@router.get("/products")
def my_products(account: MerchantAccount = Depends(merchant_session), db: Session = Depends(get_db)):
    rows = db.scalars(select(ManagedProduct).where(ManagedProduct.merchant_name == account.merchant_name).order_by(ManagedProduct.id.desc())).all()
    return [{"id": x.id, "product_id": x.product_id, "title": x.title, "category": x.category, "status": x.status, "version": x.version} for x in rows]

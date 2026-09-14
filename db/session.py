from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from functools import lru_cache
import os

from app.config import DATABASE_URL


class Base(DeclarativeBase):
    pass


engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_recycle=3600,
    connect_args={"connect_timeout": 10},
)
@lru_cache(maxsize=64)
def _tenant_engine(url):
    return create_engine(url, pool_pre_ping=True, pool_recycle=3600,
                         pool_size=int(os.getenv("DB_POOL_SIZE", "5")),
                         max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "5")),
                         pool_timeout=10, connect_args={"connect_timeout": 10})


def current_engine():
    from services.tenancy import tenant_id, configurations
    name = tenant_id.get()
    if name == "default":
        return engine
    config = configurations().get(name)
    if config is None:
        raise RuntimeError("Unknown tenant; database access denied")
    url = config["database_url"]
    host = os.getenv("TENANT_DB_HOST")
    if host:
        from sqlalchemy.engine import make_url
        url = make_url(url).set(host=host, port=3306).render_as_string(hide_password=False)
    return _tenant_engine(url)


class TenantSession(Session):
    def __init__(self, **kwargs):
        # Binding is captured once, so a Session can never change tenant later.
        kwargs["bind"] = kwargs.get("bind") or current_engine()
        super().__init__(**kwargs)


SessionLocal = sessionmaker(class_=TenantSession, autoflush=False, expire_on_commit=False)


def get_db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()

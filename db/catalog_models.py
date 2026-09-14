"""Catalog administration tables, separate from the original inspection schema."""
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from db.session import Base


class ManagedProduct(Base):
    __tablename__ = "managed_products"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    product_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    merchant_name: Mapped[str] = mapped_column(String(128), index=True)
    category: Mapped[str] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(String(500))
    description: Mapped[str] = mapped_column(Text)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    # The newest started inspection wins, even when an earlier request finishes later.
    latest_inspection_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    latest_task_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    latest_risk: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    inspected_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class ProductRevision(Base):
    __tablename__ = "product_revisions"
    __table_args__ = (UniqueConstraint("managed_product_id", "version", name="uq_product_revision"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    managed_product_id: Mapped[int] = mapped_column(ForeignKey("managed_products.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    product_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    actor: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ProductInspection(Base):
    __tablename__ = "product_inspections"
    __table_args__ = (Index("ix_product_inspection_version", "managed_product_id", "version"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    managed_product_id: Mapped[int] = mapped_column(ForeignKey("managed_products.id"))
    version: Mapped[int] = mapped_column(Integer)
    task_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    mode: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="running")
    risk_level: Mapped[str | None] = mapped_column(String(16), nullable=True)
    issue_count: Mapped[int] = mapped_column(Integer, default=0)
    degraded: Mapped[bool] = mapped_column(Boolean, default=False)
    report_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    actor: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ProductAudit(Base):
    __tablename__ = "product_audits"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    managed_product_id: Mapped[int] = mapped_column(ForeignKey("managed_products.id"), index=True)
    action: Mapped[str] = mapped_column(String(32))
    from_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    to_status: Mapped[str] = mapped_column(String(16))
    version: Mapped[int] = mapped_column(Integer)
    actor: Mapped[str] = mapped_column(String(128))
    reason: Mapped[str] = mapped_column(Text, default="")
    inspection_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


CATALOG_TABLES = [ManagedProduct.__table__, ProductRevision.__table__, ProductInspection.__table__, ProductAudit.__table__]

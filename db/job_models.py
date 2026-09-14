"""Durable administrator inspection queue records."""
from datetime import datetime
from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from db.session import Base


class InspectionJob(Base):
    __tablename__ = "inspection_jobs"
    __table_args__ = (UniqueConstraint("idempotency_key", name="uq_inspection_job_idempotency"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    mode: Mapped[str] = mapped_column(String(16), default="rules")
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    actor: Mapped[str] = mapped_column(String(128))
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(default=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class InspectionJobItem(Base):
    __tablename__ = "inspection_job_items"
    __table_args__ = (UniqueConstraint("job_id", "managed_product_id", name="uq_job_product"),
                      Index("ix_job_item_status", "job_id", "status"))
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("inspection_jobs.id"), index=True)
    managed_product_id: Mapped[int] = mapped_column(ForeignKey("managed_products.id"), index=True)
    expected_version: Mapped[int] = mapped_column(Integer)
    mode: Mapped[str] = mapped_column(String(16), default="rules")
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    task_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    inspection_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


JOB_TABLES = [InspectionJob.__table__, InspectionJobItem.__table__]

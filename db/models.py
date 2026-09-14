from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Integer, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from db.session import Base


class ProductSample(Base):
    __tablename__ = "product_samples"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    product_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    category: Mapped[str] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(String(500))
    description: Mapped[str] = mapped_column(Text)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSON)
    source: Mapped[str] = mapped_column(String(32), default="manual")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class QualityRule(Base):
    __tablename__ = "quality_rules"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    rule_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    category: Mapped[str] = mapped_column(String(32), index=True)
    issue_type: Mapped[str] = mapped_column(String(128))
    risk_level: Mapped[str] = mapped_column(String(16))
    rule_text: Mapped[str] = mapped_column(Text)
    bad_examples: Mapped[list[str]] = mapped_column(JSON)
    rewrite_hint: Mapped[str] = mapped_column(Text)
    version: Mapped[str] = mapped_column(String(32), default="1.0")
    status: Mapped[str] = mapped_column(String(16), default="enabled")


class InspectionTask(Base):
    __tablename__ = "inspection_tasks"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    product_id: Mapped[str] = mapped_column(String(128), index=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    trigger_source: Mapped[str] = mapped_column(String(32), default="api")
    input_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    input_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class InspectionResult(Base):
    __tablename__ = "inspection_results"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    risk_level: Mapped[str] = mapped_column(String(16))
    score: Mapped[int] = mapped_column(Integer)
    issues: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    optimized_title: Mapped[str] = mapped_column(String(500))
    optimized_description: Mapped[str] = mapped_column(Text)
    report_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class AgentTrace(Base):
    __tablename__ = "agent_traces"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(64), index=True)
    step_name: Mapped[str] = mapped_column(String(128))
    skill_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tool_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    input_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class EvaluationCase(Base):
    __tablename__ = "evaluation_cases"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    case_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    product_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    expected_issues: Mapped[list[str]] = mapped_column(JSON)
    predicted_issues: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    metrics: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

"""Admin authentication state, separate from the six product tables."""

from datetime import datetime, timezone

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from db.session import Base


class AdminSession(Base):
    __tablename__ = "admin_sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    username: Mapped[str] = mapped_column(String(128))
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
    )

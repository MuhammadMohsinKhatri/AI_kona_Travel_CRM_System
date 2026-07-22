from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AppSetting(Base):
    """Runtime configuration a user can change from the Settings page.

    Deliberately key/value rather than a column per setting: these are edited
    through the UI, not deployed, so adding one shouldn't need a schema change
    or a redeploy.

    This is where secrets that a user owns belong — a Telegram bot token is
    theirs to rotate, and putting it in .env means an ops task every time it
    changes. Credentials the *system* owns (KonaOS, Square, OpenAI) stay in
    the environment.
    """

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


# Setting keys
TELEGRAM_KEY = "telegram"

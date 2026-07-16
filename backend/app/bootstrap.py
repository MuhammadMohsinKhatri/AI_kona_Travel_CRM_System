"""One-time startup: create/sync tables and seed the first admin user."""
from __future__ import annotations

from sqlalchemy import inspect, text

from app.config import settings
from app.db.base import Base, SessionLocal, engine
from app.models import User
from app.security import hash_password


def init_db() -> None:
    # create_all only creates MISSING TABLES — it never alters existing ones.
    # _sync_columns then adds any model columns missing from live tables, so a
    # model change deploys cleanly without hand-run ALTERs (no Alembic set up).
    Base.metadata.create_all(bind=engine)
    _sync_columns()
    seed_admin()


def _sync_columns() -> None:
    """Add model columns that are missing from existing tables (additive only).

    This is what bit us in production: financial_entries was created from an
    early model, later commits widened the model, and every SELECT 500'd with
    UndefinedColumn. Never drops or retypes anything.
    """
    inspector = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not inspector.has_table(table.name):
                continue  # create_all just made it — already current
            live_cols = inspector.get_columns(table.name)
            existing = {c["name"] for c in live_cols}
            model_cols = {c.name for c in table.columns}

            # Orphaned columns (renamed/removed from the model) that are still
            # NOT NULL block every INSERT — the model no longer supplies them
            # (e.g. financial_entries.tax_rate → total_tax_rate). Relax them to
            # nullable; we never drop data.
            if engine.dialect.name != "sqlite":  # sqlite can't ALTER constraints
                pk_cols = set(
                    (inspector.get_pk_constraint(table.name) or {}).get(
                        "constrained_columns"
                    ) or []
                )
                for live in live_cols:
                    if live["name"] in pk_cols:
                        continue
                    if live["name"] not in model_cols and not live.get("nullable", True):
                        conn.execute(text(
                            f'ALTER TABLE {table.name} '
                            f'ALTER COLUMN {live["name"]} DROP NOT NULL'
                        ))
                        print(f"[INFO] schema sync: relaxed orphan NOT NULL "
                              f"{table.name}.{live['name']}")

            for col in table.columns:
                if col.name in existing:
                    continue
                ddl = f'ALTER TABLE {table.name} ADD COLUMN {col.name} ' \
                      f'{col.type.compile(engine.dialect)}'
                # Backfill simple scalar defaults so NOT NULL-ish reads behave.
                default = getattr(col.default, "arg", None)
                if isinstance(default, bool):
                    ddl += f" DEFAULT {'TRUE' if default else 'FALSE'}" \
                        if engine.dialect.name != "sqlite" else f" DEFAULT {int(default)}"
                elif isinstance(default, (int, float)):
                    ddl += f" DEFAULT {default}"
                elif isinstance(default, str):
                    escaped = default.replace("'", "''")
                    ddl += f" DEFAULT '{escaped}'"
                conn.execute(text(ddl))
                print(f"[INFO] schema sync: added {table.name}.{col.name}")


def seed_admin() -> None:
    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.email == settings.first_admin_email).one_or_none()
        if existing is None:
            db.add(User(
                email=settings.first_admin_email,
                hashed_password=hash_password(settings.first_admin_password),
                full_name="Administrator",
                is_admin=True,
                is_active=True,
            ))
            db.commit()
    finally:
        db.close()

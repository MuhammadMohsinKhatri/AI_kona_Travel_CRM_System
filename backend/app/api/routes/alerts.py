from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.base import get_db
from app.models import Alert, User
from app.schemas.common import Page
from app.schemas.event import AlertOut

router = APIRouter(prefix="/api/alerts", tags=["alerts"])


@router.get("", response_model=Page[AlertOut])
def list_alerts(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    severity: Optional[str] = None,
    resolved: Optional[bool] = None,
) -> Page[AlertOut]:
    query = db.query(Alert)
    if severity:
        query = query.filter(Alert.severity == severity)
    if resolved is not None:
        query = query.filter(Alert.resolved == resolved)
    total = query.with_entities(func.count(Alert.id)).scalar() or 0
    items = (
        query.order_by(Alert.resolved.asc(), Alert.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.post("/{alert_id}/resolve", response_model=AlertOut)
def resolve_alert(
    alert_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)
) -> Alert:
    alert = db.get(Alert, alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail="Alert not found")
    alert.resolved = True
    alert.resolved_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(alert)
    return alert


# response_model=None is load-bearing: with `from __future__ import annotations`,
# FastAPI 0.115's return-type inference turns `-> None` into a response body and
# asserts at import ("Status code 204 must not have a response body").
@router.delete("/{alert_id}", status_code=204, response_model=None)
def delete_alert(
    alert_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)
) -> None:
    """Delete an alert outright (use /resolve to keep it as history)."""
    alert = db.get(Alert, alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail="Alert not found")
    db.delete(alert)
    db.commit()

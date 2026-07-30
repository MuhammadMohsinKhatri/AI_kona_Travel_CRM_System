"""Recording payments that arrive off-system: a check in the post, cash counted
in a truck.

Two Telegram bots used to do this. Both had the same shape and the same flaw —
a model read the input and the write happened immediately, so a misread amount
was a payment recorded against the wrong customer, discovered whenever someone
next looked. Here the reading and the writing are separate requests with a
person in between: every endpoint below either REVIEWS (reads, matches,
computes, writes nothing) or APPLIES (writes exactly one plan a person has seen
on screen).

Apply never trusts a figure the browser sends. It is given an invoice id and an
amount — which invoice, and how much — and recomputes the fee removal, the
variance and the paid/part-paid decision server-side from the billing engine.
The screen cannot talk this into marking something paid that isn't.
"""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.config import settings
from app.core import intake_service as svc
from app.core.intake_readers import (CashEntry, CheckRead, parse_cash_speech,
                                     read_check, transcribe)
from app.db.base import get_db
from app.integrations.factory import get_crm
from app.models import User

router = APIRouter(prefix="/api/intake", tags=["intake"])

# A phone photo of a check is a couple of megabytes; a minute of dictation less.
# The cap is here so a mis-picked video doesn't get base64'd into an API call.
MAX_UPLOAD_BYTES = 12 * 1024 * 1024


def _result_json(result: svc.ApplyResult) -> dict[str, Any]:
    return {
        "ok": result.ok, "kind": "check", "summary": result.summary,
        "invoice_id": result.invoice_id, "crm_event_id": result.crm_event_id,
        "dry_run": result.dry_run, "warnings": result.warnings,
        "detail": result.detail,
    }


async def _read_upload(file: UploadFile, what: str) -> bytes:
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"That {what} is {len(data) / 1_000_000:.1f} MB — the limit is "
                   f"{MAX_UPLOAD_BYTES // 1_000_000} MB.",
        )
    return data


# ── checks ───────────────────────────────────────────────────────────────────


@router.post("/check")
async def review_check_upload(
    file: UploadFile = File(..., description="Photo of the check"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Read a photographed check, find its invoice, and settle it.

    The whole job from one upload: the fee comes off, the payment is recorded,
    the invoice reads paid. Nothing is typed and nothing is confirmed — see
    ``svc.auto_applicable_check`` for what makes that safe, which is not the
    model's confidence in itself but the check's amount agreeing exactly with an
    open invoice that no other invoice matches.

    When that agreement isn't there the upload becomes a review instead: what was
    read, what it nearly matched, and why it stopped. A failed read is a review
    too, never a 500 — the person is holding a check they cannot re-photograph
    any differently, so the answer has to be something they can act on.
    """
    image = await _read_upload(file, "photo")
    check = read_check(image, file.content_type or "image/jpeg")
    crm = get_crm()
    review = svc.review_check(db, crm, check)

    ok, held = svc.auto_applicable_check(review)
    if not ok or review.plan is None:
        return svc.check_review_json(review, held_because=held)

    result = svc.apply_check(db, crm, review.plan, by=user.email or "dashboard",
                             dry_run=settings.pipeline_dry_run)
    return svc.check_review_json(review, applied=_result_json(result))


class CheckDetails(BaseModel):
    """The check as the reviewer has it on screen — corrected, or typed from
    scratch when the photo was unreadable."""

    payer_name: str = Field(default="", max_length=255)
    amount: float = Field(default=0.0, ge=0)
    check_date: str = Field(default="", max_length=32)
    check_number: str = Field(default="", max_length=64)
    memo: str = Field(default="", max_length=512)
    invoice_id: str = Field(
        default="",
        description="Set when the reviewer picked an invoice off the screen. "
                    "Overrides the matcher.",
    )


@router.post("/check/rematch")
def rematch_check(
    body: CheckDetails,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Re-run the match after an edit, or against an invoice picked by hand.

    Same code path as the upload, so what the reviewer approves is always a plan
    this system produced — not one assembled in the browser.
    """
    check = CheckRead(
        payer_name=body.payer_name.strip(),
        amount=body.amount,
        check_date=body.check_date,
        check_number=body.check_number,
        memo=body.memo,
        confidence="manual",
    )
    review = svc.review_check(db, get_crm(), check, invoice_id=body.invoice_id.strip())
    return svc.check_review_json(review)


# ── cash ─────────────────────────────────────────────────────────────────────


class CashSpeech(BaseModel):
    transcript: str = Field(..., min_length=1, max_length=4000)
    default_date: str = Field(
        default="",
        max_length=32,
        description="YYYY-MM-DD to search when the speaker named no date.",
    )


def _cash_payload(
    db: Session, transcript: str, entries: list[CashEntry], default_date: str,
    by: str, notes: str = "", error: str = "",
) -> dict[str, Any]:
    """Match every line the recording contained, and post the ones that are sure.

    Bulk is the whole point — an admin reads off a day's takings in one breath
    and expects the day to be done. Lines that matched one event unambiguously
    post themselves; a line that matched nothing, matched two things equally, or
    landed on an event with no ledger row stays on screen for a person. The two
    outcomes are reported per line rather than as one number, so "six of seven
    went in" is visible instead of implied.
    """
    reviews = svc.review_cash(db, entries, default_date=default_date)
    items: list[dict[str, Any]] = []
    for review in reviews:
        ok, held = svc.auto_applicable_cash(review)
        if not ok or review.event is None:
            items.append(svc.cash_review_json(review, held_because=held))
            continue
        applied = _apply_cash(
            db,
            ApplyItem(kind="cash", amount=review.entry.amount,
                      crm_event_id=review.event.crm_event_id),
            by,
        )
        items.append(svc.cash_review_json(review, applied=applied))
    return {
        "transcript": transcript,
        "notes": notes,
        "error": error,
        "items": items,
    }


@router.post("/cash/voice")
async def review_cash_voice(
    file: UploadFile = File(..., description="Recording of the takings"),
    default_date: str = Form(default=""),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Transcribe one recording of a day's takings and post every event in it.

    "Pikesville took seven bucks, Camp Lollipop was twelve fifty" is two events,
    both matched and both recorded, from one press of the button. Only the lines
    the matcher won't call come back for a person.
    """
    audio = await _read_upload(file, "recording")
    transcript, error = transcribe(audio, file.filename or "speech.webm")
    if error:
        return {"transcript": transcript, "notes": "", "error": error, "items": []}

    speech = parse_cash_speech(transcript)
    return _cash_payload(
        db, transcript or speech.transcript, speech.entries, default_date,
        user.email or "dashboard", notes=speech.notes, error=speech.error,
    )


@router.post("/cash/text")
def review_cash_text(
    body: CashSpeech,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """The same, from typed text — for when the room is loud or the mic isn't
    allowed. The split-into-entries step is identical."""
    speech = parse_cash_speech(body.transcript)
    return _cash_payload(
        db, body.transcript, speech.entries, body.default_date,
        user.email or "dashboard", notes=speech.notes, error=speech.error,
    )


class CashLine(BaseModel):
    """One line as it stands on the review screen, after any correction."""

    query: str = Field(default="", max_length=512)
    amount: float = Field(default=0.0, ge=0)
    brand: str = Field(default="", max_length=64)
    date: str = Field(default="", max_length=32)
    crm_event_id: str = Field(
        default="",
        description="Set when the reviewer picked an event off the screen. "
                    "Overrides the matcher.",
    )


@router.post("/cash/rematch")
def rematch_cash(
    body: CashLine,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Re-match one line after an edit, or against an event picked by hand."""
    entry = CashEntry(query=body.query, amount=body.amount, brand=body.brand,
                      date=body.date)
    if body.crm_event_id.strip():
        review = svc.review_cash_for_event(db, body.crm_event_id.strip(), entry)
    else:
        reviews = svc.review_cash(db, [entry])
        review = reviews[0]
    return svc.cash_review_json(review)


# ── apply ────────────────────────────────────────────────────────────────────


class ApplyItem(BaseModel):
    """One approved line. Deliberately thin.

    Everything derived — the fee-free total, the variance, whether this settles
    the invoice or leaves a balance — is recomputed server-side when Apply runs.
    The browser says which invoice and how much, and nothing else.
    """

    kind: Literal["check", "cash"]
    amount: float = Field(..., ge=0)
    invoice_id: str = Field(default="", description="Checks: the invoice being paid")
    payer_name: str = Field(default="", max_length=255)
    crm_event_id: str = Field(default="", description="Cash: the event that took it")


class ApplyRequest(BaseModel):
    """The whole reviewed batch, approved in one click.

    One click for the batch, not one per line — but still a click. An LLM's
    reading of a photograph does not write payments on its own.
    """

    items: list[ApplyItem] = Field(..., min_length=1, max_length=50)


def _apply_check(db: Session, crm, item: ApplyItem, by: str) -> dict[str, Any]:
    if not item.invoice_id:
        return {"ok": False, "kind": "check",
                "summary": "No invoice was chosen for this check."}

    review = svc.review_check(
        db, crm,
        CheckRead(payer_name=item.payer_name, amount=item.amount,
                  confidence="approved"),
        invoice_id=item.invoice_id,
    )
    if review.plan is None:
        return {"ok": False, "kind": "check", "invoice_id": item.invoice_id,
                "summary": review.match.reason}

    result = svc.apply_check(db, crm, review.plan, by=by,
                             dry_run=settings.pipeline_dry_run)
    return {
        "ok": result.ok, "kind": "check", "summary": result.summary,
        "invoice_id": result.invoice_id, "crm_event_id": result.crm_event_id,
        "dry_run": result.dry_run, "warnings": result.warnings,
        "detail": result.detail,
    }


def _apply_cash(db: Session, item: ApplyItem, by: str) -> dict[str, Any]:
    """Post cash through the existing ledger endpoint rather than a second copy.

    ``/api/financials/by-event/{id}/cash`` already owns the override, the
    recompute of everything cash feeds, the audit line and the re-settling of a
    min-guarantee event whose invoice was waiting on this figure. Calling it
    here keeps one implementation of all of that — the alternative is two, and
    they drift.
    """
    from app.api.routes.financials import CashUpdate, set_cash_by_event

    if not item.crm_event_id:
        return {"ok": False, "kind": "cash",
                "summary": "No event was chosen for this amount."}
    try:
        response = set_cash_by_event(
            item.crm_event_id,
            CashUpdate(cash_collected=item.amount, source="manual", by=by),
            db=db,
            _="intake",
        )
    except HTTPException as e:
        return {"ok": False, "kind": "cash", "crm_event_id": item.crm_event_id,
                "summary": str(e.detail)}

    summary = f"Cash of ${item.amount:,.2f} recorded for event {item.crm_event_id}"
    if response.get("invoice_needed"):
        summary += (f" — minimum guarantee short by "
                    f"${response.get('shortfall', 0):,.2f}, invoice settling")
    return {"ok": True, "kind": "cash", "crm_event_id": item.crm_event_id,
            "summary": summary, "detail": response}


@router.post("/apply")
def apply_batch(
    body: ApplyRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Apply every approved line, and report what happened to each.

    Lines are independent: one failing — an invoice paid by hand five minutes
    ago, an event with no ledger row — does not stop the rest. The response
    carries a result per line so the screen can show exactly which went through.
    """
    crm = get_crm()
    by = user.email or "dashboard"
    results: list[dict[str, Any]] = []
    for item in body.items:
        try:
            if item.kind == "check":
                results.append(_apply_check(db, crm, item, by))
            else:
                results.append(_apply_cash(db, item, by))
        except Exception as e:  # noqa: BLE001 — one bad line must not lose the rest
            db.rollback()
            results.append({
                "ok": False, "kind": item.kind,
                "invoice_id": item.invoice_id, "crm_event_id": item.crm_event_id,
                "summary": f"Couldn't apply this one: {e}",
            })

    applied = sum(1 for r in results if r.get("ok"))
    return {
        "applied": applied,
        "failed": len(results) - applied,
        "dry_run": settings.pipeline_dry_run,
        "results": results,
    }

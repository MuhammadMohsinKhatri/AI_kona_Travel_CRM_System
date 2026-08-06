"""Images the chat displays, proxied so the Google Maps key stays server-side.

An ``<img>`` cannot send an Authorization header, so a media URL cannot be
protected the way the rest of the API is. Two bad answers to that, and the one
taken here:

  * Hand the browser a ``maps.googleapis.com`` URL — the key travels in the
    query string of a page the whole office has open. Scraped keys get billed
    to the owner.
  * Leave this endpoint open — anyone who finds it can spend the Maps quota by
    looping over addresses.

So the URL is SIGNED. The server names the subject (an address it already
resolved) and stamps it with an HMAC over that value plus an expiry, using the
app's existing secret. The browser can fetch what Aimee offered it and nothing
else, and only for as long as the conversation is fresh. No new credential, no
storage, and a link that leaks is a link to one Street View photo that stops
working within the hour.
"""
from __future__ import annotations

import hashlib
import hmac
import time
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from app.config import settings
from app.integrations import gmaps

router = APIRouter(prefix="/api/aimee/media", tags=["aimee"])

# Long enough to read a reply and scroll back to it; short enough that a link
# pasted elsewhere is dead by the time it travels.
SIGNATURE_TTL_SECONDS = 3600
_KINDS = {"street_view"}


def _sign(kind: str, subject: str, expires: int) -> str:
    payload = f"{kind}\n{subject}\n{expires}".encode("utf-8")
    return hmac.new(
        settings.secret_key.encode("utf-8"), payload, hashlib.sha256
    ).hexdigest()[:32]


def sign_media(kind: str, subject: str) -> str:
    """The path a tool hands back for the browser to load."""
    expires = int(time.time()) + SIGNATURE_TTL_SECONDS
    sig = _sign(kind, subject, expires)
    return (f"/api/aimee/media/{kind}?subject={quote(subject)}"
            f"&expires={expires}&sig={sig}")


@router.get("/{kind}")
def media(
    kind: str,
    subject: str = Query(...),
    expires: int = Query(...),
    sig: str = Query(...),
) -> Response:
    if kind not in _KINDS:
        raise HTTPException(status_code=404, detail="Unknown media kind.")
    # compare_digest, not ==: string comparison short-circuits on the first
    # wrong character, which leaks the signature a byte at a time to anyone
    # patient enough to time the responses.
    if not hmac.compare_digest(sig, _sign(kind, subject, expires)):
        raise HTTPException(status_code=403, detail="Bad media signature.")
    if expires < int(time.time()):
        raise HTTPException(status_code=410, detail="This image link has expired.")

    try:
        content = gmaps.street_view(subject)
    except gmaps.MapsError as e:
        raise HTTPException(status_code=502, detail=str(e))

    return Response(
        content=content,
        media_type="image/jpeg",
        # Cacheable for the life of the signature and no longer — the URL stops
        # working at the same moment, so a cached copy cannot outlive it.
        headers={"Cache-Control": f"private, max-age={SIGNATURE_TTL_SECONDS}"},
    )

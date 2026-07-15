"""KonaOS CRM proxy endpoints — merged from the Konaos_crms_apis project.

Mounted at /api/konaos by app.main. Endpoint behaviour is preserved from the
original src/main.py; auth accepts the original X-API-Key (GPT_API_KEY) or a
dashboard JWT. The KonaosClient singleton is created via init_konaos() from
the main app's lifespan.
"""
import os
import re
from contextlib import asynccontextmanager
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Header, HTTPException, Query, Depends, Body
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from dotenv import load_dotenv
import httpx
from bs4 import BeautifulSoup

from app.konaos.client import KonaosClient
from app.konaos.models import (
    ClientDetails,
    ClientIndustryType,
    ClientInfo,
    ClientRankingRequest,
    ClientRankingResponse,
    ClientResponse,
    CreateEventRequest,
    CreateStaffRequest,
    DeleteEventRequest,
    EventDetails,
    EventOperationResponse,
    EventsResponse,
    InvoiceMarkPaidRequest,
    SalesDataRequest,
    SalesDataResponse,
    StaffAvailabilityResponse,
    StaffResponse,
    StaffScheduleUsersListResponse,
    UpdateEventRequest,
    ZipcodeWebsiteResponse,
)

load_dotenv()

GPT_API_KEY = os.getenv("GPT_API_KEY")
SCRAPERAPI_KEY = os.getenv("SCRAPERAPI_KEY")

REPORT_BRAND_IDS = {
    "konaice": "66704154faed4c5991533eb5253815d9",
    "travelintom": "4553cb46d02d40e4ab2732673e141ac3",
}


def _resolve_report_brand_ids(brand: str) -> list[str]:
    """Map report brand selector to KonaOS brandIds list."""
    normalized = (brand or "both").strip().lower()
    if normalized == "konaice":
        return [REPORT_BRAND_IDS["konaice"]]
    if normalized == "travelintom":
        return [REPORT_BRAND_IDS["travelintom"]]
    return [REPORT_BRAND_IDS["konaice"], REPORT_BRAND_IDS["travelintom"]]


def _get_us_proxies() -> dict | None:
    """
    Build an httpx proxies dict from US_* proxy environment variables.

    Expected env vars:
      US_HTTP_PROXY=http://user:pass@host:port
      US_HTTPS_PROXY=http://user:pass@host:port
    """
    http_proxy = os.getenv("US_HTTP_PROXY")
    https_proxy = os.getenv("US_HTTPS_PROXY") or http_proxy

    if not http_proxy and not https_proxy:
        return None

    proxies: dict = {}
    if http_proxy:
        proxies["http://"] = http_proxy
    if https_proxy:
        proxies["https://"] = https_proxy
    return proxies


def _extract_content_from_html(html: str) -> tuple[str, List[str], List[str]]:
    """
    Extract text content, emails, and phone numbers from HTML.
    
    Returns:
        tuple: (text_content, emails_list, phone_numbers_list)
    """
    soup = BeautifulSoup(html, "html.parser")
    
    # Verify we got valid HTML
    if not soup.find("body") and not soup.find("html"):
        return ("", [], [])
    
    # Remove script, style, noscript elements
    for elem in soup.find_all(["script", "style", "noscript"]):
        elem.decompose()
    
    text_parts = []
    
    # Strategy 1: Look for franchise page containers (.header-container, .body-container)
    header_container = soup.select_one(".header-container")
    body_container = soup.select_one(".body-container")
    
    # If not found directly, try within .entry-content
    if not header_container or not body_container:
        entry_content = soup.select_one(".entry-content")
        if entry_content:
            if not header_container:
                header_container = entry_content.select_one(".header-container")
            if not body_container:
                body_container = entry_content.select_one(".body-container")
    
    # If still not found, try within #main-content > article
    if not header_container or not body_container:
        main_content = soup.select_one("#main-content")
        if main_content:
            article = main_content.select_one("article")
            if article:
                if not header_container:
                    header_container = article.select_one(".header-container")
                if not body_container:
                    body_container = article.select_one(".body-container")
    
    # Extract text from containers if found
    if header_container:
        header_text = header_container.get_text(separator="\n", strip=True)
        if header_text and len(header_text) > 10:
            text_parts.append(header_text)
    
    if body_container:
        body_text = body_container.get_text(separator="\n", strip=True)
        if body_text and len(body_text) > 10:
            text_parts.append(body_text)
    
    # Strategy 2: If containers empty or not found, try .entry-content directly
    if not text_parts or (text_parts and all(len(tp.strip()) < 10 for tp in text_parts)):
        entry_content = soup.select_one(".entry-content")
        if entry_content:
            for form in entry_content.find_all(["form", "iframe"]):
                form.decompose()
            entry_text = entry_content.get_text(separator="\n", strip=True)
            if entry_text and len(entry_text) > 50:
                text_parts = [entry_text]
    
    # Strategy 3: Fallback to other main content selectors
    if not text_parts or (text_parts and all(len(tp.strip()) < 10 for tp in text_parts)):
        content_selectors = ["#main-content", "article.post", "article", "#et-main-area", "main"]
        for selector in content_selectors:
            main_content = soup.select_one(selector)
            if main_content:
                for elem in main_content.find_all(["nav", "footer", "form", "iframe"]):
                    elem.decompose()
                content_text = main_content.get_text(separator="\n", strip=True)
                if content_text and len(content_text) > 50:
                    text_parts = [content_text]
                    break
    
    # Strategy 4: Last resort - body without nav/header/footer
    if not text_parts or (text_parts and all(len(tp.strip()) < 10 for tp in text_parts)):
        body = soup.find("body")
        if body:
            for elem in body.find_all(["nav", "header", "footer", "form", "iframe"]):
                elem.decompose()
            body_text = body.get_text(separator="\n", strip=True)
            if body_text and len(body_text) > 50:
                text_parts = [body_text]
    
    # Combine all text parts
    if text_parts:
        text = "\n\n".join(tp for tp in text_parts if tp.strip())
    else:
        text = soup.get_text(separator="\n", strip=True)
    
    # Clean up excessive whitespace
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = text.strip()
    
    # Extract email addresses
    emails = []
    for link in soup.find_all("a", href=True):
        href = link.get("href", "")
        if href.startswith("mailto:"):
            email = href.replace("mailto:", "").split("?")[0].strip()
            if email and "@" in email:
                emails.append(email)
        link_text = link.get_text(strip=True)
        email_match = re.search(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', link_text, re.IGNORECASE)
        if email_match:
            emails.append(email_match.group(0))
    
    email_pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
    if text:
        emails.extend(re.findall(email_pattern, text, re.IGNORECASE))
    emails.extend(re.findall(email_pattern, html, re.IGNORECASE))
    emails = list(set(email.lower() for email in emails if email))
    
    # Extract phone numbers
    phone_numbers = []
    for link in soup.find_all("a", href=True):
        href = link.get("href", "")
        if href.startswith("tel:"):
            phone = href.replace("tel:", "").strip()
            phone_digits = re.sub(r'[^\d]', '', phone)
            if len(phone_digits) >= 10:
                if re.match(r'[\d\-\(\)\.\s\+]{10,}', phone):
                    phone_numbers.append(phone)
                elif len(phone_digits) == 10:
                    phone_numbers.append(f"{phone_digits[:3]}-{phone_digits[3:6]}-{phone_digits[6:]}")
    
    for link in soup.find_all("a"):
        link_text = link.get_text(strip=True)
        phone_match = re.search(r'\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}', link_text)
        if phone_match:
            phone_numbers.append(phone_match.group(0))
    
    phone_patterns = [
        r'\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}',
        r'\d{3}[-.\s]?\d{3}[-.\s]?\d{4}',
    ]
    for pattern in phone_patterns:
        phone_numbers.extend(re.findall(pattern, text))
        phone_numbers.extend(re.findall(pattern, html))
    
    # Validate and filter phone numbers
    validated_phones = []
    for p in set(phone_numbers):
        digits = re.sub(r'[^\d]', '', p)
        if len(digits) == 10 or (len(digits) == 11 and digits.startswith('1')):
            if len(set(digits)) < 3:  # Too many repeated digits
                continue
            if len(digits) == 10 and digits.startswith('1') and int(digits) > 1500000000:
                continue
            validated_phones.append(p)
    
    return (text, emails, validated_phones)


def _build_absolute_url(base_domain: str, url: str) -> str:
    """Build an absolute URL from base domain and possibly-relative URL."""
    if not url:
        return ""
    if url.startswith("http"):
        return url
    if url.startswith("/"):
        return f"{base_domain}{url}"
    return f"{base_domain}/{url}"


def _extract_nearest_franchise_results(
    soup: BeautifulSoup, base_domain: str
) -> tuple[str, List[str], List[str], List[str]]:
    """
    Fast-path extractor for nearest-franchise result blocks.

    Returns:
        tuple: (text_content, emails, phone_numbers, local_site_urls)
    """
    results_container = soup.select_one(".ft-zip1-results")
    result_items = soup.select(".ft-zip1-search-result-item")

    if not results_container and not result_items:
        return ("", [], [], [])

    text_parts: List[str] = []
    if results_container:
        container_text = results_container.get_text(separator="\n", strip=True)
        if container_text:
            text_parts.append(container_text)

    emails: List[str] = []
    phones: List[str] = []
    local_site_urls: List[str] = []

    # Gather links and contact info from nearest-franchise result items.
    for item in result_items:
        item_text = item.get_text(separator="\n", strip=True)
        if item_text:
            text_parts.append(item_text)

        for link in item.find_all("a", href=True):
            href = link.get("href", "").strip()
            if not href:
                continue
            if href.startswith("mailto:"):
                email = href.replace("mailto:", "").split("?")[0].strip().lower()
                if email and "@" in email:
                    emails.append(email)
            elif href.startswith("tel:"):
                phone = href.replace("tel:", "").strip()
                if phone:
                    phones.append(phone)
            elif "/local-site/" in href:
                local_site_urls.append(_build_absolute_url(base_domain, href))

            link_text = link.get_text(strip=True)
            if link_text:
                email_matches = re.findall(
                    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b",
                    link_text,
                    re.IGNORECASE,
                )
                emails.extend(match.lower() for match in email_matches)
                phone_matches = re.findall(
                    r"\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}",
                    link_text,
                )
                phones.extend(phone_matches)

    # Fallback: detect any local-site links in container area.
    if results_container:
        for link in results_container.find_all("a", href=True):
            href = link.get("href", "").strip()
            if "/local-site/" in href:
                local_site_urls.append(_build_absolute_url(base_domain, href))

    combined_text = "\n\n".join(t for t in text_parts if t).strip()
    unique_emails = sorted(set(e for e in emails if e))
    unique_phones = sorted(set(p for p in phones if p))
    unique_local_urls = sorted(set(u for u in local_site_urls if u))
    return (combined_text, unique_emails, unique_phones, unique_local_urls)

router = APIRouter()

# Module-level singletons, initialised from the main app's lifespan.
konaos_client: Optional[KonaosClient] = None
shared_http_client: Optional[httpx.AsyncClient] = None


def init_konaos() -> None:
    """Create the shared KonaOS client + HTTP client (idempotent)."""
    global konaos_client, shared_http_client
    if konaos_client is None:
        konaos_client = KonaosClient()
    if shared_http_client is None:
        shared_http_client = httpx.AsyncClient(timeout=20.0)


async def close_konaos() -> None:
    """Tear down the shared clients (called from the main app's lifespan)."""
    global konaos_client, shared_http_client
    if konaos_client:
        await konaos_client.close()
        konaos_client = None
    if shared_http_client:
        await shared_http_client.aclose()
        shared_http_client = None


def verify_api_key(
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    authorization: Optional[str] = Header(None),
) -> str:
    """Accept the original X-API-Key (GPT_API_KEY) or a dashboard JWT bearer."""
    if GPT_API_KEY and x_api_key == GPT_API_KEY:
        return x_api_key
    if authorization and authorization.lower().startswith("bearer "):
        from app.security import decode_access_token

        if decode_access_token(authorization.split(" ", 1)[1]):
            return "jwt"
    raise HTTPException(
        status_code=401,
        detail="Invalid API key or token (send X-API-Key or Authorization: Bearer)",
    )


@router.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok v1.0"}


class SessionUpdateRequest(BaseModel):
    session_key: str


@router.get("/session/status")
async def konaos_session_status(api_key: str = Depends(verify_api_key)):
    """Report the KonaOS session key's health: masked key, age, and whether a
    live probe against KonaOS succeeds. Use this to know when to rotate."""
    init_konaos()
    kc = konaos_client
    valid = await kc.probe_session()
    key = kc.session_key or ""
    age = kc.session_age_days()
    return {
        "configured": bool(key),
        "masked_key": (key[:4] + "…" + key[-4:]) if len(key) >= 8 else "",
        "obtained_days_ago": round(age, 1) if age is not None else None,
        "valid": valid,
        "hint": None if valid else (
            "Session key is stale. Paste a fresh one from admin.konaos.com "
            "devtools (jsessionId cookie) via POST /api/konaos/session."
        ),
    }


@router.post("/session/status")
async def konaos_session_refresh(api_key: str = Depends(verify_api_key)):
    """Force a refresh attempt: re-read the persisted key, then try login."""
    init_konaos()
    kc = konaos_client
    try:
        await kc._login()
        valid = await kc.probe_session()
        return {"refreshed": True, "valid": valid}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Refresh failed: {e}")


@router.post("/session")
async def konaos_session_update(
    body: SessionUpdateRequest, api_key: str = Depends(verify_api_key)
):
    """Adopt a new session key (e.g. pasted from browser devtools after the
    monthly rotation). Persists it so the API and worker both pick it up."""
    init_konaos()
    kc = konaos_client
    kc.set_session_key(body.session_key.strip())
    valid = await kc.probe_session()
    return {
        "updated": True,
        "valid": valid,
        "detail": "Session key accepted and verified against KonaOS."
        if valid
        else "Session key saved but a probe against KonaOS FAILED — double-check the key.",
    }


@router.get("/zipcode-site", response_model=ZipcodeWebsiteResponse)
async def get_zipcode_site(
    zipcode: str = Query(..., description="US zipcode to look up on Kona Ice 'Find A Kona' page or Travelin' Tom's page"),
    use_proxy: bool = Query(
        False,
        alias="useProxy",
        description="If true, fetch directly via configured US proxy instead of ScraperAPI",
    ),
    brand: str = Query(
        "Kona_ice",
        description="Brand to search for: 'Kona_ice' or 'Travelin_tom'",
    ),
    api_key: str = Depends(verify_api_key),
):
    """
    Fetch brand-specific truck finder page content for a given zipcode.

    - For brand="Kona_ice", searches Kona Ice 'Find A Kona' page
    - For brand="Travelin_tom", searches Travelin' Tom's Coffee truck finder page
    - By default (useProxy=false), uses ScraperAPI to bypass geo blocking / Cloudflare
      and returns the page content in markdown format.
    - If useProxy=true, performs a direct GET using the configured US_HTTP_PROXY /
      US_HTTPS_PROXY, returning the raw HTML as markdown string.
    """
    # Validate and normalize brand parameter
    brand = brand.lower()
    if brand not in ["kona_ice", "travelin_tom"]:
        raise HTTPException(
            status_code=400,
            detail="Invalid brand parameter. Must be 'Kona_ice' or 'Travelin_tom'",
        )
    
    # Set target URL based on brand
    if brand == "kona_ice":
        target_url = f"https://www.kona-ice.com/find-a-kona/search/?zipcode={zipcode}"
    else:  # travelin_tom
        # Travelin' Tom search endpoint uses /search/ route, not base finder page.
        target_url = f"https://travelintomscoffee.com/find-a-toms-coffee-truck/search/?zipcode={zipcode}"

    # Path 1: Direct via proxy (custom code)
    if use_proxy:
        proxies = _get_us_proxies()
        if not proxies:
            raise HTTPException(
                status_code=500,
                detail="US_HTTP_PROXY / US_HTTPS_PROXY not configured on server",
            )

        # Set base domain and referer based on brand
        base_domain = "https://www.kona-ice.com" if brand == "kona_ice" else "https://travelintomscoffee.com"
        referer = f"{base_domain}/find-a-kona/" if brand == "kona_ice" else f"{base_domain}/find-a-toms-coffee-truck/"
        
        # Browser-style headers similar to tests
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,*/*;q=0.8"
            ),
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": referer,
            "Connection": "keep-alive",
        }

        # Option A: One request with follow_redirects=True first.
        # - If final URL is local-site → content is in that response (1 request).
        # - If final URL is search page → extract; if client-side redirect to local-site, do 1 more request (2 total).
        def _is_cloudflare_challenge(html: str, status_403: bool = False) -> bool:
            if not html:
                return False
            h = html.lower()
            return (
                ("just a moment" in h and "checking your browser" in h)
                or ("attention required" in h and "cloudflare" in h and "ray id" in h)
                or ("checking your browser before accessing" in h)
                or ("ddos protection by cloudflare" in h and "just a moment" in h)
                or (status_403 and "cloudflare" in h and "challenge" in h)
            )

        try:
            async with httpx.AsyncClient(
                timeout=20.0,
                follow_redirects=True,
                headers=headers,
                proxies=proxies,
            ) as client:
                response = await client.get(target_url)
                response.raise_for_status()
                final_url = str(response.url)
                html = response.text

                if _is_cloudflare_challenge(html, response.status_code == 403):
                    raise HTTPException(
                        status_code=502,
                        detail="Received Cloudflare challenge page. The proxy may be blocked or the request was flagged.",
                    )

                # HTTP redirect to local-site: content is already in this response (1 request).
                # Both Kona Ice and Travelin' Tom's use /local-site/ pattern
                if "/local-site/" in final_url:
                    text, emails, phones = _extract_content_from_html(html)
                    return ZipcodeWebsiteResponse(
                        zipcode=zipcode,
                        contentMarkdown=text,
                        emails=emails,
                        phoneNumbers=phones,
                        brand=brand,  # Include brand in response
                    )  # type: ignore[arg-type]

                soup_temp = BeautifulSoup(html, "html.parser")
                local_site_url = None

                # Meta refresh
                meta_refresh = soup_temp.find("meta", attrs={"http-equiv": re.compile("refresh", re.I)})
                if meta_refresh and meta_refresh.get("content"):
                    content = meta_refresh.get("content", "")
                    url_match = re.search(r"url=([^;]+)", content, re.I)
                    if url_match:
                        redirect_url = url_match.group(1).strip()
                        if "/local-site/" in redirect_url:
                            local_site_url = _build_absolute_url(base_domain, redirect_url)

                # JavaScript redirect
                if not local_site_url:
                    for script in soup_temp.find_all("script"):
                        script_text = script.string or ""
                        location_match = re.search(
                            r'(?:window\.location|location\.href)\s*=\s*["\']([^"\']+)["\']', script_text
                        )
                        if location_match:
                            redirect_url = location_match.group(1)
                            if "/local-site/" in redirect_url:
                                local_site_url = _build_absolute_url(base_domain, redirect_url)
                                break

                # Local-site link fallback
                if not local_site_url:
                    local_site_links = soup_temp.find_all("a", href=re.compile(r"/local-site/", re.I))
                    if local_site_links:
                        href = local_site_links[0].get("href", "")
                        if href:
                            local_site_url = _build_absolute_url(base_domain, href)

                # Fast path: detect nearest-franchise result blocks (e.g., Travelin' Tom's 3 nearest franchises).
                nearest_text, nearest_emails, nearest_phones, nearest_local_urls = _extract_nearest_franchise_results(
                    soup_temp, base_domain
                )
                if nearest_text and "/local-site/" not in final_url:
                    return ZipcodeWebsiteResponse(
                        zipcode=zipcode,
                        contentMarkdown=nearest_text,
                        emails=nearest_emails,
                        phoneNumbers=nearest_phones,
                        brand=brand,
                    )  # type: ignore[arg-type]

                # Fallback to the generic extractor when no fast-path result blocks were detected.
                search_text, search_emails, search_phones = _extract_content_from_html(html)

                if not local_site_url and nearest_local_urls:
                    # Use first local-site link if we have one and may need richer details.
                    local_site_url = nearest_local_urls[0]

                local_site_text = ""
                local_site_emails: List[str] = []
                local_site_phones: List[str] = []

                if local_site_url and "/local-site/" in local_site_url:
                    try:
                        local_site_response = await client.get(local_site_url)
                        local_site_response.raise_for_status()
                        local_site_html = local_site_response.text
                        if not _is_cloudflare_challenge(
                            local_site_html, local_site_response.status_code == 403
                        ):
                            local_site_text, local_site_emails, local_site_phones = _extract_content_from_html(
                                local_site_html
                            )
                    except Exception:
                        pass

                combined_text_parts = [t for t in (search_text, local_site_text) if t]
                combined_text = "\n\n".join(combined_text_parts).strip() or search_text or local_site_text
                all_emails = list(set(search_emails + local_site_emails))
                all_phones = list(set(search_phones + local_site_phones))

                return ZipcodeWebsiteResponse(
                    zipcode=zipcode,
                    contentMarkdown=combined_text,
                    emails=all_emails,
                    phoneNumbers=all_phones,
                    brand=brand,
                )  # type: ignore[arg-type]

        except HTTPException:
            raise
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 403:
                raise HTTPException(
                    status_code=502,
                    detail=(
                        f"Received 403 Forbidden from {'Kona Ice' if brand == 'kona_ice' else 'Travelin Toms'} via configured US proxy. "
                        "This usually means the proxy IP is blocked or not acceptable to Cloudflare."
                    ),
                )
            raise HTTPException(
                status_code=e.response.status_code,
                detail=f"Error fetching zipcode site via proxy: {e.response.text[:500]}",
            )
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"Error fetching zipcode site via proxy: {str(e)}",
            )

    # Path 2: ScraperAPI (default)
    if not SCRAPERAPI_KEY:
        raise HTTPException(
            status_code=500,
            detail="SCRAPERAPI_KEY not configured on server",
        )

    scraper_url = "https://api.scraperapi.com/"
    params = {
        "api_key": SCRAPERAPI_KEY,
        "url": target_url,
        "output_format": "markdown",
        "country_code": "us",
    }

    try:
        if shared_http_client:
            response = await shared_http_client.get(scraper_url, params=params)
            response.raise_for_status()
        else:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.get(scraper_url, params=params)
                response.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=e.response.status_code,
            detail=f"ScraperAPI error: {e.response.text[:500]}",
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error fetching zipcode site content: {str(e)}",
        )

    # ScraperAPI returns the markdown directly as text when output_format=markdown
    content_markdown = response.text
    
    # Extract contact details from markdown content
    email_pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
    emails = list(set(re.findall(email_pattern, content_markdown, re.IGNORECASE)))
    
    phone_patterns = [
        r'\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}',  # Standard US format
        r'\d{3}[-.\s]?\d{3}[-.\s]?\d{4}',  # Without parentheses
        r'\+?1[-.\s]?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}',  # With country code
    ]
    phone_numbers = []
    for pattern in phone_patterns:
        found = re.findall(pattern, content_markdown)
        phone_numbers.extend(found)
    # Remove duplicates and clean up
    phone_numbers = list(set(phone_numbers))
    # Remove very short matches that are likely false positives
    phone_numbers = [p for p in phone_numbers if len(re.sub(r'[^\d]', '', p)) >= 10]
    
    return ZipcodeWebsiteResponse(
        zipcode=zipcode,
        contentMarkdown=content_markdown,
        emails=emails,
        phoneNumbers=phone_numbers,
        brand=brand
    )  # type: ignore[arg-type]


@router.get("/now")
async def get_current_time(
    days: int = Query(0, description="Add or subtract days from current time. Use negative for past, positive for future."),
    api_key: str = Depends(verify_api_key)
):
    """
    Get the current timestamp and date information, optionally adjusted by days.
    
    Use this endpoint to get timestamps for date ranges - no manual calculation needed!
    Examples:
    - /now?days=0 (or just /now) - current time (for "last 2 weeks" toDate)
    - /now?days=-14 - 2 weeks ago (for "last 2 weeks" fromDate)
    - /now?days=14 - 2 weeks from now (for "next 2 weeks" toDate)
    """
    import time
    from datetime import datetime, timedelta
    
    now_dt = datetime.now()
    adjusted_dt = now_dt + timedelta(days=days)
    adjusted_ms = int(adjusted_dt.timestamp() * 1000)
    
    return {
        "timestamp": adjusted_ms,  # Use this for fromDate/toDate parameters
        "date": adjusted_dt.strftime("%Y-%m-%d"),
        "datetime": adjusted_dt.strftime("%Y-%m-%d %H:%M:%S"),
        "year": adjusted_dt.year,
        "month": adjusted_dt.month,
        "day": adjusted_dt.day,
        "dayOfWeek": adjusted_dt.strftime("%A"),
        "daysOffset": days,  # Show what offset was applied
    }


@router.get("/events")
async def get_events(
    limit: int = Query(10, ge=1, le=100, description="Number of results per page"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
    sort_column: Optional[str] = Query(None, description="Column to sort by"),
    sort_type: Optional[str] = Query(None, description="Sort direction (asc/desc)"),
    search_text: str = Query("", alias="searchText", description="Search text"),
    to_date: Optional[int] = Query(None, alias="toDate", description="End date timestamp (Unix epoch)"),
    from_date: Optional[int] = Query(None, alias="fromDate", description="Start date timestamp (Unix epoch)"),
    apply_activated_status: bool = Query(False, alias="applyActivatedStatus"),
    active_event: str = Query("", alias="activeEvent"),
    activated: bool = Query(False),  # Default to False to match frontend behavior
    deleted: bool = Query(False),
    asset_ids: Optional[list[str]] = Query(None, alias="assetIds"),
    status_list: Optional[list] = Query(None, alias="statusList"),
    user_ids: Optional[list] = Query(None, alias="userIds"),
    brand_ids: Optional[list[str]] = Query(None, alias="brandIds"),
    un_assigned_asset_events: bool = Query(False, alias="unAssignedAssetEvents"),
    pre_pay_event: Optional[bool] = Query(None, alias="prePayEvent"),
    kurbside_event: Optional[bool] = Query(None, alias="kurbsideEvent"),
    api_key: str = Depends(verify_api_key)
):
    """
    Get a list of events with optional filtering.
    
    Returns a paginated list of events matching the specified criteria.
    """
    # Default to last 30 days if no dates provided
    import time
    if from_date is None or from_date == 0:
        from_date = int((time.time() - (30 * 24 * 60 * 60)) * 1000)  # 30 days ago
    if to_date is None or to_date == 0:
        to_date = int(time.time() * 1000)  # now
    
    # Note: KonaOS API filters by startDateTime, so events that start within the range will be returned
    
    # Default brandIds to user's brands if not provided
    if not brand_ids and konaos_client.brand_ids:
        brand_ids = konaos_client.brand_ids
    
    params = {
        "limit": limit,
        "offset": offset,
        "sortColumn": sort_column if sort_column is not None else None,
        "sortType": sort_type if sort_type is not None else None,
        "searchText": search_text or "",
        "fromDate": from_date,
        "toDate": to_date,
        "applyActivatedStatus": apply_activated_status,
        "activeEvent": active_event or "",
        "activated": activated,
        "deleted": deleted,
        "assetIds": asset_ids or [],
        "statusList": status_list or [],
        "userIds": user_ids or [],
        "brandIds": brand_ids or [],
        "unAssignedAssetEvents": un_assigned_asset_events,
        "prePayEvent": pre_pay_event,  # None is valid
        "kurbsideEvent": kurbside_event,  # None is valid
    }
    
    try:
        # Use KonaOS search directly - grid-data endpoint handles searchText well
        # For multi-term searches (e.g., "Liberty Sports Park Travelin Toms"), 
        # use the first term for KonaOS search, then filter client-side for all terms
        search_terms_for_filtering = []
        if search_text and search_text.strip():
            search_terms_list = search_text.strip().split()
            if len(search_terms_list) > 1:
                # Multi-term search: use first term for KonaOS, filter client-side for all
                params['searchText'] = search_terms_list[0]  # Use first term (likely location)
                search_terms_for_filtering = [term.lower() for term in search_terms_list]
            else:
                # Single term: use as-is
                params['searchText'] = search_text.strip()
        else:
            params['searchText'] = ''
        
        # Debug: Log what we're sending
        print(f"[DEBUG] main.py: search_text={repr(search_text)}, params['searchText']={repr(params.get('searchText'))}, filter_terms={search_terms_for_filtering}, brandIds={len(params.get('brandIds', []))}")
        
        response_data = await konaos_client.get_events_monthly(**params)
        
        # Debug: Log response
        print(f"[DEBUG] main.py: response count={response_data.get('count')}, events={len(response_data.get('data', []))}")
        # Remove unreliable/unnecessary fields
        response_data.pop('totalCount', None)
        response_data.pop('userResult', None)
        response_data.pop('assetResult', None)
        response_data.pop('statusResult', None)
        response_data.pop('sortColumn', None)
        response_data.pop('sortType', None)
        
        # Simplify event data - only include essential fields
        simplified_events = []
        from datetime import datetime
        
        # Filter events by date range and additional search terms
        # KonaOS search handles basic searchText, but we do additional client-side filtering
        # for multi-term queries like "Liberty Sports Park Travelin Toms"
        original_event_count = len(response_data.get('data', []))
        filtered_out_count = 0
        
        # Filter events by date range and search terms
        for event in response_data.get('data', []):
            # Additional client-side filtering for multi-term searches
            # Check if all search terms appear in event name, asset names, address, staff names, or brand-related fields
            if search_terms_for_filtering:
                event_name = event.get('name', '').lower()
                address_line1 = event.get('addressLine1', '').lower()
                city = event.get('city', '').lower()
                brand_id = event.get('brandId', '')
                
                # Get asset names from assetNamesList or assetNames
                asset_names = []
                asset_names_list = event.get('assetNamesList') or []
                for asset in asset_names_list:
                    if isinstance(asset, dict):
                        asset_names.append(asset.get('name', '').lower())
                    elif isinstance(asset, str):
                        asset_names.append(asset.lower())
                # Also check assetNames string field
                asset_names_str = event.get('assetNames', '')
                if asset_names_str:
                    asset_names.extend([a.strip().lower() for a in asset_names_str.split(',')])
                
                # Get staff names
                staff_names = []
                staff_names_list = event.get('staffNamesList') or []
                for staff in staff_names_list:
                    if isinstance(staff, dict):
                        staff_names.append(staff.get('name', '').lower() or staff.get('firstName', '').lower())
                    elif isinstance(staff, str):
                        staff_names.append(staff.lower())
                # Also check staffNames string field
                staff_names_str = event.get('staffNames', '')
                if staff_names_str:
                    staff_names.extend([s.strip().lower() for s in staff_names_str.split(',')])
                
                # Check if all search terms appear in any of these fields
                # Brand/asset associations:
                # - BEV assets → Travelin' Toms brand
                # - KEV assets → Kona Ice brand
                event_link = event.get('link', '').lower()
                brand_id = event.get('brandId', '')
                
                # Check for brand associations via asset names
                has_bev = any('bev' in asset_name for asset_name in asset_names)
                has_kev = any('kev' in asset_name for asset_name in asset_names)
                
                all_terms_found = True
                for term in search_terms_for_filtering:
                    term_lower = term.lower()
                    term_found = (
                        term_lower in event_name or
                        term_lower in address_line1 or
                        term_lower in city or
                        term_lower in event_link or
                        any(term_lower in asset_name for asset_name in asset_names) or
                        any(term_lower in staff_name for staff_name in staff_names) or
                        # Brand name patterns
                        ('travelin' in term_lower and ('travelin' in event_link or 'travelin' in event_name or has_bev)) or
                        ('toms' in term_lower and ('toms' in event_link or 'toms' in event_name or has_bev)) or
                        ('kona' in term_lower and ('kona' in event_link or 'kona' in event_name or has_kev)) or
                        ('ice' in term_lower and ('ice' in event_link or 'ice' in event_name or has_kev)) or
                        # Asset associations
                        ('bev' in term_lower and has_bev) or
                        ('kev' in term_lower and has_kev)
                    )
                    if not term_found:
                        all_terms_found = False
                        break
                
                if not all_terms_found:
                    filtered_out_count += 1
                    continue
            start_ts = event.get('startDateTime')
            end_ts = event.get('endDateTime')
            
            # Skip events outside the requested date range
            if from_date and start_ts and start_ts < from_date:
                filtered_out_count += 1
                continue
            if to_date and start_ts and start_ts > to_date:
                filtered_out_count += 1
                continue
            
            # Format dates/times for readability
            start_date_str = None
            start_time_str = None
            end_date_str = None
            end_time_str = None
            duration_str = None
            
            if start_ts:
                start_dt = datetime.fromtimestamp(start_ts / 1000)
                start_date_str = start_dt.strftime('%B %d, %Y')  # e.g., "November 17, 2025"
                start_time_str = start_dt.strftime('%I:%M %p')  # e.g., "09:00 AM"
            
            if end_ts:
                end_dt = datetime.fromtimestamp(end_ts / 1000)
                end_date_str = end_dt.strftime('%B %d, %Y')
                end_time_str = end_dt.strftime('%I:%M %p')
            
            # Create duration string
            if start_date_str and end_date_str:
                if start_date_str == end_date_str:
                    # Same day
                    duration_str = f"{start_date_str}, {start_time_str} - {end_time_str}"
                else:
                    # Multi-day
                    duration_str = f"{start_date_str} {start_time_str} - {end_date_str} {end_time_str}"
            
            # Extract asset names - use same logic as filtering
            asset_names = []
            # Check assetNamesList first (list format)
            asset_names_list = event.get('assetNamesList') or []
            for asset in asset_names_list:
                if isinstance(asset, dict):
                    asset_names.append(asset.get('name', '') or asset.get('assetName', ''))
                elif isinstance(asset, str):
                    asset_names.append(asset)
            # Also check assetNames string field (comma-separated)
            asset_names_str = event.get('assetNames', '')
            if asset_names_str:
                asset_names.extend([a.strip() for a in asset_names_str.split(',') if a.strip()])
            # Fallback to assetsList if needed
            if not asset_names:
                assets_list = event.get('assetsList') or []
                for asset in assets_list:
                    if isinstance(asset, dict):
                        asset_name = asset.get('assetName') or asset.get('name', '')
                        if asset_name:
                            asset_names.append(asset_name)
            
            # Extract staff names - handle None case
            staff_names = []
            staffs_list = event.get('staffsList') or []
            if isinstance(staffs_list, list):
                for staff in staffs_list:
                    if isinstance(staff, dict):
                        first_name = staff.get('firstName', '')
                        last_name = staff.get('lastName', '')
                        full_name = f"{first_name} {last_name}".strip()
                        if full_name:
                            staff_names.append(full_name)
            
            # Build full address string
            address_parts = []
            address_line1 = event.get('addressLine1')
            address_line2 = event.get('addressLine2')
            city = event.get('city')
            state = event.get('state')
            zip_code = event.get('zipCode')
            
            if address_line1:
                address_parts.append(address_line1)
            if address_line2:
                address_parts.append(address_line2)
            if city or state or zip_code:
                city_state_zip = ', '.join(filter(None, [city, state, zip_code]))
                if city_state_zip:
                    address_parts.append(city_state_zip)
            
            full_address = ', '.join(address_parts) if address_parts else None
            
            simplified_event = {
                'id': event.get('id'),
                'name': event.get('name'),
                'city': city or '',
                'state': state or '',
                'addressLine1': address_line1,
                'addressLine2': address_line2,
                'zipCode': zip_code,
                'fullAddress': full_address,
                'startDateTime': start_ts,
                'endDateTime': end_ts,
                'startDate': start_date_str,  # Human-readable date
                'startTime': start_time_str,  # Human-readable time
                'endDate': end_date_str,
                'endTime': end_time_str,
                'duration': duration_str,  # Combined duration string
                'staffNames': staff_names,
                'assetNames': asset_names,
            }
            simplified_events.append(simplified_event)
        
        # Update count to reflect filtered results
        filtered_count = len(simplified_events)
        
        # Return only the fields we want - explicitly exclude any extra fields
        result = {
            "data": simplified_events,
            "count": filtered_count,  # Use filtered count, not original
            "offset": response_data.get('offset', 0),
            "limit": response_data.get('limit', 10),
            "searchText": search_text or "",  # Preserve the original searchText we sent, not what KonaOS returns
        }
        # Ensure no extra fields slip through
        return result
    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=e.response.status_code,
            detail=f"KonaOS API error: {e.response.text}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error fetching events: {str(e)}"
        )


@router.get("/events/{event_id}", response_model=EventDetails)
async def get_event(
    event_id: str,
    deleted: Optional[str] = Query(None, description="Filter by deleted status"),
    api_key: str = Depends(verify_api_key)
):
    """
    Get detailed information about a specific event.
    
    Returns comprehensive event details including staff assignments, assets, and contact information.
    """
    try:
        response_data = await konaos_client.get_event_details(event_id, deleted=deleted)
        return EventDetails(**response_data)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            raise HTTPException(status_code=404, detail="Event not found")
        raise HTTPException(
            status_code=e.response.status_code,
            detail=f"KonaOS API error: {e.response.text}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error fetching event details: {str(e)}"
        )


@router.post("/events", response_model=EventOperationResponse)
async def create_event(
    event: CreateEventRequest,
    api_key: str = Depends(verify_api_key)
):
    """
    Create a new event.
    
    Creates an event with the provided details. All required fields must be provided.
    Returns a success message if the event was created successfully.
    """
    try:
        response_data = await konaos_client.create_event(
            name=event.name,
            start_date_time=event.start_date_time,
            end_date_time=event.end_date_time,
            business_name=event.business_name,
            address_line1=event.address_line1,
            city=event.city,
            state=event.state,
            zip_code=event.zip_code,
            contact_name=event.contact_name,
            contact_email=event.contact_email,
            brand_id=event.brand_id,
            client_id=event.client_id,
            contact_title=event.contact_title,
            contact_phone=event.contact_phone,
            contact_phone_country_code=event.contact_phone_country_code,
            county=event.county,
            country=event.country,
            admin_notes=event.admin_notes,
            notes=event.notes,
            event_status=event.event_status,
            manual_status=event.manual_status,
            payment_term=event.payment_term
        )
        return EventOperationResponse(**response_data)
    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=e.response.status_code,
            detail=f"KonaOS API error: {e.response.text}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error creating event: {str(e)}"
        )


@router.put("/events/{event_id}", response_model=EventOperationResponse)
async def update_event(
    event_id: str,
    event: UpdateEventRequest,
    api_key: str = Depends(verify_api_key)
):
    """
    Update an existing event.
    
    Updates the specified fields of an event. Only provided fields will be updated.
    Returns a success message if the event was updated successfully.
    """
    try:
        # Forward all schema-approved fields so valid update fields are not dropped silently.
        update_kwargs = event.model_dump(
            by_alias=True,
            exclude_none=True,
            exclude_unset=True,
        )
        print(
            f"[DEBUG] update_event: event_id={event_id}, "
            f"forwarding_keys={sorted(update_kwargs.keys())}"
        )

        response_data = await konaos_client.update_event(
            event_id=event_id,
            **update_kwargs
        )
        return EventOperationResponse(**response_data)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            raise HTTPException(status_code=404, detail="Event not found")
        raise HTTPException(
            status_code=e.response.status_code,
            detail=f"KonaOS API error: {e.response.text}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error updating event: {str(e)}"
        )


@router.delete("/events/{event_id}", response_model=EventOperationResponse)
async def delete_event(
    event_id: str,
    delete_request: Optional[DeleteEventRequest] = None,
    api_key: str = Depends(verify_api_key)
):
    """
    Delete (soft delete) an event.
    
    Soft deletes the specified event. The event will be marked as deleted but not permanently removed.
    If update_series is True and this is a recurring event, the entire series will be deleted.
    Returns a success message if the event was deleted successfully.
    """
    try:
        update_series = False
        if delete_request:
            update_series = delete_request.update_series
        
        response_data = await konaos_client.delete_event(
            event_id=event_id,
            update_series=update_series
        )
        return EventOperationResponse(**response_data)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            raise HTTPException(status_code=404, detail="Event not found")
        raise HTTPException(
            status_code=e.response.status_code,
            detail=f"KonaOS API error: {e.response.text}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error deleting event: {str(e)}"
        )


@router.get("/staff", response_model=StaffResponse)
async def get_staff(
    limit: int = Query(10, ge=1, le=100, description="Number of results per page"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
    sort_column: Optional[str] = Query(None, description="Column to sort by"),
    sort_type: Optional[str] = Query(None, description="Sort direction (asc/desc)"),
    search_text: str = Query("", description="Search text"),
    from_date: Optional[int] = Query(None, description="Start date timestamp (Unix epoch)"),
    to_date: Optional[int] = Query(None, description="End date timestamp (Unix epoch)"),
    brand_ids: Optional[str] = Query(None, alias="brandIds", description='Brand IDs filter. Use "ALL" to get all brands.'),
    role_id: Optional[str] = Query(None, alias="roleId"),
    api_key: str = Depends(verify_api_key)
):
    """
    Get a list of staff members with optional filtering.
    
    Returns a paginated list of staff members matching the specified criteria.
    """
    params = {
        "limit": limit,
        "offset": offset,
        "sortColumn": sort_column or "",
        "sortType": sort_type or "",
        "searchText": search_text or "",
        "fromDate": from_date if from_date is not None else 0,
        "toDate": to_date if to_date is not None else 0,
        "roleId": role_id or "",
    }
    
    # Remove None values (but keep empty strings)
    params = {k: v for k, v in params.items() if v is not None}
    
    try:
        # Handle brandIds - convert string to list for processing, or use None for default "ALL"
        brand_ids_param = None
        if brand_ids:
            if isinstance(brand_ids, str):
                if brand_ids.upper() == "ALL":
                    brand_ids_param = "ALL"
                else:
                    # Comma-separated string -> list
                    brand_ids_param = [bid.strip() for bid in brand_ids.split(',') if bid.strip()]
        
        response_data = await konaos_client.get_staff_grid_data(brand_ids=brand_ids_param, **params)
        # Remove unreliable totalCount
        response_data.pop('totalCount', None)
        
        # Filter staff data to only include essential fields
        if 'data' in response_data and isinstance(response_data['data'], list):
            filtered_data = []
            for staff_item in response_data['data']:
                filtered_staff = {
                    'id': staff_item.get('id'),
                    'userId': staff_item.get('userId'),  # Required for filtering events
                    'firstName': staff_item.get('firstName'),
                    'lastName': staff_item.get('lastName'),
                    'email': staff_item.get('email'),
                    'phoneNum': staff_item.get('phoneNum'),
                    'roleName': staff_item.get('roleName'),
                    'activated': staff_item.get('activated'),
                }
                # Only include if we have at least id and name
                if filtered_staff.get('id') and filtered_staff.get('firstName'):
                    filtered_data.append(filtered_staff)
            response_data['data'] = filtered_data
        
        return StaffResponse(**response_data)
    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=e.response.status_code,
            detail=f"KonaOS API error: {e.response.text}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error fetching staff: {str(e)}"
        )


async def _resolve_default_availability_user_ids(brand_ids: Optional[list[str]]) -> list[str]:
    """
    Build default userIds for staff availability requests.

    Includes special values used by KonaOS UI and all discovered staff userIds.
    """
    discovered_user_ids: list[str] = []
    page_limit = 100
    current_offset = 0
    total_count: Optional[int] = None

    while True:
        params = {
            "limit": page_limit,
            "offset": current_offset,
            "sortColumn": "",
            "sortType": "",
            "searchText": "",
            "fromDate": 0,
            "toDate": 0,
            "roleId": "",
        }
        staff_page = await konaos_client.get_staff_grid_data(brand_ids=brand_ids, **params)

        page_items = staff_page.get("data", []) if isinstance(staff_page, dict) else []
        if not isinstance(page_items, list):
            page_items = []

        for staff_item in page_items:
            if isinstance(staff_item, dict):
                user_id = staff_item.get("userId")
                if isinstance(user_id, str) and user_id.strip():
                    discovered_user_ids.append(user_id.strip())

        if total_count is None:
            count_val = staff_page.get("count") if isinstance(staff_page, dict) else None
            if isinstance(count_val, int) and count_val >= 0:
                total_count = count_val

        if not page_items:
            break
        current_offset += page_limit
        if total_count is not None and current_offset >= total_count:
            break

    # Match KonaOS UI semantics by including these special pseudo-user IDs.
    ordered_defaults = ["UNASSIGNED", *discovered_user_ids, "DEACTIVATED"]
    seen: set[str] = set()
    unique_user_ids: list[str] = []
    for user_id in ordered_defaults:
        if user_id not in seen:
            seen.add(user_id)
            unique_user_ids.append(user_id)
    return unique_user_ids


@router.post("/staff", response_model=EventOperationResponse)
async def create_staff(
    staff: CreateStaffRequest,
    api_key: str = Depends(verify_api_key)
):
    """
    Create a new staff member.

    You can pass either:
    - roleId directly, or
    - staffType (server, manager, worker, driver) and configure env role mappings.
    """
    try:
        response_data = await konaos_client.create_staff(
            first_name=staff.first_name,
            last_name=staff.last_name,
            email=staff.email,
            phone_num=staff.phone_num,
            num_country_code=staff.num_country_code,
            staff_type=staff.staff_type,
            role_id=staff.role_id,
            alternate_num_country_code=staff.alternate_num_country_code,
            alternate_phone_num=staff.alternate_phone_num,
            emergency_contact_person_name=staff.emergency_contact_person_name,
            emergency_num_country_code=staff.emergency_num_country_code,
            emergency_phone_num=staff.emergency_phone_num,
            hourly_rate=staff.hourly_rate,
            address=staff.address,
            city=staff.city,
            state=staff.state,
            country=staff.country,
            zip_code=staff.zip_code,
            bio_image_file_id=staff.bio_image_file_id,
            bio=staff.bio,
            staff_brand_list=[brand.model_dump(by_alias=True) for brand in staff.staff_brand_list],
            access_group_permissions_input=staff.access_group_permissions_input,
            access_permissions_updated=staff.access_permissions_updated,
        )
        return EventOperationResponse(**response_data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=e.response.status_code,
            detail=f"KonaOS API error: {e.response.text}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error creating staff: {str(e)}"
        )


@router.get("/staff/availability", response_model=StaffAvailabilityResponse)
async def get_staff_availability(
    limit: int = Query(2000, ge=1, le=2000, description="Number of results per page"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
    sort_column: Optional[str] = Query(None, alias="sortColumn", description="Column to sort by"),
    sort_type: Optional[str] = Query(None, alias="sortType", description="Sort direction (asc/desc)"),
    search_text: str = Query("", alias="searchText", description="Search text"),
    from_date: Optional[int] = Query(None, alias="fromDate", description="Start date timestamp (Unix epoch in milliseconds)"),
    to_date: Optional[int] = Query(None, alias="toDate", description="End date timestamp (Unix epoch in milliseconds)"),
    activated: bool = Query(True, description="Filter by activated status"),
    apply_activated_status: bool = Query(True, alias="applyActivatedStatus", description="Apply activated status filter"),
    asset_ids: Optional[list[str]] = Query(None, alias="assetIds", description="Filter by asset IDs"),
    user_ids: Optional[list[str]] = Query(None, alias="userIds", description="Filter by user IDs (staff member IDs)"),
    brand_ids: Optional[list[str]] = Query(None, alias="brandIds", description="Filter by brand IDs"),
    display_event_task_of_shift: bool = Query(True, alias="displayEventTaskOfShift", description="Display event task of shift"),
    api_key: str = Depends(verify_api_key)
):
    """
    Get staff availability/unavailability calendar view.
    
    Returns reported availability and unavailability records for staff members.
    Use this to see when staff members have marked themselves as available or unavailable.
    """
    # Handle dates: validate and set defaults
    import time
    
    if from_date is None or from_date == 0:
        # Default to 30 days ago
        from_date = int((time.time() - (30 * 24 * 60 * 60)) * 1000)
    if to_date is None or to_date == 0:
        # Default to now
        to_date = int(time.time() * 1000)
    
    # Validate date range - swap if backwards
    if from_date > to_date:
        print(f"[WARNING] Date range is backwards (fromDate > toDate), swapping dates")
        from_date, to_date = to_date, from_date

    try:
        resolved_user_ids = user_ids or await _resolve_default_availability_user_ids(brand_ids=brand_ids)
        response_data = await konaos_client.get_staff_availability(
            limit=limit,
            offset=offset,
            sortColumn=sort_column,
            sortType=sort_type,
            searchText=search_text or "",
            fromDate=from_date,
            toDate=to_date,
            activated=activated,
            applyActivatedStatus=apply_activated_status,
            assetIds=asset_ids or [],
            userIds=resolved_user_ids,
            brandIds=brand_ids or []
        )
        return StaffAvailabilityResponse(**response_data)
    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=e.response.status_code,
            detail=f"KonaOS API error: {e.response.text}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error fetching staff availability: {str(e)}"
        )


@router.get("/staff/shifts-and-availability", response_model=StaffScheduleUsersListResponse)
async def get_staff_shifts_and_availability(
    from_date: Optional[int] = Query(
        None,
        alias="fromDate",
        description="Start of range (Unix epoch ms). Defaults to ~30 days ago if omitted or 0.",
    ),
    to_date: Optional[int] = Query(
        None,
        alias="toDate",
        description="End of range (Unix epoch ms). Omit or use 0 for open end (matches admin empty toDate).",
    ),
    brand_ids: Optional[list[str]] = Query(
        None,
        alias="brandIds",
        description="Brand ID(s). Uses KONAOS_BRAND_IDS from env when omitted.",
    ),
    event_id: Optional[str] = Query(None, alias="eventId", description="Optional event filter"),
    activated: bool = Query(True, description="Filter activated staff"),
    apply_activated_status: bool = Query(
        True,
        alias="applyActivatedStatus",
        description="Apply activated status filter",
    ),
    limit: int = Query(199, ge=1, le=2000, description="Max users to return"),
    api_key: str = Depends(verify_api_key),
):
    """
    Staff roster for a date window with nested availability slots per user.

    Proxies KonaOS GET `/api/v1/secure/staffs-schedule/users-list` (same query shape as admin).
    Use this to see who is on the roster and their `staffAvailabilitiesList` windows for scheduling.
    """
    import time

    if from_date is None or from_date == 0:
        from_date = int((time.time() - (30 * 24 * 60 * 60)) * 1000)

    to_forward: Optional[int] = to_date
    if to_date is not None and to_date == 0:
        to_forward = None

    try:
        response_data = await konaos_client.get_staff_schedule_users_list(
            from_date=from_date,
            to_date=to_forward,
            brand_ids=brand_ids,
            event_id=event_id,
            activated=activated,
            apply_activated_status=apply_activated_status,
            limit=limit,
        )
        return StaffScheduleUsersListResponse(**response_data)
    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=e.response.status_code,
            detail=f"KonaOS API error: {e.response.text}",
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error fetching staff shifts and availability: {str(e)}",
        )


@router.get("/clients/industries", response_model=List[ClientIndustryType])
async def get_client_industries(
    api_key: str = Depends(verify_api_key)
):
    """
    Get list of client industry types.
    
    Returns all available client industry types (e.g., "Athletics", "Church", etc.).
    Use these IDs in the /clients endpoint to filter clients by industry type.
    """
    try:
        industries = await konaos_client.get_client_industries_types()
        return [ClientIndustryType(**industry) for industry in industries]
    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=e.response.status_code,
            detail=f"KonaOS API error: {e.response.text}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error fetching client industries: {str(e)}"
        )


@router.get("/clients", response_model=ClientResponse)
async def get_clients(
    limit: int = Query(10, ge=1, le=100, description="Number of results per page"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
    sort_column: Optional[str] = Query(None, alias="sortColumn", description="Column to sort by"),
    sort_type: str = Query("asc", description="Sort direction (asc/desc)"),
    search_text: str = Query("", alias="searchText", description="Search text"),
    activated: bool = Query(True, description="Filter by activated status"),
    apply_activated_status: bool = Query(True, alias="applyActivatedStatus", description="Apply activated status filter"),
    industry_type_ids: Optional[list[str]] = Query(None, alias="industryTypeIds", description="Filter by industry type IDs. Use 'UNASSIGNED' to include clients without industry type."),
    api_key: str = Depends(verify_api_key)
):
    """
    Get a list of clients with optional filtering.
    
    Returns a paginated list of clients matching the specified criteria.
    
    Note: If industry_type_ids is not provided, the API may return empty results.
    Use /clients/industries to get valid industry type IDs.
    """
    try:
        # If no industry_type_ids provided, try to fetch all industry types and include "UNASSIGNED"
        if not industry_type_ids:
            try:
                industries = await konaos_client.get_client_industries_types()
                industry_type_ids = ["UNASSIGNED"]
                for industry in industries:
                    industry_id = industry.get('id')
                    if industry_id:
                        industry_type_ids.append(industry_id)
                print(f"[DEBUG] Auto-fetched {len(industry_type_ids)-1} industry type IDs (plus UNASSIGNED)")
            except Exception as e:
                print(f"[WARNING] Failed to auto-fetch industry types: {e}, using UNASSIGNED only")
                industry_type_ids = ["UNASSIGNED"]
        
        response_data = await konaos_client.get_clients_grid_data(
            limit=limit,
            offset=offset,
            sort_column=sort_column,
            sort_type=sort_type,
            search_text=search_text,
            activated=activated,
            apply_activated_status=apply_activated_status,
            industry_type_ids=industry_type_ids
        )
        
        # Remove unreliable totalCount
        response_data.pop('totalCount', None)
        response_data.pop('sortColumn', None)
        response_data.pop('sortType', None)
        
        # Filter client data to only include essential fields
        if 'data' in response_data and isinstance(response_data['data'], list):
            filtered_data = []
            for client_item in response_data['data']:
                filtered_client = {
                    'id': client_item.get('id'),
                    'code': client_item.get('code'),
                    'businessName': client_item.get('businessName'),
                    'clientName': client_item.get('clientName'),
                    'email': client_item.get('email'),
                    'phoneNum': client_item.get('phoneNum'),
                    'numCountryCode': client_item.get('numCountryCode'),
                    'city': client_item.get('city'),
                    'state': client_item.get('state'),
                    'address': client_item.get('address'),
                    'zipCode': client_item.get('zipCode'),
                    'county': client_item.get('county'),
                    'country': client_item.get('country'),
                    'clientIndustriesTypeId': client_item.get('clientIndustriesTypeId'),
                    'activated': client_item.get('activated'),
                    'paymentTerm': client_item.get('paymentTerm'),
                    'adminNotes': client_item.get('adminNotes'),
                }
                # Only include if we have at least id and businessName
                if filtered_client.get('id') and filtered_client.get('businessName'):
                    filtered_data.append(filtered_client)
            response_data['data'] = filtered_data
        
        return ClientResponse(**response_data)
    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=e.response.status_code,
            detail=f"KonaOS API error: {e.response.text}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error fetching clients: {str(e)}"
        )


@router.get("/clients/{client_id}", response_model=ClientDetails)
async def get_client(
    client_id: str,
    api_key: str = Depends(verify_api_key)
):
    """
    Get detailed information about a specific client.
    """
    try:
        response_data = await konaos_client.get_client_details(client_id)
        return ClientDetails(**response_data)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            raise HTTPException(status_code=404, detail="Client not found")
        raise HTTPException(
            status_code=e.response.status_code,
            detail=f"KonaOS API error: {e.response.text}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error fetching client details: {str(e)}"
        )


@router.post("/reports/sales-data", response_model=SalesDataResponse)
async def get_sales_data(
    request: SalesDataRequest,
    api_key: str = Depends(verify_api_key)
):
    """
    Get sales data report (giveback grid data).

    This endpoint proxies KonaOS:
    POST /api/v1/secure/reports/giveback/grid-data
    """
    try:
        payload = request.model_dump(by_alias=True)
        print(
            "[DEBUG] /reports/sales-data incoming payload: "
            f"searchText={payload.get('searchText')!r}, "
            f"fromDate={payload.get('fromDate')}, "
            f"toDate={payload.get('toDate')}, "
            f"sortColumn={payload.get('sortColumn')!r}, "
            f"sortType={payload.get('sortType')!r}, "
            f"brand={payload.get('brand')!r}, "
            f"limit={payload.get('limit')}, "
            f"offset={payload.get('offset')}"
        )
        brand = payload.pop("brand", "both")
        payload["brandIds"] = _resolve_report_brand_ids(brand)
        print(
            "[DEBUG] /reports/sales-data forwarding to KonaOS: "
            f"searchText={payload.get('searchText')!r}, "
            f"fromDate={payload.get('fromDate')}, "
            f"toDate={payload.get('toDate')}, "
            f"sortColumn={payload.get('sortColumn')!r}, "
            f"sortType={payload.get('sortType')!r}, "
            f"brandIds={payload.get('brandIds')}, "
            f"industryTypeIdList={payload.get('industryTypeIdList')}, "
            f"clientId={payload.get('clientId')!r}"
        )
        response_data = await konaos_client.get_sales_data_report(**payload)
        print(
            "[DEBUG] /reports/sales-data KonaOS response summary: "
            f"count={response_data.get('count')}, "
            f"searchText={response_data.get('searchText')!r}, "
            f"fromDate={response_data.get('fromDate')}, "
            f"toDate={response_data.get('toDate')}, "
            f"sortColumn={response_data.get('sortColumn')!r}, "
            f"sortType={response_data.get('sortType')!r}, "
            f"keys={response_data.get('keys')}, "
            f"data_len={len(response_data.get('data', [])) if isinstance(response_data.get('data'), list) else 'n/a'}"
        )

        # Defensive normalization: some KonaOS responses may return null for
        # sortColumn/sortType. Normalize those to empty strings so Pydantic
        # parsing never fails and callers always see a string value.
        if response_data.get("sortColumn") is None:
            response_data["sortColumn"] = ""
        if response_data.get("sortType") is None:
            response_data["sortType"] = ""

        return SalesDataResponse(**response_data)
    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=e.response.status_code,
            detail=f"KonaOS API error: {e.response.text}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error fetching sales data: {str(e)}"
        )


@router.post("/reports/client-ranking", response_model=ClientRankingResponse)
async def get_client_ranking(
    request: ClientRankingRequest,
    api_key: str = Depends(verify_api_key)
):
    """
    Get client ranking report (grid data).

    This endpoint proxies KonaOS:
    POST /api/v1/secure/reports/client-ranking/grid-data
    """
    try:
        payload = request.model_dump(by_alias=True)
        brand = payload.pop("brand", "both")
        payload["brandIds"] = _resolve_report_brand_ids(brand)
        response_data = await konaos_client.get_client_ranking_report(**payload)
        return ClientRankingResponse(**response_data)
    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=e.response.status_code,
            detail=f"KonaOS API error: {e.response.text}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error fetching client ranking: {str(e)}"
        )


@router.post("/invoices", response_model=EventOperationResponse)
async def create_invoice(
    body: Dict[str, Any] = Body(...),
    api_key: str = Depends(verify_api_key),
):
    """
    Create a client invoice (e.g. save as draft).

    Proxies KonaOS POST /api/v1/secure/invoice
    """
    try:
        response_data = await konaos_client.create_invoice(body)
        return EventOperationResponse(**response_data)
    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=e.response.status_code,
            detail=f"KonaOS API error: {e.response.text}",
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error creating invoice: {str(e)}",
        )


@router.put("/invoices", response_model=EventOperationResponse)
async def update_invoice(
    body: Dict[str, Any] = Body(...),
    api_key: str = Depends(verify_api_key),
):
    """
    Update a client invoice (e.g. submit with line items; include `id` from create).

    Proxies KonaOS PUT /api/v1/secure/invoice
    """
    try:
        response_data = await konaos_client.update_invoice(body)
        return EventOperationResponse(**response_data)
    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=e.response.status_code,
            detail=f"KonaOS API error: {e.response.text}",
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error updating invoice: {str(e)}",
        )


@router.put("/invoices/{invoice_id}/mark-paid", response_model=EventOperationResponse)
async def mark_invoice_paid(
    invoice_id: str,
    mark_paid: InvoiceMarkPaidRequest,
    is_mark_as_paid: bool = Query(
        True,
        alias="isMarkAsPaid",
        description="When true, marks invoice as paid (KonaOS default).",
    ),
    api_key: str = Depends(verify_api_key),
):
    """
    Update invoice status (e.g. mark as paid).

    Proxies KonaOS PUT /api/v1/secure/invoice/update-invoice-status/{id}
    """
    try:
        payload = mark_paid.model_dump(by_alias=True, exclude_none=True)
        response_data = await konaos_client.update_invoice_status(
            invoice_id,
            payload,
            is_mark_as_paid=is_mark_as_paid,
        )
        return EventOperationResponse(**response_data)
    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=e.response.status_code,
            detail=f"KonaOS API error: {e.response.text}",
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error updating invoice status: {str(e)}",
        )


@router.get("/invoices/grid/list")
async def list_invoices_grid(
    brand_ids: Optional[str] = Query(
        None,
        alias="brandIds",
        description="Comma-separated KonaOS brand IDs; defaults to KONAOS_BRAND_IDS / client cache when omitted",
    ),
    event_date: bool = Query(False, alias="eventDate"),
    from_date: Optional[int] = Query(None, alias="fromDate"),
    to_date: Optional[int] = Query(None, alias="toDate"),
    search_text: str = Query("", alias="searchText"),
    offset: int = Query(0, ge=0),
    limit: int = Query(10, ge=1, le=5000),
    sort_column: str = Query("", alias="sortColumn"),
    sort_type: str = Query("desc", alias="sortType"),
    api_key: str = Depends(verify_api_key),
):
    """
    List invoices (admin grid) with optional filters.

    Proxies KonaOS GET /api/v1/secure/invoice/grid/list
    """
    import time

    if from_date is None or from_date == 0:
        from_date = int((time.time() - (30 * 24 * 60 * 60)) * 1000)
    if to_date is None or to_date == 0:
        to_date = int(time.time() * 1000)

    resolved_brands = brand_ids
    if not resolved_brands or not resolved_brands.strip():
        if konaos_client.brand_ids:
            resolved_brands = ",".join(str(b) for b in konaos_client.brand_ids if b)
        else:
            raise HTTPException(
                status_code=400,
                detail="brandIds is required when KONAOS_BRAND_IDS / brand cache is not configured",
            )

    try:
        return await konaos_client.get_invoice_grid_list(
            brand_ids=resolved_brands.strip(),
            event_date=event_date,
            from_date=from_date,
            to_date=to_date,
            search_text=search_text or "",
            offset=offset,
            limit=limit,
            sort_column=sort_column or "",
            sort_type=sort_type or "desc",
        )
    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=e.response.status_code,
            detail=f"KonaOS API error: {e.response.text}",
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error fetching invoice list: {str(e)}",
        )


@router.get("/invoices/summary/{franchise_id}")
async def get_invoice_summary(
    franchise_id: str,
    from_date: int = Query(..., alias="fromDate", description="Start (epoch ms)"),
    to_date: int = Query(..., alias="toDate", description="End (epoch ms)"),
    brand_ids: str = Query(
        ...,
        alias="brandIds",
        description="Comma-separated KonaOS brand IDs",
    ),
    api_key: str = Depends(verify_api_key),
):
    """
    Invoice totals by status for a franchise and date range.

    Proxies KonaOS GET /api/v1/secure/invoice/get-summary/{franchiseId}
    """
    try:
        return await konaos_client.get_invoice_summary(
            franchise_id,
            from_date=from_date,
            to_date=to_date,
            brand_ids=brand_ids,
        )
    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=e.response.status_code,
            detail=f"KonaOS API error: {e.response.text}",
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error fetching invoice summary: {str(e)}",
        )


@router.post("/invoices/{invoice_id}/resend-receipt", response_model=EventOperationResponse)
async def resend_invoice_receipt(
    invoice_id: str,
    api_key: str = Depends(verify_api_key),
):
    """
    Resend the invoice receipt email.

    Proxies KonaOS POST /api/v1/secure/invoice/resend-receipt/{id}
    """
    try:
        response_data = await konaos_client.resend_invoice_receipt(invoice_id)
        return EventOperationResponse(**response_data)
    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=e.response.status_code,
            detail=f"KonaOS API error: {e.response.text}",
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error resending invoice receipt: {str(e)}",
        )


@router.delete("/invoices/{invoice_id}", response_model=EventOperationResponse)
async def delete_invoice(
    invoice_id: str,
    api_key: str = Depends(verify_api_key),
):
    """
    Delete a client invoice.

    Proxies KonaOS PUT /api/v1/secure/invoice/delete/{id}
    """
    try:
        response_data = await konaos_client.delete_invoice(invoice_id)
        return EventOperationResponse(**response_data)
    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=e.response.status_code,
            detail=f"KonaOS API error: {e.response.text}",
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error deleting invoice: {str(e)}",
        )


# Entry point is in ../run.py


"""KonaOS API client with session management."""
import os
import time
import json
import hmac
import hashlib
import base64
from typing import Optional
from urllib.parse import urlparse
import httpx
from dotenv import load_dotenv

load_dotenv()

# Cache files live in KONAOS_CACHE_DIR (a shared Docker volume in production)
# or default to the backend root (three levels up from app/konaos/).
_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_CACHE_DIR = os.getenv("KONAOS_CACHE_DIR", _BACKEND_ROOT)
os.makedirs(_CACHE_DIR, exist_ok=True)
BRAND_IDS_CACHE_FILE = os.path.join(_CACHE_DIR, ".brand_ids_cache.json")
# Persisted session key: survives restarts and is shared between the API
# process and the Celery worker (each reloads it from here on a 401).
SESSION_CACHE_FILE = os.path.join(_CACHE_DIR, ".konaos_session_cache.json")

KONAOS_API_BASE_URL = os.getenv("KONAOS_API_BASE_URL", "https://api.konaos.com")
KONAOS_EMAIL = os.getenv("KONAOS_EMAIL")
KONAOS_PASSWORD = os.getenv("KONAOS_PASSWORD")
DEFAULT_PROD_HMAC_SECRET = "5sdfWERGA3115REWQRasdf156afRWRafa15AF6gdsg65"
STAFF_ROLE_IDS = {
    "driver": "driver24f7042fb1fb5f7d3ef4479e",
    "manager": "manager24f7042fb1fb5f7d3ef4479e",
    "server": "server24f7042fb1fb5f7d3ef4479e",
    "worker": "worker24f7042fb1fb5f7d3ef4479e",
}

# Session TTL: 30 minutes (in seconds)
SESSION_TTL = 30 * 60


def _js_safe(value):
    """Recursively replace NaN/±Infinity with None, like JSON.stringify does.

    Python's json.dumps emits literal ``NaN`` — INVALID JSON that KonaOS
    rejects with main.invalidJsonError. These sneak in when an event fetched
    from KonaOS carries NaN numerics (json.loads happily parses them) and the
    read-modify-write update PUTs the whole structure back.
    """
    if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
        return None
    if isinstance(value, dict):
        return {k: _js_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_js_safe(v) for v in value]
    return value


class KonaosClient:
    """Client for interacting with the KonaOS API with automatic session management."""
    
    def __init__(self):
        self.base_url = KONAOS_API_BASE_URL
        self.email = KONAOS_EMAIL
        self.password = KONAOS_PASSWORD

        # Session key resolution order:
        #   1. persisted cache file (most recent known-good key)
        #   2. KONAOS_SESSION_KEY env var (initial seed)
        # A 401 triggers refresh via _login() (cache re-read, then real login).
        self.session_obtained_at: Optional[float] = None
        self.session_key: Optional[str] = self._load_session_from_cache()
        if not self.session_key:
            env_key = os.getenv("KONAOS_SESSION_KEY")
            if env_key:
                self.set_session_key(env_key)

        # Externally-provided session keys live ~15-30 days; staleness is
        # detected via 401 responses, not a local timer.
        self.session_expires_at: Optional[float] = (
            time.time() + SESSION_TTL if self.session_key else None
        )

        self.brand_id: Optional[str] = None
        self.franchise_id: Optional[str] = None
        
        # Check for brand IDs from environment
        brand_ids_env = os.getenv("KONAOS_BRAND_IDS")
        if brand_ids_env:
            self.brand_ids = brand_ids_env.split(",")
        else:
            self.brand_ids = None  # Will be populated from franchise-brands
            
        self.client = httpx.AsyncClient(timeout=5.0)  # 5 second timeout max
        # Try to load brandIds from cache immediately
        self._load_brand_ids_from_cache()
    
    # ── session persistence ──────────────────────────────────────────────

    def _load_session_from_cache(self) -> Optional[str]:
        """Read the persisted session key (returns None if absent/invalid)."""
        try:
            with open(SESSION_CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            key = data.get("session_key")
            if key:
                self.session_obtained_at = data.get("obtained_at")
                return key
        except (OSError, ValueError, json.JSONDecodeError):
            pass
        return None

    def set_session_key(self, key: str) -> None:
        """Adopt a session key (manual paste or fresh login) and persist it."""
        self.session_key = key
        self.session_obtained_at = time.time()
        self.session_expires_at = time.time() + SESSION_TTL
        try:
            with open(SESSION_CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(
                    {"session_key": key, "obtained_at": self.session_obtained_at}, f
                )
        except OSError as e:
            print(f"[WARNING] Could not persist session key: {e}")

    def session_age_days(self) -> Optional[float]:
        if not self.session_obtained_at:
            return None
        return (time.time() - self.session_obtained_at) / 86400.0

    async def probe_session(self) -> bool:
        """Cheap authenticated call to check whether the session is alive."""
        if not self.session_key:
            return False
        try:
            resp = await self._make_request("GET", "/api/v1/metadata")
            return resp.status_code == 200
        except Exception:
            return False

    async def _ensure_session(self) -> None:
        """Ensure we have a session key; a stale one is refreshed on 401."""
        if self.session_key is None:
            await self._login()
    
    def _encrypt_password(self, password: str) -> str:
        """
        Encrypt password using AES encryption (same as frontend).
        
        Frontend code:
        - Uses CryptoJS AES encryption
        - ECB mode, PKCS7 padding
        - Key from LOGIN_SECRET (base64 encoded)
        - Result is base64 string
        """
        import base64
        from Crypto.Cipher import AES
        from Crypto.Util.Padding import pad
        
        # Check if password is already encrypted (base64 format)
        import re
        if re.match(r'^[A-Za-z0-9+/]+=*$', password) and len(password) > 20:
            # Try to decode - if it's valid base64 and looks encrypted, return as-is
            try:
                decoded = base64.b64decode(password)
                # If decoded length is a multiple of 16 (AES block size), likely encrypted
                if len(decoded) % 16 == 0 and len(decoded) >= 16:
                    return password
            except:
                pass
        
        # Get LOGIN_SECRET from environment or use default
        # You need to find this in the frontend JavaScript code
        login_secret_b64 = os.getenv("KONAOS_LOGIN_SECRET")
        
        if not login_secret_b64:
            # If no secret, check if password looks already encrypted
            if re.match(r'^[A-Za-z0-9+/]+=*$', password) and len(password) >= 16:
                return password
            raise ValueError(
                "KONAOS_LOGIN_SECRET not set. "
                "Find LOGIN_SECRET in the frontend JavaScript code and set it in .env\n"
                "Or use the encrypted password directly from browser devtools."
            )
        
        # Decode the base64 key
        try:
            key = base64.b64decode(login_secret_b64)
        except Exception as e:
            raise ValueError(f"Invalid KONAOS_LOGIN_SECRET (not valid base64): {e}")
        
        # Encrypt using AES-ECB-PKCS7
        cipher = AES.new(key, AES.MODE_ECB)
        padded_password = pad(password.encode('utf-8'), AES.block_size)
        encrypted = cipher.encrypt(padded_password)
        
        # Return base64 encoded result
        return base64.b64encode(encrypted).decode('utf-8')
    
    async def _login(self) -> None:
        """Refresh the session: newer persisted key first, then real login.

        1. If another process (worker / manual POST /api/konaos/session) has
           persisted a NEWER key than the one we hold, adopt it and return —
           the caller's 401-retry will validate it.
        2. Otherwise perform email/password login; persist the fresh key.
        """
        cached = self._load_session_from_cache()
        if cached and cached != self.session_key:
            print("[INFO] Adopting refreshed session key from cache file")
            self.session_key = cached
            self.session_expires_at = time.time() + SESSION_TTL
            return

        # Real email/password authentication
        if not self.email or not self.password:
            raise ValueError(
                "Session key is stale and KONAOS_EMAIL/KONAOS_PASSWORD are not set. "
                "Paste a fresh session key via POST /api/konaos/session "
                "(see the API Explorer tab)."
            )
        
        login_url = f"{self.base_url}/api/v1/sessions"
        
        # Match the exact format from the browser request
        # Device fields are empty strings, password is encrypted
        encrypted_password = self._encrypt_password(self.password)
        
        login_data = {
            "email": self.email,
            "password": encrypted_password,
            "deviceId": "",
            "deviceType": "",
            "deviceModel": "",
            "os": "",
            "osVersion": "",
            "appVersion": "",
            "deviceName": "",
            "termsConditionAccepted": False,
        }
        
        # Add headers to match browser request
        headers = {
            "accept": "application/json",
            "content-type": "application/json",
            "timezone": "US/Eastern",  # Match browser header
            "origin": "https://admin.konaos.com",
            "referer": "https://admin.konaos.com/",
        }
        
        response = await self.client.post(login_url, json=login_data, headers=headers)
        
        if response.status_code != 200:
            error_text = response.text
            try:
                error_json = response.json()
                error_text = str(error_json)
            except:
                pass
            raise ValueError(f"Login failed with status {response.status_code}: {error_text}")
        
        data = response.json()
        fresh_key = data.get("sessionKey")
        if not fresh_key:
            raise ValueError("Failed to get sessionKey from login response")
        self.set_session_key(fresh_key)  # adopt + persist for other processes
        
        # Store brandId and franchiseId from login response
        self.brand_id = data.get("brandId")
        self.franchise_id = data.get("franchiseId")
        
        # Load brandIds from metadata endpoint (needed for events API)
        try:
            await self._load_brand_ids()
        except Exception as e:
            # If we can't load brandIds, that's okay - we'll use what's provided
            print(f"[WARNING] Could not load brand IDs: {e}")
        
        # Set expiration time (30 minutes from now)
        self.session_expires_at = time.time() + SESSION_TTL
    
    def _load_brand_ids_from_cache(self) -> bool:
        """Load brandIds from cache file. Returns True if loaded successfully."""
        try:
            if os.path.exists(BRAND_IDS_CACHE_FILE):
                with open(BRAND_IDS_CACHE_FILE, 'r') as f:
                    data = json.load(f)
                    self.brand_ids = data.get("brandIds", [])
                    return len(self.brand_ids) > 0
        except Exception:
            pass
        return False
    
    def _save_brand_ids_to_cache(self) -> None:
        """Save brandIds to cache file."""
        try:
            with open(BRAND_IDS_CACHE_FILE, 'w') as f:
                json.dump({"brandIds": self.brand_ids or []}, f)
        except Exception:
            pass
    
    async def _load_brand_ids(self) -> None:
        """Load brandIds from cache file, or fetch from API if cache doesn't exist."""
        # Try cache first
        if self._load_brand_ids_from_cache():
            return
        
        # Cache miss - fetch from API
        response = await self._make_request(
            "GET",
            "/api/v1/metadata"
        )
        response.raise_for_status()
        metadata = response.json()
        
        # Extract brandIds from brandList
        brand_list = metadata.get("brandList", [])
        if isinstance(brand_list, list):
            self.brand_ids = [brand.get("brandId") for brand in brand_list if brand.get("brandId")]
            # Save to cache
            self._save_brand_ids_to_cache()

    def _get_hmac_secret(self) -> str:
        """
        Resolve KonaOS HMAC secret used for X-Auth generation.

        Priority:
        1) KONAOS_HMAC_SECRET
        2) KONAOS_HMAC_PART1 + KONAOS_HMAC_PART2 + KONAOS_HMAC_PART3
        3) DEFAULT_PROD_HMAC_SECRET
        """
        direct = os.getenv("KONAOS_HMAC_SECRET", "").strip()
        if direct:
            return direct

        part1 = os.getenv("KONAOS_HMAC_PART1", "").strip()
        part2 = os.getenv("KONAOS_HMAC_PART2", "").strip()
        part3 = os.getenv("KONAOS_HMAC_PART3", "").strip()
        if part1 or part2 or part3:
            return f"{part1}{part2}{part3}"

        return DEFAULT_PROD_HMAC_SECRET

    def _to_js_json_string(self, payload: object) -> str:
        """Serialize request body close to frontend JSON.stringify behavior."""
        if payload is None:
            return ""
        if isinstance(payload, str):
            return payload
        # Compact JSON output; same string is used for hash and outgoing bytes.
        return json.dumps(_js_safe(payload), separators=(",", ":"), ensure_ascii=False)

    def _build_body_hash(self, method: str, body_string: str) -> str:
        """Build Base64(SHA256(body)) for non-empty request bodies."""
        if method.upper() == "GET" or not body_string:
            return ""
        digest = hashlib.sha256(body_string.encode("utf-8")).digest()
        return base64.b64encode(digest).decode("utf-8")

    def _generate_x_auth(self, method: str, url: str, body_string: str, timestamp_ms: str) -> str:
        """
        Generate X-Auth like frontend interceptor:
        WEB;timestamp;METHOD;pathLower;bodyHash
        """
        path_lower = urlparse(url).path.lower()
        body_hash = self._build_body_hash(method, body_string)
        payload = f"WEB;{timestamp_ms};{method.upper()};{path_lower};{body_hash}"
        secret = self._get_hmac_secret()
        signature = hmac.new(
            secret.encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        return f"HMAC-SHA256 Signature={base64.b64encode(signature).decode('utf-8')}"
    
    def _get_session_cookie(self) -> dict:
        """Get the session cookie for authenticated requests."""
        if not self.session_key:
            raise ValueError("No session key available")
        
        # Match browser cookie naming used by KonaOS.
        # Also include the GA cookies that were present in the successful request
        return {
            "jsessionId": self.session_key,
            "_ga": "GA1.1.1728304975.1768553510",
            "_ga_512BY0KKR5": "GS2.1.s1773643162$o68$g0$t1773643163$j59$l0$h0"
        }
    
    async def _make_request(
        self,
        method: str,
        endpoint: str,
        **kwargs
    ) -> httpx.Response:
        """Make an authenticated request to the KonaOS API."""
        await self._ensure_session()
        
        url = f"{self.base_url}{endpoint}"
        cookies = self._get_session_cookie()
        
        # Build body string once and send exact same bytes used for signature.
        json_payload = kwargs.pop("json", None)
        body_string = ""
        if json_payload is not None:
            body_string = self._to_js_json_string(json_payload)
            kwargs["content"] = body_string.encode("utf-8")
        elif "content" in kwargs:
            content_val = kwargs.get("content")
            if isinstance(content_val, bytes):
                body_string = content_val.decode("utf-8")
            elif isinstance(content_val, str):
                body_string = content_val

        timestamp_ms = str(int(time.time() * 1000))
        x_auth = self._generate_x_auth(method, url, body_string, timestamp_ms)

        # Add browser-like headers (required by secure KonaOS endpoints)
        headers = kwargs.pop("headers", {})
        headers.setdefault("accept", "application/json")
        headers.setdefault("accept-language", "en-US")
        headers.setdefault("content-type", "application/json")
        headers.setdefault("origin", "https://admin.konaos.com")
        headers.setdefault("referer", "https://admin.konaos.com/")
        headers.setdefault("timezone", "US/Eastern")
        headers["X-Client-Type"] = "WEB"
        headers["X-Authorization-Timestamp"] = timestamp_ms
        headers["X-Auth"] = x_auth
        
        response = await self.client.request(
            method=method,
            url=url,
            cookies=cookies,
            headers=headers,
            **kwargs
        )
        
        # If we get a 401, try re-authenticating once
        if response.status_code == 401:
            await self._login()
            cookies = self._get_session_cookie()
            retry_timestamp_ms = str(int(time.time() * 1000))
            headers["X-Authorization-Timestamp"] = retry_timestamp_ms
            headers["X-Auth"] = self._generate_x_auth(method, url, body_string, retry_timestamp_ms)
            response = await self.client.request(
                method=method,
                url=url,
                cookies=cookies,
                headers=headers,
                **kwargs
            )
        
        return response
    
    async def get_events_monthly(self, **params) -> dict:
        """Get events for a monthly view."""
        # Always use grid-data endpoint with GET for more reliable results
        # This matches how the browser makes requests
        print(f"[DEBUG] Using events grid-data endpoint")
        
        # Default brandIds to the ones from environment if available
        if 'brandIds' not in params and self.brand_ids:
            params['brandIds'] = self.brand_ids
        
        # Convert params to query string format, handling empty strings and None
        str_params = {}
        for key, value in params.items():
            if value is None:
                str_params[key] = ''  # Empty string for None values
            elif isinstance(value, list):
                if value:
                    str_params[key] = ','.join(str(v) for v in value)
                else:
                    str_params[key] = ''  # Empty string for empty lists
            elif isinstance(value, bool):
                str_params[key] = str(value).lower()
            elif value == '':
                str_params[key] = ''  # Preserve empty strings
            else:
                str_params[key] = str(value)
        
        # Add default parameters that are required by the API
        if 'limit' not in str_params:
            str_params['limit'] = '100'
        if 'offset' not in str_params:
            str_params['offset'] = '0'
        
        # Add fromDate and toDate if not provided
        import time
        if 'fromDate' not in str_params or not str_params['fromDate']:
            # Default to 30 days ago
            str_params['fromDate'] = str(int((time.time() - (30 * 24 * 60 * 60)) * 1000))
        if 'toDate' not in str_params or not str_params['toDate']:
            # Default to now
            str_params['toDate'] = str(int(time.time() * 1000))
            
        print(f"[DEBUG] Events request params: {str_params}")
        
        response = await self._make_request(
            "GET",
            "/api/v1/secure/events/grid-data",
            params=str_params
        )
        
        if response.status_code != 200:
            error_text = response.text[:500] if hasattr(response, 'text') else str(response)
            raise httpx.HTTPStatusError(
                f"KonaOS API error {response.status_code}: {error_text}",
                request=response.request,
                response=response
            )
        response.raise_for_status()
        return response.json()
    
    async def get_event_details(self, event_id: str, deleted: Optional[str] = None) -> dict:
        """Get detailed information about a specific event."""
        params = {}
        if deleted is not None:
            params["deleted"] = deleted
        
        response = await self._make_request(
            "GET",
            f"/api/v1/secure/events/details-minimal/{event_id}",
            params=params
        )
        response.raise_for_status()
        return response.json()
    
    async def get_staff_grid_data(self, brand_ids: Optional[list] = None, **params) -> dict:
        """Get staff grid data."""
        # Handle brandIds parameter - API expects "ALL" as string or comma-separated brandIds
        if brand_ids is None:
            # Default to "ALL" to get all staff across all brands (don't need to load brandIds)
            params["brandIds"] = "ALL"
        elif isinstance(brand_ids, list):
            if len(brand_ids) == 0 or "ALL" in brand_ids:
                params["brandIds"] = "ALL"
            else:
                # Join multiple brandIds with commas
                params["brandIds"] = ",".join(str(bid) for bid in brand_ids if bid)
        elif isinstance(brand_ids, str) and brand_ids.upper() == "ALL":
            params["brandIds"] = "ALL"
        else:
            # Single value
            params["brandIds"] = str(brand_ids)
        
        # Convert all params to strings (API expects query params as strings)
        # Keep empty strings - the API requires them (e.g. sortColumn=&sortType=&searchText=)
        str_params = {}
        for k, v in params.items():
            if v is None:
                continue
            str_params[k] = str(v)
        
        # Debug logging
        import sys
        print(f"[DEBUG] get_staff_grid_data params: {str_params}", flush=True)
        sys.stdout.flush()
        
        response = await self._make_request(
            "GET",
            "/api/v1/secure/staffs/grid-data",
            params=str_params
        )
        
        if response.status_code != 200:
            error_text = response.text[:1000] if hasattr(response, 'text') else str(response)
            print(f"[ERROR] get_staff_grid_data failed {response.status_code}: {error_text}", flush=True)
            print(f"[ERROR] Request URL: {response.request.url}", flush=True)
            sys.stdout.flush()
        
        response.raise_for_status()
        return response.json()
    
    async def get_franchise_timezone(self) -> dict:
        """Get franchise timezone - known working endpoint to test auth."""
        import sys
        
        # First try the known-working endpoint to test auth
        try:
            print("[DEBUG] Testing auth with franchise-timezone endpoint", flush=True)
            
            # Generate auth headers
            import time
            import hmac
            import hashlib
            import base64
            
            # Generate timestamp
            timestamp = str(int(time.time() * 1000))
            
            # Generate x-auth HMAC signature
            signature_key = self.session_key or "default_key"
            message = f"{timestamp}/api/v1/secure/franchise-timezone"
            signature = hmac.new(
                signature_key.encode(),
                message.encode(),
                hashlib.sha256
            ).digest()
            auth_header = f"HMAC-SHA256 Signature={base64.b64encode(signature).decode()}"
            
            custom_headers = {
                "timezone": "US/Eastern",
                "x-client-type": "WEB",
                "x-authorization-timestamp": timestamp,
                "x-auth": auth_header
            }
            
            response = await self._make_request(
                "GET",
                "/api/v1/secure/franchise-timezone",
                headers=custom_headers
            )
            
            if response.status_code == 200:
                print("[DEBUG] ✅ Franchise timezone auth SUCCESS", flush=True)
                return response.json()
            else:
                print(f"[DEBUG] ❌ Franchise timezone auth FAILED: {response.status_code}", flush=True)
                return {}
        except Exception as e:
            print(f"[DEBUG] ❌ Franchise timezone error: {str(e)}", flush=True)
            return {}
    
    async def get_staff_availability(self, **params) -> dict:
        """Get staff schedules monthly data from KonaOS."""
        payload = {
            "limit": params.get("limit", 2000),
            "offset": params.get("offset", 0),
            "sortColumn": params.get("sortColumn"),
            "sortType": params.get("sortType"),
            "searchText": params.get("searchText", ""),
            "toDate": params.get("toDate"),
            "fromDate": params.get("fromDate"),
            "activated": params.get("activated", True),
            "applyActivatedStatus": params.get("applyActivatedStatus", True),
            "assetIds": params.get("assetIds") or [],
            "userIds": params.get("userIds") or [],
            "brandIds": params.get("brandIds") or [],
        }

        response = await self._make_request(
            "POST",
            "/api/v1/secure/staffs/schedules-monthly",
            json=payload
        )
        response.raise_for_status()
        return response.json()

    async def get_staff_schedule_users_list(
        self,
        from_date: int,
        to_date: Optional[int] = None,
        brand_ids: Optional[list[str]] = None,
        event_id: Optional[str] = None,
        activated: bool = True,
        apply_activated_status: bool = True,
        limit: int = 199,
    ) -> dict:
        """
        Staff users with nested availability windows (admin GET users-list).

        Matches KonaOS GET /api/v1/secure/staffs-schedule/users-list query parameters.
        """
        brands: Optional[list[str]] = brand_ids
        if brands is None and self.brand_ids:
            brands = list(self.brand_ids)

        brand_ids_str = ""
        if brands:
            brand_ids_str = ",".join(str(bid) for bid in brands if bid)

        str_params = {
            "fromDate": str(from_date),
            "toDate": "" if to_date is None else str(to_date),
            "brandIds": brand_ids_str,
            "eventId": event_id or "",
            "activated": str(activated).lower(),
            "applyActivatedStatus": str(apply_activated_status).lower(),
            "limit": str(limit),
        }

        response = await self._make_request(
            "GET",
            "/api/v1/secure/staffs-schedule/users-list",
            params=str_params,
        )
        response.raise_for_status()
        return response.json()

    async def get_client_industries_types(self) -> list:
        """Get list of client industry types."""
        response = await self._make_request(
            "GET",
            "/api/v1/secure/client-industries-types"
        )
        response.raise_for_status()
        return response.json()
    
    async def get_clients_grid_data(
        self,
        limit: int = 10,
        offset: int = 0,
        sort_column: Optional[str] = None,
        sort_type: str = "asc",
        search_text: str = "",
        activated: bool = True,
        apply_activated_status: bool = True,
        industry_type_ids: Optional[list[str]] = None
    ) -> dict:
        """
        Get clients grid data.
        
        Args:
            limit: Number of results per page (default: 10)
            offset: Offset for pagination (default: 0)
            sort_column: Column to sort by (optional)
            sort_type: Sort direction - "asc" or "desc" (default: "asc")
            search_text: Search text filter (default: "")
            activated: Filter by activated status (default: True)
            apply_activated_status: Apply activated status filter (default: True)
            industry_type_ids: List of industry type IDs to filter by (optional)
        
        Returns:
            dict with keys: sortColumn, count, limit, sortType, data, offset, searchText
        """
        # Build query parameters - API expects all as strings
        params = {
            "limit": str(limit),
            "offset": str(offset),
            "sortColumn": sort_column if sort_column is not None else "",
            "sortType": sort_type,
            "searchText": search_text or "",
            "activated": str(activated).lower(),
            "applyActivatedStatus": str(apply_activated_status).lower(),
        }
        
        # Handle industryTypeIds - API expects comma-separated string or empty string
        if industry_type_ids:
            if isinstance(industry_type_ids, list):
                # Join with commas, including "UNASSIGNED" if needed
                industry_ids_str = ",".join(str(iid) for iid in industry_type_ids if iid)
                params["industryTypeIds"] = industry_ids_str
            else:
                params["industryTypeIds"] = str(industry_type_ids)
        else:
            params["industryTypeIds"] = ""
        
        response = await self._make_request(
            "GET",
            "/api/v1/secure/clients/grid-data",
            params=params
        )
        
        if response.status_code != 200:
            error_text = response.text[:500] if hasattr(response, 'text') else str(response)
            raise httpx.HTTPStatusError(
                f"KonaOS API error {response.status_code}: {error_text}",
                request=response.request,
                response=response
            )
        response.raise_for_status()
        return response.json()

    async def get_client_details(self, client_id: str) -> dict:
        """Get detailed information about a specific client."""
        response = await self._make_request(
            "GET",
            f"/api/v1/secure/clients/details/{client_id}"
        )
        if response.status_code != 200:
            error_text = response.text[:500] if hasattr(response, "text") else str(response)
            raise httpx.HTTPStatusError(
                f"KonaOS API error {response.status_code}: {error_text}",
                request=response.request,
                response=response
            )
        response.raise_for_status()
        return response.json()
    
    async def get_aging_report(
        self,
        past_days: int = 30,
        future_days: int = 30,
        from_sales: Optional[float] = None,
        to_sales: Optional[float] = None,
        search_text: str = "",
        offset: int = 0,
        limit: int = 10,
        sort_column: Optional[str] = None,
        sort_type: str = "asc",
        franchise_id: Optional[str] = None,
        brand_id: Optional[str] = None
    ) -> dict:
        """
        Get aging report data for clients.
        
        Args:
            past_days: Number of days in the past to check for events (default: 30)
            future_days: Number of days in the future to check for events (default: 30)
            from_sales: Minimum event sales amount (optional)
            to_sales: Maximum event sales amount (optional)
            search_text: Search text filter (default: "")
            offset: Pagination offset (default: 0)
            limit: Number of results per page (default: 10)
            sort_column: Column to sort by (optional)
            sort_type: Sort direction - "asc" or "desc" (default: "asc")
            franchise_id: Franchise ID (uses self.franchise_id if not provided)
            brand_id: Brand ID (uses self.brand_id if not provided)
        
        Returns:
            dict with keys: sortColumn, count, totalCount, limit, sortType, data, offset, toDate, searchText, fromDate
        """
        # Use instance values if not provided
        if franchise_id is None:
            if not self.franchise_id:
                await self._ensure_session()  # This will set franchise_id
            franchise_id = self.franchise_id
        
        if brand_id is None:
            if not self.brand_id:
                await self._ensure_session()  # This will set brand_id
            brand_id = self.brand_id
        
        if not franchise_id or not brand_id:
            raise ValueError("franchise_id and brand_id are required")
        
        # Build query parameters
        params = {
            "franchiseId": franchise_id,
            "brandId": brand_id,
            "pastDays": str(past_days),
            "futureDays": str(future_days),
            "searchText": search_text,
            "offset": str(offset),
            "limit": str(limit),
            "sortType": sort_type,
        }
        
        # Add optional parameters
        if from_sales is not None:
            params["fromSales"] = str(from_sales)
        if to_sales is not None:
            params["toSales"] = str(to_sales)
        if sort_column:
            params["sortColumn"] = sort_column
        else:
            params["sortColumn"] = ""  # Empty string if not provided
        
        response = await self._make_request(
            "GET",
            "/api/v1/secure/reports/client/aging-reports",
            params=params
        )
        
        if response.status_code != 200:
            error_text = response.text[:500] if hasattr(response, 'text') else str(response)
            raise httpx.HTTPStatusError(
                f"KonaOS API error {response.status_code}: {error_text}",
                request=response.request,
                response=response
            )
        response.raise_for_status()
        return response.json()

    async def get_sales_data_report(self, **payload) -> dict:
        """Get sales data report from giveback grid-data endpoint."""
        # Auto-fill brandIds when not provided, matching existing grid-data behavior.
        brand_ids = payload.get("brandIds")
        if (not brand_ids) and self.brand_ids:
            payload["brandIds"] = self.brand_ids

        print(
            "[DEBUG] KonaosClient.get_sales_data_report request payload: "
            f"{json.dumps(payload, ensure_ascii=False, sort_keys=True)}"
        )

        response = await self._make_request(
            "POST",
            "/api/v1/secure/reports/giveback/grid-data",
            json=payload
        )

        if response.status_code != 200:
            error_text = response.text[:500] if hasattr(response, "text") else str(response)
            raise httpx.HTTPStatusError(
                f"KonaOS API error {response.status_code}: {error_text}",
                request=response.request,
                response=response
            )
        response.raise_for_status()
        response_json = response.json()
        print(
            "[DEBUG] KonaosClient.get_sales_data_report response summary: "
            f"count={response_json.get('count')}, "
            f"searchText={response_json.get('searchText')!r}, "
            f"fromDate={response_json.get('fromDate')}, "
            f"toDate={response_json.get('toDate')}, "
            f"sortColumn={response_json.get('sortColumn')!r}, "
            f"sortType={response_json.get('sortType')!r}, "
            f"keys={response_json.get('keys')}, "
            f"data_len={len(response_json.get('data', [])) if isinstance(response_json.get('data'), list) else 'n/a'}"
        )
        return response_json

    async def get_client_ranking_report(self, **payload) -> dict:
        """Get client ranking report from client-ranking grid-data endpoint."""
        # Auto-fill brandIds when not provided, matching existing grid-data behavior.
        brand_ids = payload.get("brandIds")
        if (not brand_ids) and self.brand_ids:
            payload["brandIds"] = self.brand_ids

        response = await self._make_request(
            "POST",
            "/api/v1/secure/reports/client-ranking/grid-data",
            json=payload
        )

        if response.status_code != 200:
            error_text = response.text[:500] if hasattr(response, "text") else str(response)
            raise httpx.HTTPStatusError(
                f"KonaOS API error {response.status_code}: {error_text}",
                request=response.request,
                response=response
            )
        response.raise_for_status()
        return response.json()

    def _resolve_staff_role_id(self, staff_type: Optional[str], role_id: Optional[str]) -> str:
        """
        Resolve roleId for staff create request.

        Priority:
        1) Explicit role_id from request
        2) In-code mapping based on staff_type
        """
        if role_id:
            return role_id

        if not staff_type:
            raise ValueError(
                "Either roleId or staffType is required. "
                "Allowed staffType values: server, manager, worker, driver."
            )

        staff_type_key = staff_type.strip().lower()
        mapped_role_id = STAFF_ROLE_IDS.get(staff_type_key)
        if not mapped_role_id:
            raise ValueError(
                f"Unsupported staffType '{staff_type}'. "
                "Allowed values: server, manager, worker, driver."
            )
        return mapped_role_id

    async def create_staff(
        self,
        first_name: str,
        last_name: str,
        email: str,
        phone_num: str,
        num_country_code: str = "+1",
        staff_type: Optional[str] = None,
        role_id: Optional[str] = None,
        alternate_num_country_code: str = "+1",
        alternate_phone_num: str = "",
        emergency_contact_person_name: str = "",
        emergency_num_country_code: str = "+1",
        emergency_phone_num: str = "",
        hourly_rate: str = "0",
        address: str = "",
        city: str = "",
        state: str = "",
        country: str = "USA",
        zip_code: str = "",
        bio_image_file_id: str = "",
        bio: str = "",
        staff_brand_list: Optional[list[dict]] = None,
        access_group_permissions_input=None,
        access_permissions_updated: bool = False,
        **kwargs
    ) -> dict:
        """Create a new staff member."""
        await self._ensure_session()

        resolved_role_id = self._resolve_staff_role_id(staff_type=staff_type, role_id=role_id)

        if staff_brand_list:
            normalized_brand_list = [b for b in staff_brand_list if isinstance(b, dict) and b.get("brandId")]
        else:
            normalized_brand_list = []
            if self.brand_ids:
                normalized_brand_list = [{"brandId": brand_id} for brand_id in self.brand_ids if brand_id]
            elif self.brand_id:
                normalized_brand_list = [{"brandId": self.brand_id}]

        if not normalized_brand_list:
            raise ValueError(
                "No brandId available for staffBrandList. "
                "Provide staffBrandList in request or configure KONAOS_BRAND_IDS."
            )

        payload = {
            "firstName": first_name,
            "lastName": last_name,
            "email": email,
            "phoneNum": phone_num,
            "numCountryCode": num_country_code,
            "alternateNumCountryCode": alternate_num_country_code,
            "alternatePhoneNum": alternate_phone_num,
            "emergencyContactPersonName": emergency_contact_person_name,
            "emergencyNumCountryCode": emergency_num_country_code,
            "emergencyPhoneNum": emergency_phone_num,
            "hourlyRate": hourly_rate,
            "address": address,
            "city": city,
            "state": state,
            "country": country,
            "zipCode": zip_code,
            "roleId": resolved_role_id,
            "bioImageFileId": bio_image_file_id,
            "bio": bio,
            "staffBrandList": normalized_brand_list,
            "accessGroupPermissionsInput": access_group_permissions_input,
            "accessPermissionsUpdated": access_permissions_updated,
        }

        payload.update(kwargs)

        response = await self._make_request(
            "POST",
            "/api/v1/secure/staffs",
            json=payload
        )

        if response.status_code != 200:
            error_text = response.text[:500] if hasattr(response, "text") else str(response)
            raise httpx.HTTPStatusError(
                f"KonaOS API error {response.status_code}: {error_text}",
                request=response.request,
                response=response
            )
        response.raise_for_status()
        return response.json()
    
    async def create_event(
        self,
        name: str,
        start_date_time: int,
        end_date_time: int,
        business_name: str,
        address_line1: str,
        city: str,
        state: str,
        zip_code: str,
        contact_name: str,
        contact_email: str,
        brand_id: Optional[str] = None,
        client_id: Optional[str] = None,
        contact_title: str = "",
        contact_phone: str = "",
        contact_phone_country_code: str = "+1",
        county: str = "",
        country: str = "USA",
        admin_notes: str = "",
        notes: str = "",
        event_status: str = "hold",
        manual_status: str = "pending",
        payment_term: str = "menu",
        **kwargs
    ) -> dict:
        """
        Create a new event.
        
        Args:
            name: Event name (required)
            start_date_time: Event start date/time (Unix timestamp in milliseconds)
            end_date_time: Event end date/time (Unix timestamp in milliseconds)
            business_name: Business name (required)
            address_line1: Street address (required)
            city: City (required)
            state: State (required)
            zip_code: Zip code (required)
            contact_name: Contact name (required)
            contact_email: Contact email (required)
            brand_id: Brand ID (uses self.brand_id if not provided)
            client_id: Client ID (optional)
            contact_title: Contact title (optional)
            contact_phone: Contact phone number (optional)
            contact_phone_country_code: Phone country code (default: "+1")
            county: County (optional)
            country: Country (default: "USA")
            admin_notes: Admin notes (optional)
            notes: Event notes (optional, can be HTML)
            event_status: Event status (default: "hold")
            manual_status: Manual status (default: "pending")
            payment_term: Payment term (default: "menu")
            **kwargs: Additional event fields
        
        Returns:
            dict with success message
        """
        if brand_id is None:
            if not self.brand_id:
                await self._ensure_session()
            brand_id = self.brand_id
        
        if not brand_id:
            raise ValueError("brand_id is required")
        
        # Build event payload
        payload = {
            "eventCode": "",
            "brandId": brand_id,
            "name": name,
            "clientId": client_id,
            "addressLatitude": "",
            "addressLine1": address_line1,
            "addressLongitude": "",
            "adminNotes": admin_notes,
            "bannerText": "",
            "businessName": business_name,
            "city": city,
            "clientIndustriesTypeId": "",
            "contactEmail": contact_email,
            "contactName": contact_name,
            "contactPersonId": "",
            "contactPhoneNumCountryCode": contact_phone_country_code,
            "contactPhoneNumber": contact_phone,
            "contactTitle": contact_title,
            "country": country,
            "county": county,
            "days": "",
            "endDateTime": end_date_time,
            "eventAssetsList": [],
            "eventBannerFiles": [],
            "eventStatus": event_status,
            "eventTemplatesDtoList": [],
            "expiryDate": "",
            "flyerItems": "",
            "givebackPercentage": "0",
            "itemsDtoList": [],
            "key": "",
            "lastDayOfMonth": False,
            "manualStatus": manual_status,
            "maxAllowedOrders": "",
            "maxOrderInSlot": "",
            "monthlyDateTime": "",
            "newContact": True,
            "notes": notes if notes else "<p></p>",
            "orderAttribute": "",
            "paymentTerm": payment_term,
            "preOrder": "",
            "prePay": False,
            "recipientNameLabel": "",
            "recipientNameRequired": True,
            "recurringType": "DNR",
            "startDateTime": start_date_time,
            "state": state,
            "tags": [],
            "taxPercent": "0",
            "timeSlot": "",
            "useTimeSlot": False,
            "values": "",
            "zipCode": zip_code
        }
        
        # Merge caller-supplied extras, but ONLY keys that are real quick-add
        # fields (i.e. already present in the canonical payload above). KonaOS
        # rejects the entire body with "invalidJsonError" if it sees an unknown
        # property, so invented fields like kurbsideEvent / driverNotes must be
        # dropped here rather than forwarded verbatim. This mirrors how the CRM
        # proxy / n8n create path only ever sends known fields.
        allowed_keys = set(payload.keys())
        dropped = [k for k in kwargs if k not in allowed_keys]
        if dropped:
            print(f"[WARNING] create_event: dropping unknown quick-add fields: {dropped}")
        payload.update({k: v for k, v in kwargs.items() if k in allowed_keys})

        response = await self._make_request(
            "POST",
            "/api/v1/secure/events/quick-add",
            json=payload
        )
        
        if response.status_code != 200:
            error_text = response.text[:500] if hasattr(response, 'text') else str(response)
            raise httpx.HTTPStatusError(
                f"KonaOS API error {response.status_code}: {error_text}",
                request=response.request,
                response=response
            )
        response.raise_for_status()
        return response.json()
    
    async def update_event(
        self,
        event_id: str,
        name: Optional[str] = None,
        business_name: Optional[str] = None,
        admin_notes: Optional[str] = None,
        notes: Optional[str] = None,
        **kwargs
    ) -> dict:
        """
        Update an existing event.
        
        Args:
            event_id: Event ID to update (required)
            name: New event name (optional)
            business_name: New business name (optional)
            admin_notes: New admin notes (optional)
            notes: New event notes (optional, can be HTML)
            **kwargs: Additional fields to update
        
        Returns:
            dict with success message
        """
        # First, get the existing event to get full structure
        existing_event = await self.get_event_details(event_id)
        
        # Use the full event structure
        update_payload = existing_event.copy()
        update_payload["id"] = event_id
        
        # Update specified fields
        if name is not None:
            update_payload["name"] = name
        if business_name is not None:
            update_payload["businessName"] = business_name
        if admin_notes is not None:
            update_payload["adminNotes"] = admin_notes
        if notes is not None:
            update_payload["notes"] = notes
        
        # Update any additional fields from kwargs
        update_payload.update(kwargs)

        # KonaOS rejects the PUT with events.clientBusinessNameRequired when
        # businessName is empty — some events are stored without one. Fall
        # back to the event's name so a financial-fields update can't fail
        # on an unrelated required field.
        if not update_payload.get("businessName"):
            update_payload["businessName"] = update_payload.get("name") or "Kona Ice Event"

        # Ensure arrays are lists (not None)
        array_fields = ["eventTemplatesDtoList", "eventAssetsList", "eventStaffList", 
                       "itemsDtoList", "tags", "eventBannerFiles"]
        for field in array_fields:
            if update_payload.get(field) is None:
                update_payload[field] = []
        
        # Ensure string fields are strings (not None) - common required fields
        string_fields_defaults = {
            "clientId": "",
            "contactPersonId": "primary",
        }
        for field, default_value in string_fields_defaults.items():
            if update_payload.get(field) is None:
                update_payload[field] = default_value
        
        response = await self._make_request(
            "PUT",
            "/api/v1/secure/events",
            json=update_payload
        )
        
        if response.status_code != 200:
            error_text = response.text[:500] if hasattr(response, 'text') else str(response)
            raise httpx.HTTPStatusError(
                f"KonaOS API error {response.status_code}: {error_text}",
                request=response.request,
                response=response
            )
        response.raise_for_status()
        return response.json()
    
    async def delete_event(
        self,
        event_id: str,
        update_series: bool = False
    ) -> dict:
        """
        Delete (soft delete) an event.
        
        Args:
            event_id: Event ID to delete (required)
            update_series: Whether to update the entire series if this is a recurring event (default: False)
        
        Returns:
            dict with success message
        """
        delete_payload = {
            "softDeleted": True,
            "updateSeries": update_series
        }
        
        response = await self._make_request(
            "PUT",
            f"/api/v1/secure/events/{event_id}/delete-event",
            json=delete_payload
        )
        
        if response.status_code != 200:
            error_text = response.text[:500] if hasattr(response, 'text') else str(response)
            raise httpx.HTTPStatusError(
                f"KonaOS API error {response.status_code}: {error_text}",
                request=response.request,
                response=response
            )
        response.raise_for_status()
        return response.json()

    async def create_invoice(self, body: dict) -> dict:
        """
        Create a client invoice (e.g. save as draft).

        Proxies POST /api/v1/secure/invoice
        """
        response = await self._make_request(
            "POST",
            "/api/v1/secure/invoice",
            json=body,
        )
        if response.status_code != 200:
            error_text = response.text[:500] if hasattr(response, "text") else str(response)
            raise httpx.HTTPStatusError(
                f"KonaOS API error {response.status_code}: {error_text}",
                request=response.request,
                response=response,
            )
        response.raise_for_status()
        return response.json()

    async def update_invoice(self, body: dict) -> dict:
        """
        Update a client invoice (e.g. submit with line items).

        Proxies PUT /api/v1/secure/invoice
        """
        response = await self._make_request(
            "PUT",
            "/api/v1/secure/invoice",
            json=body,
        )
        if response.status_code != 200:
            error_text = response.text[:500] if hasattr(response, "text") else str(response)
            raise httpx.HTTPStatusError(
                f"KonaOS API error {response.status_code}: {error_text}",
                request=response.request,
                response=response,
            )
        response.raise_for_status()
        return response.json()

    async def update_invoice_status(
        self,
        invoice_id: str,
        body: dict,
        *,
        is_mark_as_paid: bool = True,
    ) -> dict:
        """
        Update invoice status (e.g. mark as paid).

        Proxies PUT /api/v1/secure/invoice/update-invoice-status/{id}
        """
        endpoint = f"/api/v1/secure/invoice/update-invoice-status/{invoice_id}"
        params = {"isMarkAsPaid": str(is_mark_as_paid).lower()}
        response = await self._make_request(
            "PUT",
            endpoint,
            params=params,
            json=body,
        )
        if response.status_code != 200:
            error_text = response.text[:500] if hasattr(response, "text") else str(response)
            raise httpx.HTTPStatusError(
                f"KonaOS API error {response.status_code}: {error_text}",
                request=response.request,
                response=response,
            )
        response.raise_for_status()
        return response.json()

    async def get_invoice_summary(
        self,
        franchise_id: str,
        *,
        from_date: int,
        to_date: int,
        brand_ids: str,
    ) -> dict:
        """
        Invoice totals by status for a franchise.

        Proxies GET /api/v1/secure/invoice/get-summary/{franchiseId}
        """
        params = {
            "fromDate": str(from_date),
            "toDate": str(to_date),
            "brandIds": brand_ids,
        }
        response = await self._make_request(
            "GET",
            f"/api/v1/secure/invoice/get-summary/{franchise_id}",
            params=params,
        )
        if response.status_code != 200:
            error_text = response.text[:500] if hasattr(response, "text") else str(response)
            raise httpx.HTTPStatusError(
                f"KonaOS API error {response.status_code}: {error_text}",
                request=response.request,
                response=response,
            )
        response.raise_for_status()
        return response.json()

    async def get_invoice_grid_list(
        self,
        *,
        brand_ids: str,
        event_date: bool = False,
        from_date: int,
        to_date: int,
        search_text: str = "",
        offset: int = 0,
        limit: int = 10,
        sort_column: str = "",
        sort_type: str = "desc",
    ) -> dict:
        """
        Paginated invoice list for the admin grid.

        Proxies GET /api/v1/secure/invoice/grid/list
        """
        params = {
            "brandIds": brand_ids,
            "eventDate": str(event_date).lower(),
            "fromDate": str(from_date),
            "toDate": str(to_date),
            "searchText": search_text or "",
            "offset": str(offset),
            "limit": str(limit),
            "sortColumn": sort_column or "",
            "sortType": sort_type or "desc",
        }
        response = await self._make_request(
            "GET",
            "/api/v1/secure/invoice/grid/list",
            params=params,
        )
        if response.status_code != 200:
            error_text = response.text[:500] if hasattr(response, "text") else str(response)
            raise httpx.HTTPStatusError(
                f"KonaOS API error {response.status_code}: {error_text}",
                request=response.request,
                response=response,
            )
        response.raise_for_status()
        return response.json()

    async def resend_invoice_receipt(self, invoice_id: str) -> dict:
        """
        Resend invoice receipt email.

        Proxies POST /api/v1/secure/invoice/resend-receipt/{id}
        """
        response = await self._make_request(
            "POST",
            f"/api/v1/secure/invoice/resend-receipt/{invoice_id}",
            content=b"",
            headers={"content-type": "text/plain"},
        )
        if response.status_code != 200:
            error_text = response.text[:500] if hasattr(response, "text") else str(response)
            raise httpx.HTTPStatusError(
                f"KonaOS API error {response.status_code}: {error_text}",
                request=response.request,
                response=response,
            )
        response.raise_for_status()
        return response.json()

    async def delete_invoice(self, invoice_id: str) -> dict:
        """
        Delete a client invoice.

        Proxies PUT /api/v1/secure/invoice/delete/{id}
        """
        response = await self._make_request(
            "PUT",
            f"/api/v1/secure/invoice/delete/{invoice_id}",
            content=b"",
            headers={"content-type": "text/plain"},
        )
        if response.status_code != 200:
            error_text = response.text[:500] if hasattr(response, "text") else str(response)
            raise httpx.HTTPStatusError(
                f"KonaOS API error {response.status_code}: {error_text}",
                request=response.request,
                response=response,
            )
        response.raise_for_status()
        return response.json()
    
    async def close(self):
        """Close the HTTP client."""
        await self.client.aclose()


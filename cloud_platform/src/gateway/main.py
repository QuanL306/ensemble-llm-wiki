#!/usr/bin/env python3
"""
API Gateway — Entry point for third-party access.
Handles routing, authentication, rate limiting, and logging.
"""

import hmac as _hmac
import json
import math
import os
import shutil
import sys
import time
import asyncio
import calendar
import unicodedata
from typing import List, Optional

from fastapi import FastAPI, Request, Response, Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
import uuid
import redis.asyncio as redis
import redis.exceptions as redis_exc
import httpx
import jwt
import bcrypt
from pydantic import BaseModel, EmailStr
from contextlib import asynccontextmanager


# Structured logging — log.py lives one level up from gateway/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from log import setup_logging, get_logger, redact_key

setup_logging("gateway")
log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

REDIS_URL    = os.getenv("REDIS_URL", "redis://localhost:6379")
MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", "http://localhost:8001")

# ── JWT ──────────────────────────────────────────────────────────────────────
JWT_SECRET    = os.getenv("JWT_SECRET", "")
JWT_ALGORITHM = "HS256"      # hard-coded — never from env (C4)
JWT_ISSUER    = "kb-gateway"
JWT_AUDIENCE  = "kb-api"

_KNOWN_BAD_SECRETS = {"your-secret-key", "changeme", "secret", "password", "jwt-secret"}
if not JWT_SECRET or JWT_SECRET in _KNOWN_BAD_SECRETS:
    raise RuntimeError(
        "JWT_SECRET env var is not set or uses an insecure default. "
        "Generate a strong value with: python3 -c \"import secrets; print(secrets.token_hex(32))\""
    )
_jwt_bytes = JWT_SECRET.encode()
if len(_jwt_bytes) < 32:                               # C3: minimum 32-byte secret
    raise RuntimeError("JWT_SECRET must be at least 32 bytes — increase its length")
_freq: dict = {}
for _b in _jwt_bytes:
    _freq[_b] = _freq.get(_b, 0) + 1
_entropy = -sum((c / len(_jwt_bytes)) * math.log2(c / len(_jwt_bytes)) for c in _freq.values())
if _entropy < 3.5:                                     # C3: reject low-entropy secrets
    raise RuntimeError(
        f"JWT_SECRET has low entropy ({_entropy:.2f} bits/byte) — use a random value, "
        "e.g. python3 -c \"import secrets; print(secrets.token_hex(32))\""
    )
del _jwt_bytes, _freq, _entropy  # don't keep raw bytes in module scope

# ── API-key hashing (C1) ─────────────────────────────────────────────────────
API_KEY_SALT = os.getenv("API_KEY_SALT", "")
if not API_KEY_SALT:
    raise RuntimeError(
        "API_KEY_SALT env var is not set — API keys cannot be stored securely without it. "
        "Generate with: python3 -c \"import secrets; print(secrets.token_hex(32))\""
    )

# ── Internal service secret ──────────────────────────────────────────────────
INTERNAL_SECRET = os.getenv("INTERNAL_SECRET", "")

# ── CORS ─────────────────────────────────────────────────────────────────────
_cors_env = os.getenv("CORS_ORIGINS", "")
if _cors_env.strip() == "*":
    log.critical(
        "CORS_ORIGINS is set to '*' (wildcard) — this is insecure and NOT allowed. "
        "Set CORS_ORIGINS to a comma-separated list of explicit origins instead. "
        "Treating as empty (all cross-origin requests blocked)."
    )
    _cors_env = ""
CORS_ORIGINS: list[str] = [o.strip() for o in _cors_env.split(",") if o.strip()]

# ── Rate-limiting knobs ───────────────────────────────────────────────────────
RATE_LIMIT_WINDOW      = 60    # seconds per window
RATE_LIMIT_DEFAULT     = 100   # requests per window (per API key)
REGISTER_RATE_LIMIT    = 5     # registrations per IP per minute
LOGIN_IP_RATE_LIMIT    = 10    # login attempts per IP per minute (H2)
LOGIN_EMAIL_RATE_LIMIT = 5     # login attempts per email per minute (H2)
REFRESH_RATE_LIMIT     = 10    # refresh calls per IP per minute (M1)
JWT_MEMBER_RATE_LIMIT  = 30    # member-management calls per minute per user
KEY_CREATE_RATE_LIMIT  = 5     # API key creations per user per hour (H8)
MAX_KEYS_PER_USER      = 10    # hard cap on API keys per user (H8)

# ── Session management ────────────────────────────────────────────────────────
SESSION_TTL = 7 * 24 * 3600  # 7 days — must match refresh token lifetime

# Dummy bcrypt hash used for constant-time login when user is not found (H3)
# Computed once at startup (~100 ms); bcrypt.checkpw against this always fails.
_DUMMY_HASH = bcrypt.hashpw(b"constant-time-placeholder", bcrypt.gensalt()).decode()

# ── Clients ───────────────────────────────────────────────────────────────────
redis_client: Optional[redis.Redis] = None
_http_client: Optional[httpx.AsyncClient] = None

from pathlib import Path as _Path
USAGE_FALLBACK_PATH = _Path(os.getenv("USAGE_FALLBACK_PATH", "/data/usage_fallback.log"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage Redis and HTTP client lifecycle."""
    global redis_client, _http_client
    redis_client = redis.from_url(REDIS_URL, decode_responses=True)
    _http_client = httpx.AsyncClient(timeout=30.0)
    yield
    if redis_client:
        await redis_client.close()
    if _http_client:
        await _http_client.aclose()


app = FastAPI(title="Knowledge Base MCP Gateway", version="1.0.0", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def _hash_api_key(raw_key: str) -> str:
    """Return HMAC-SHA256 hex digest of raw_key keyed by API_KEY_SALT.

    Raw API keys are never stored in Redis — only their HMAC digest (C1).
    """
    return _hmac.new(API_KEY_SALT.encode(), raw_key.encode(), "sha256").hexdigest()


def _normalize_email(email: str) -> str:
    """NFKC-casefold and strip whitespace — consistent regardless of Unicode form (M9)."""
    return unicodedata.normalize("NFKC", email).casefold().strip()


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

# Headers that must be set by the gateway — never accepted from inbound clients (H12)
_BLOCKED_INBOUND_HEADERS = frozenset({
    "x-internal-token",
    "x-user-id",
    "x-kb-owner-id",
    "x-api-key-id",
    "x-permissions",
})


class _RequestLogMiddleware(BaseHTTPMiddleware):
    """
    Strip spoofable internal headers, assign a request ID, time the response,
    and emit one structured log line per request.
    """

    async def dispatch(self, request: Request, call_next):
        # H12: Drop headers that must only be injected by the gateway itself
        if any(h.lower() in _BLOCKED_INBOUND_HEADERS for h in request.headers.keys()):
            from starlette.datastructures import MutableHeaders
            mh = MutableHeaders(scope=request.scope)
            for name in list(request.headers.keys()):
                if name.lower() in _BLOCKED_INBOUND_HEADERS:
                    del mh[name]

        req_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:8]
        request.state.req_id = req_id
        t0 = time.monotonic()

        try:
            response = await call_next(request)
            duration_ms = int((time.monotonic() - t0) * 1000)
            log.info(
                "request_complete",
                extra={
                    "req_id":      req_id,
                    "method":      request.method,
                    "path":        request.url.path,
                    "status":      response.status_code,
                    "duration_ms": duration_ms,
                },
            )
            response.headers["X-Request-ID"] = req_id
            return response

        except Exception as exc:
            duration_ms = int((time.monotonic() - t0) * 1000)
            log.error(
                "unhandled_error",
                extra={
                    "req_id":      req_id,
                    "method":      request.method,
                    "path":        request.url.path,
                    "duration_ms": duration_ms,
                    "error":       f"{type(exc).__name__}: {exc}",
                },
                exc_info=True,
            )
            raise


# Middleware (outermost added last — executes first in FastAPI)
app.add_middleware(_RequestLogMiddleware)
app.add_middleware(GZipMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["GET", "POST", "DELETE"],                   # explicit allowlist (H7/CORS)
    allow_headers=["Authorization", "Content-Type", "X-API-Key",
                   "X-KB-ID", "X-KB-Owner-ID", "X-Request-ID"],  # explicit allowlist
)


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = 3600

# "admin" removed from allowed permissions until a concrete privilege model is defined (H9)
_ALLOWED_PERMISSIONS = {"read", "write"}


class UserRegister(BaseModel):
    email: EmailStr
    password: str
    name: str = ""

class UserLogin(BaseModel):
    email: EmailStr
    password: str

class APIKeyCreate(BaseModel):
    name: str
    permissions: List[str] = ["read"]

class KBMemberAdd(BaseModel):
    email: EmailStr
    role: str = "read"   # "read" or "write"

class APIKeyResponse(BaseModel):
    id: str
    name: str
    key: str  # returned only once at creation time
    permissions: List[str]
    rate_limit: int
    quota_limit: int
    created_at: str


# ---------------------------------------------------------------------------
# Authentication helpers
# ---------------------------------------------------------------------------

security = HTTPBearer(auto_error=False)


async def get_api_key(request: Request) -> Optional[str]:
    """Extract API Key from X-API-Key header only.

    Authorization: Bearer is reserved exclusively for JWTs (H14/Medium).
    """
    return request.headers.get("X-API-Key")


async def verify_api_key(api_key: str, request: Optional[Request] = None) -> dict:
    """Verify API Key against Redis (hash-based lookup).

    The raw key is HMAC-hashed before lookup — plaintext never stored (C1).
    Fails closed (503) on Redis outage.
    """
    req_id = getattr(getattr(request, "state", None), "req_id", "-") if request else "-"

    if not api_key:
        log.warning("auth_fail", extra={"req_id": req_id, "error": "no_api_key"})
        raise HTTPException(status_code=401, detail="API Key required")

    key_hash = _hash_api_key(api_key)
    try:
        key_data = await redis_client.hgetall(f"api_key:{key_hash}")
    except (redis_exc.RedisError, Exception) as exc:
        log.error("redis_unavailable", extra={"req_id": req_id, "error": str(exc)})
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")

    if not key_data:
        log.warning(
            "auth_fail",
            extra={"req_id": req_id, "error": "invalid_key",
                   "key_prefix": redact_key(api_key)},
        )
        raise HTTPException(status_code=401, detail="Invalid API Key")

    if key_data.get("is_active") != "true":
        log.warning(
            "auth_fail",
            extra={"req_id": req_id, "error": "key_deactivated",
                   "key_id": key_data.get("key_id", "?")},
        )
        raise HTTPException(status_code=401, detail="API Key deactivated")

    expires_at = key_data.get("expires_at")
    if expires_at and float(expires_at) < time.time():
        log.warning(
            "auth_fail",
            extra={"req_id": req_id, "error": "key_expired",
                   "key_id": key_data.get("key_id", "?")},
        )
        raise HTTPException(status_code=401, detail="API Key expired")

    return {
        "user_id":     key_data.get("user_id", ""),
        "key_id":      key_data["key_id"],
        "permissions": key_data.get("permissions", "read").split(","),
        "rate_limit":  int(key_data.get("rate_limit", RATE_LIMIT_DEFAULT)),
        "quota_limit": int(key_data.get("quota_limit", 10000)),
    }


async def verify_jwt_token(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    """Verify JWT access token.

    Enforces (C4):
    - iss / aud claim validation
    - type == "access" claim (H1) — refresh/reset tokens rejected
    - per-jti revocation check — fails closed on Redis outage
    - auth_epoch check: rejects tokens issued before the user's revocation epoch (M2)
    """
    if not credentials:
        raise HTTPException(status_code=401, detail="Authorization required")

    try:
        payload = jwt.decode(
            credentials.credentials,
            JWT_SECRET,
            algorithms=["HS256"],       # literal, never from variable (C4)
            issuer=JWT_ISSUER,
            audience=JWT_AUDIENCE,
            options={"require": ["exp", "iat", "sub", "jti"]},
        )

        # H1: reject any non-access token type (refresh, reset, invite …)
        if payload.get("type") != "access":
            raise HTTPException(
                status_code=401,
                detail="Invalid token type — use the access token"
            )

        jti = payload.get("jti")
        sub = payload.get("sub")

        if redis_client:
            try:
                if await redis_client.exists(f"jwt_jti:{jti}"):
                    raise HTTPException(status_code=401, detail="Token has been revoked")
                # M2: reject tokens issued before the per-user revocation epoch
                if sub:
                    epoch_ts = await redis_client.get(f"auth_epoch:{sub}")
                    if epoch_ts and payload.get("iat", 0) < float(epoch_ts):
                        raise HTTPException(
                            status_code=401,
                            detail="Token invalidated — please log in again"
                        )
            except HTTPException:
                raise
            except Exception as exc:
                log.error("redis_unavailable_jwt_check", extra={"error": str(exc)})
                raise HTTPException(
                    status_code=503,
                    detail="Authentication service temporarily unavailable"
                )

        return payload

    except HTTPException:
        raise
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


# ---------------------------------------------------------------------------
# Rate Limiting
# ---------------------------------------------------------------------------

# Lua script for atomic check-and-increment with TTL
_RATE_LIMIT_LUA = """
if tonumber(ARGV[1]) <= 0 then return 0 end
local current = redis.call('GET', KEYS[1])
if current == false then
    redis.call('SETEX', KEYS[1], ARGV[2], 1)
    return 1
end
if tonumber(current) >= tonumber(ARGV[1]) then
    return 0
end
return redis.call('INCR', KEYS[1])
"""

_rate_limit_script = None
_quota_script = None


async def check_rate_limit(key: str, limit: int, window: int = RATE_LIMIT_WINDOW) -> bool:
    """Atomic per-window rate limit check. Fails closed on Redis errors."""
    global _rate_limit_script
    try:
        if _rate_limit_script is None:
            _rate_limit_script = redis_client.register_script(_RATE_LIMIT_LUA)
        result = await _rate_limit_script.execute(
            keys=[f"rate_limit:{key}"], args=[limit, window]
        )
        return int(result) > 0
    except (redis_exc.RedisError, Exception) as exc:
        log.error("redis_rate_limit_error", extra={"error": str(exc)})
        return False  # fail closed: deny when we can't verify


def _seconds_until_month_end() -> int:
    """Return seconds remaining until the end of the current UTC month."""
    now = time.gmtime()
    if now.tm_mon == 12:
        next_month_ts = calendar.timegm((now.tm_year + 1, 1, 1, 0, 0, 0, 0, 0, 0))
    else:
        next_month_ts = calendar.timegm((now.tm_year, now.tm_mon + 1, 1, 0, 0, 0, 0, 0, 0))
    return max(1, int(next_month_ts - time.time()))


async def check_quota(key_id: str, limit: int, user_id: str = "") -> bool:
    """Monthly call quota check (per-key and per-user aggregate).

    Uses key_id (UUID) as the Redis key so the raw API key never appears in
    Redis key names (C1).  Per-user aggregate cap prevents quota evasion via
    many keys (H8).  Fails closed on Redis errors.
    """
    global _quota_script
    month = time.strftime('%Y-%m', time.gmtime())
    key = f"quota:{key_id}:{month}"
    try:
        if _quota_script is None:
            _quota_script = redis_client.register_script(_RATE_LIMIT_LUA)
        ttl = _seconds_until_month_end()
        result = await _quota_script.execute(keys=[key], args=[limit, ttl])
        if int(result) <= 0:
            return False
        # H8: per-user aggregate quota (sum across all keys)
        if user_id:
            user_key = f"quota_user:{user_id}:{month}"
            user_limit = limit * MAX_KEYS_PER_USER
            u_result = await _quota_script.execute(keys=[user_key], args=[user_limit, ttl])
            if int(u_result) <= 0:
                return False
        return True
    except (redis_exc.RedisError, Exception) as exc:
        log.error("redis_quota_error", extra={"error": str(exc)})
        return False  # fail closed


# ---------------------------------------------------------------------------
# Routes — Health
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    """Health check — reports Redis availability and disk space."""
    redis_ok = False
    try:
        if redis_client:
            await redis_client.ping()
            redis_ok = True
    except Exception:
        pass
    disk_ok = True
    disk_free_gb = None
    try:
        usage = shutil.disk_usage("/")
        disk_free_gb = round(usage.free / (1024 ** 3), 1)
        disk_ok = disk_free_gb > 1.0
    except Exception:
        pass
    status = "ok" if (redis_ok and disk_ok) else "degraded"
    return {
        "status":       status,
        "redis":        "ok" if redis_ok else "unavailable",
        "disk_free_gb": disk_free_gb,
        "version":      "1.0.0",
    }


# ---------------------------------------------------------------------------
# Auth — token issuance
# ---------------------------------------------------------------------------

def _make_tokens(user_id: str, email: str) -> TokenResponse:
    """Generate access + refresh token pair — pure, synchronous, no I/O.

    Every token carries: iss, aud, iat, nbf, exp, jti, type (C4).
    access token  → type="access"   (H1)
    refresh token → type="refresh"

    Call _persist_session() afterwards to register the tokens in Redis
    for revocation tracking (H13, H14).
    """
    now = int(time.time())
    access_jti  = str(uuid.uuid4())
    refresh_jti = str(uuid.uuid4())

    access_token = jwt.encode(
        {
            "sub":   user_id,
            "email": email,
            "type":  "access",        # H1: explicit type on every access token
            "jti":   access_jti,
            "iss":   JWT_ISSUER,
            "aud":   JWT_AUDIENCE,
            "iat":   now,
            "nbf":   now,
            "exp":   now + 3600,      # 1 hour
        },
        JWT_SECRET,
        algorithm=JWT_ALGORITHM,
    )
    refresh_token = jwt.encode(
        {
            "sub":   user_id,
            "email": email,
            "type":  "refresh",
            "jti":   refresh_jti,
            "iss":   JWT_ISSUER,
            "aud":   JWT_AUDIENCE,
            "iat":   now,
            "nbf":   now,
            "exp":   now + SESSION_TTL,  # 7 days
        },
        JWT_SECRET,
        algorithm=JWT_ALGORITHM,
    )

    result = TokenResponse(access_token=access_token, refresh_token=refresh_token)
    # Attach jtis as private attributes so route handlers can call _persist_session
    # without re-decoding the tokens. Not included in the API response payload.
    result._access_jti  = access_jti   # type: ignore[attr-defined]
    result._refresh_jti = refresh_jti  # type: ignore[attr-defined]
    return result


async def _persist_session(user_id: str, access_jti: str, refresh_jti: str) -> None:
    """Store session tracking data in Redis (non-fatal if Redis unavailable).

    - jwt_link:{access_jti}      → refresh_jti  (for paired revocation at logout, H13)
    - user_sessions:{user_id}    → SADD refresh_jti  (for revoke-all, H14)
    """
    if redis_client:
        try:
            async with redis_client.pipeline(transaction=False) as pipe:
                pipe.setex(f"jwt_link:{access_jti}", SESSION_TTL, refresh_jti)
                pipe.sadd(f"user_sessions:{user_id}", refresh_jti)
                pipe.expire(f"user_sessions:{user_id}", SESSION_TTL)
                await pipe.execute()
        except Exception as exc:
            log.warning("session_tracking_failed", extra={"error": str(exc)})


# ---------------------------------------------------------------------------
# Auth Routes
# ---------------------------------------------------------------------------

@app.post("/api/v1/auth/register")
async def register(request: Request, data: UserRegister):
    """Register a new user.

    Rate-limited by IP. Bcrypt-hashes password before storage.
    Provisions a default KB on the MCP server (best-effort).
    """
    client_ip = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip() \
        or (request.client.host if request.client else "unknown")
    if not await check_rate_limit(f"ip_register:{client_ip}", REGISTER_RATE_LIMIT):
        raise HTTPException(status_code=429, detail="Too many registration attempts — try again later")

    email = _normalize_email(data.email)
    # Explicitly truncate to bcrypt's 72-byte limit to make the cap visible (H6)
    password_bytes = data.password.encode()[:72]
    if len(password_bytes) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

    try:
        existing = await redis_client.hget(f"user_account:{email}", "user_id")
    except (redis_exc.RedisError, Exception) as exc:
        log.error("redis_unavailable", extra={"error": str(exc)})
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")
    if existing:
        raise HTTPException(status_code=409, detail="Email already registered")

    user_id = str(uuid.uuid4())
    password_hash = await asyncio.to_thread(
        lambda: bcrypt.hashpw(password_bytes, bcrypt.gensalt()).decode()
    )
    created_at = time.strftime("%Y-%m-%dT%H:%M:%SZ")

    try:
        await redis_client.hset(f"user_account:{email}", mapping={
            "user_id":       user_id,
            "email":         email,
            "name":          data.name,
            "password_hash": password_hash,
            "created_at":    created_at,
        })
    except (redis_exc.RedisError, Exception) as exc:
        log.error("redis_unavailable", extra={"error": str(exc)})
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")

    # Provision default KB — best-effort
    try:
        if _http_client:
            await _http_client.post(
                f"{MCP_SERVER_URL}/api/v1/knowledge-bases",
                headers={"X-User-ID": user_id, "X-Internal-Token": INTERNAL_SECRET},
                json={"kb_id": "default"},
                timeout=10.0,
            )
    except Exception as exc:
        log.warning("kb_provision_skipped", extra={"user_id": user_id, "error": str(exc)})

    return {"user_id": user_id, "email": email, "created_at": created_at}


@app.post("/api/v1/auth/login", response_model=TokenResponse)
async def login(request: Request, data: UserLogin):
    """User login.

    Per-IP and per-email rate limiting (H2).
    Always runs bcrypt — constant-time regardless of whether email exists (H3).
    """
    client_ip = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip() \
        or (request.client.host if request.client else "unknown")

    # H2: per-IP rate limit
    if not await check_rate_limit(f"ip_login:{client_ip}", LOGIN_IP_RATE_LIMIT):
        raise HTTPException(status_code=429, detail="Too many login attempts — try again later")

    email = _normalize_email(data.email)

    # H2: per-email rate limit
    if not await check_rate_limit(f"email_login:{email}", LOGIN_EMAIL_RATE_LIMIT):
        raise HTTPException(status_code=429, detail="Too many login attempts — try again later")

    try:
        user_data = await redis_client.hgetall(f"user_account:{email}")
    except (redis_exc.RedisError, Exception) as exc:
        log.error("redis_unavailable", extra={"error": str(exc)})
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")

    # H3: always run bcrypt — use dummy hash when user not found so timing is uniform
    stored_hash = user_data.get("password_hash", _DUMMY_HASH) if user_data else _DUMMY_HASH
    password_bytes = data.password.encode()[:72]
    match = await asyncio.to_thread(
        lambda: bcrypt.checkpw(password_bytes, stored_hash.encode())
    )

    if not user_data or not match:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    tokens = _make_tokens(user_data["user_id"], email)
    await _persist_session(user_data["user_id"], tokens._access_jti, tokens._refresh_jti)  # type: ignore[attr-defined]
    return tokens


@app.post("/api/v1/auth/refresh", response_model=TokenResponse)
async def refresh_token(request: Request):
    """Refresh access token with single-use rotation (C2).

    - Validates the presented refresh token
    - Revokes it atomically (single-use)
    - Detects replay (already-revoked token) → revokes ALL user sessions
    - Issues a new access + refresh token pair
    - Rate-limited per IP (M1)
    """
    client_ip = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip() \
        or (request.client.host if request.client else "unknown")
    if not await check_rate_limit(f"ip_refresh:{client_ip}", REFRESH_RATE_LIMIT):
        raise HTTPException(status_code=429, detail="Too many refresh attempts — try again later")

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    token = body.get("refresh_token")
    if not token:
        raise HTTPException(status_code=400, detail="refresh_token is required")

    try:
        payload = jwt.decode(
            token, JWT_SECRET,
            algorithms=["HS256"],
            issuer=JWT_ISSUER,
            audience=JWT_AUDIENCE,
        )
        if payload.get("type") != "refresh":
            raise HTTPException(status_code=401, detail="Invalid refresh token")

        jti     = payload.get("jti")
        user_id = payload["sub"]
        email   = payload.get("email", "")
        exp     = payload.get("exp", 0)

        if not jti:
            raise HTTPException(status_code=401, detail="Malformed refresh token")

        try:
            # C2: check-then-set — if already revoked, this is a replay attack
            already_revoked = await redis_client.exists(f"jwt_jti:{jti}")
            if already_revoked:
                log.warning(
                    "refresh_token_replay_detected",
                    extra={"user_id": user_id, "jti": jti},
                )
                await _revoke_all_sessions(user_id)
                raise HTTPException(
                    status_code=401,
                    detail="Refresh token already used — all sessions revoked. Please log in again."
                )

            # Revoke the consumed token (single-use rotation)
            ttl = max(int(exp) - int(time.time()), 60)
            await redis_client.setex(f"jwt_jti:{jti}", ttl, "1")
            await redis_client.srem(f"user_sessions:{user_id}", jti)

        except HTTPException:
            raise
        except Exception as exc:
            log.error("redis_unavailable_refresh", extra={"error": str(exc)})
            raise HTTPException(
                status_code=503,
                detail="Authentication service temporarily unavailable"
            )

        tokens = _make_tokens(user_id, email)
        await _persist_session(user_id, tokens._access_jti, tokens._refresh_jti)  # type: ignore[attr-defined]
        return tokens

    except HTTPException:
        raise
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Refresh token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid refresh token")


@app.post("/api/v1/auth/logout")
async def logout(request: Request, user: dict = Depends(verify_jwt_token)):
    """Revoke the current access token and its paired refresh token (H13).

    Optionally accepts body: {"refresh_token": "<token>"}.
    If not provided, the paired refresh token is resolved from the
    jwt_link:{access_jti} key set at issue time.
    """
    access_jti = user.get("jti")
    user_id    = user.get("sub", "")
    exp        = user.get("exp")
    ttl        = max(int(exp) - int(time.time()), 60) if exp else 86400

    refresh_jti: Optional[str] = None

    # Try to extract refresh token from body (optional)
    try:
        body = await request.json()
        provided_rt = body.get("refresh_token")
        if provided_rt:
            try:
                rt_payload = jwt.decode(
                    provided_rt, JWT_SECRET,
                    algorithms=["HS256"],
                    issuer=JWT_ISSUER,
                    audience=JWT_AUDIENCE,
                    options={"verify_exp": False},
                )
                if rt_payload.get("type") == "refresh" and rt_payload.get("sub") == user_id:
                    refresh_jti = rt_payload.get("jti")
            except jwt.InvalidTokenError:
                pass  # ignore invalid refresh token at logout
    except Exception:
        pass  # no body or non-JSON — fall through to link lookup

    if redis_client:
        try:
            # If no refresh token in body, resolve from the link stored at issue time
            if access_jti and refresh_jti is None:
                refresh_jti = await redis_client.get(f"jwt_link:{access_jti}")

            if access_jti:
                await redis_client.setex(f"jwt_jti:{access_jti}", ttl, "1")
                await redis_client.delete(f"jwt_link:{access_jti}")
            if refresh_jti:
                await redis_client.setex(f"jwt_jti:{refresh_jti}", SESSION_TTL, "1")
                await redis_client.srem(f"user_sessions:{user_id}", refresh_jti)
        except Exception as exc:
            log.warning("logout_revocation_failed", extra={"error": str(exc)})

    return {"message": "Logged out"}


async def _revoke_all_sessions(user_id: str) -> None:
    """Revoke all active refresh tokens for a user (H14).

    Also bumps auth_epoch to invalidate all outstanding access tokens (M2).
    Called on refresh-token replay detection and on explicit user request.
    """
    try:
        session_key = f"user_sessions:{user_id}"
        refresh_jtis = await redis_client.smembers(session_key)
        async with redis_client.pipeline(transaction=False) as pipe:
            for jti in refresh_jtis:
                pipe.setex(f"jwt_jti:{jti}", SESSION_TTL, "1")
            pipe.delete(session_key)
            # M2: bump epoch → all existing access tokens (issued before now) rejected
            pipe.set(f"auth_epoch:{user_id}", str(int(time.time())))
            await pipe.execute()
        log.info("all_sessions_revoked", extra={"user_id": user_id, "count": len(refresh_jtis)})
    except Exception as exc:
        log.error("revoke_all_sessions_failed", extra={"user_id": user_id, "error": str(exc)})


@app.delete("/api/v1/auth/sessions")
async def revoke_all_sessions_endpoint(user: dict = Depends(verify_jwt_token)):
    """Revoke all active sessions for the authenticated user (H14).

    Use this if you suspect your account has been compromised.
    Forces re-login on all devices.
    """
    user_id = user.get("sub", "")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token")
    try:
        await _revoke_all_sessions(user_id)
    except Exception as exc:
        log.error("revoke_all_sessions_error", extra={"error": str(exc)})
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")
    return {"message": "All sessions revoked — please log in again on all devices"}


# ---------------------------------------------------------------------------
# API Key Management
# ---------------------------------------------------------------------------

@app.get("/api/v1/api-keys")
async def list_api_keys(user: dict = Depends(verify_jwt_token)):
    """List API Keys for the authenticated user."""
    user_id = user["sub"]
    try:
        keys = await redis_client.smembers(f"user:{user_id}:api_keys")
        result = []
        for key_id in keys:
            key_data = await redis_client.hgetall(f"api_key_data:{key_id}")
            if key_data:
                result.append({
                    "id":          key_id,
                    "name":        key_data.get("name"),
                    "permissions": key_data.get("permissions", "read").split(","),
                    "rate_limit":  int(key_data.get("rate_limit", 100)),
                    "quota_limit": int(key_data.get("quota_limit", 10000)),
                    "created_at":  key_data.get("created_at"),
                    "is_active":   key_data.get("is_active") == "true",
                })
    except (redis_exc.RedisError, Exception) as exc:
        log.error("redis_unavailable", extra={"error": str(exc)})
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")
    return {"api_keys": result}


@app.post("/api/v1/api-keys", response_model=APIKeyResponse)
async def create_api_key(data: APIKeyCreate, user: dict = Depends(verify_jwt_token)):
    """Create a new API Key.

    Enforces per-user key cap (H8) and per-user hourly creation rate limit (H8).
    Returns the plaintext key exactly once — it is not stored (C1).
    """
    if not data.name.strip():
        raise HTTPException(status_code=400, detail="API key name is required")
    if not data.permissions:
        raise HTTPException(status_code=400, detail="At least one permission is required")
    unknown = set(data.permissions) - _ALLOWED_PERMISSIONS
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown permissions: {sorted(unknown)}")

    user_id = user["sub"]

    # H8: per-user creation rate limit (5/hour)
    if not await check_rate_limit(f"key_create:{user_id}", KEY_CREATE_RATE_LIMIT, window=3600):
        raise HTTPException(
            status_code=429,
            detail="Too many API key creation attempts — try again later"
        )

    key_id   = str(uuid.uuid4())
    api_key  = f"kb_live_{uuid.uuid4().hex}"
    key_hash = _hash_api_key(api_key)           # C1: hash before any Redis write
    created_at = time.strftime("%Y-%m-%dT%H:%M:%SZ")

    try:
        # H8: enforce per-user key cap
        current_count = await redis_client.scard(f"user:{user_id}:api_keys")
        if current_count >= MAX_KEYS_PER_USER:
            raise HTTPException(
                status_code=400,
                detail=f"Maximum of {MAX_KEYS_PER_USER} API keys per user — delete an existing key first"
            )

        async with redis_client.pipeline(transaction=True) as pipe:
            # C1: lookup key is the HMAC digest, not the plaintext
            pipe.hset(f"api_key:{key_hash}", mapping={
                "user_id":     user_id,
                "key_id":      key_id,
                "permissions": ",".join(data.permissions),
                "rate_limit":  str(RATE_LIMIT_DEFAULT),
                "quota_limit": "10000",
                "is_active":   "true",
            })
            # C1: store hash (not plaintext) for later deactivation
            pipe.hset(f"api_key_data:{key_id}", mapping={
                "user_id":      user_id,
                "api_key_hash": key_hash,    # never store the raw key
                "name":         data.name,
                "permissions":  ",".join(data.permissions),
                "rate_limit":   str(RATE_LIMIT_DEFAULT),
                "quota_limit":  "10000",
                "created_at":   created_at,
                "is_active":    "true",
            })
            pipe.sadd(f"user:{user_id}:api_keys", key_id)
            await pipe.execute()
    except HTTPException:
        raise
    except (redis_exc.RedisError, Exception) as exc:
        log.error("redis_unavailable", extra={"error": str(exc)})
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")

    log.info(
        "api_key_created",
        extra={"user_id": user_id, "key_id": key_id,
               "name": data.name, "permissions": data.permissions},
    )
    return APIKeyResponse(
        id=key_id,
        name=data.name,
        key=api_key,          # returned only once — not stored anywhere
        permissions=data.permissions,
        rate_limit=RATE_LIMIT_DEFAULT,
        quota_limit=10000,
        created_at=created_at,
    )


@app.delete("/api/v1/api-keys/{key_id}")
async def delete_api_key(key_id: str, user: dict = Depends(verify_jwt_token)):
    """Delete (deactivate) an API Key."""
    user_id = user["sub"]
    try:
        key_data = await redis_client.hgetall(f"api_key_data:{key_id}")
        if not key_data or key_data.get("user_id") != user_id:
            raise HTTPException(status_code=404, detail="API Key not found")

        # C1: use stored HMAC hash to deactivate the lookup entry
        key_hash = key_data.get("api_key_hash", "")
        async with redis_client.pipeline(transaction=True) as pipe:
            pipe.hset(f"api_key_data:{key_id}", "is_active", "false")
            if key_hash:
                pipe.hset(f"api_key:{key_hash}", "is_active", "false")
            pipe.srem(f"user:{user_id}:api_keys", key_id)
            await pipe.execute()
    except HTTPException:
        raise
    except (redis_exc.RedisError, Exception) as exc:
        log.error("redis_unavailable", extra={"error": str(exc)})
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")
    return {"message": "API Key deleted"}


# ---------------------------------------------------------------------------
# Hop-by-hop headers excluded from proxy responses
# ---------------------------------------------------------------------------

_HOP_BY_HOP_HEADERS = frozenset({
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade",
    "content-encoding", "server",
})


# ---------------------------------------------------------------------------
# MCP Proxy
# ---------------------------------------------------------------------------

async def _proxy_to_mcp(request: Request, path: str, method: str) -> Response:
    """
    Verify API Key → rate limit → quota → forward to MCP Server.
    Uses key_id (UUID) for rate/quota Redis keys so raw API keys never appear
    in Redis key names (C1).
    """
    req_id   = getattr(request.state, "req_id", uuid.uuid4().hex[:8])
    api_key  = await get_api_key(request)
    key_info = await verify_api_key(api_key, request)

    user_id     = key_info["user_id"]
    key_id      = key_info["key_id"]        # use key_id for rate/quota keys (C1)
    kb_id       = request.headers.get("X-KB-ID") or request.query_params.get("kb_id", "default")
    kb_owner_id = request.headers.get("X-KB-Owner-ID", user_id)

    if not await check_rate_limit(key_id, key_info["rate_limit"]):
        log.warning("rate_limit_exceeded",
                    extra={"req_id": req_id, "user_id": user_id,
                           "kb_id": kb_id, "key_id": key_id})
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

    if not await check_quota(key_id, key_info["quota_limit"], user_id=user_id):
        log.warning("quota_exceeded",
                    extra={"req_id": req_id, "user_id": user_id,
                           "kb_id": kb_id, "key_id": key_id})
        raise HTTPException(status_code=429, detail="Quota exceeded")

    forwarded_headers = {
        "X-User-ID":        user_id,
        "X-KB-ID":          kb_id,
        "X-KB-Owner-ID":    kb_owner_id,
        "X-API-Key-ID":     key_id,
        "X-Permissions":    ",".join(key_info["permissions"]),
        "X-Request-ID":     req_id,
        "X-Internal-Token": INTERNAL_SECRET,
    }

    client = _http_client
    if client is None:
        raise HTTPException(status_code=503, detail="Gateway not initialized")
    try:
        if method == "GET":
            response = await client.get(
                f"{MCP_SERVER_URL}/{path}",
                headers=forwarded_headers,
                params=dict(request.query_params),
            )
        else:
            body = await request.body()
            response = await client.post(
                f"{MCP_SERVER_URL}/{path}",
                content=body,
                headers={"Content-Type": "application/json", **forwarded_headers},
            )

        client_ip = (
            (request.headers.get("X-Forwarded-For", "") or "").split(",")[0].strip()
            or (request.client.host if request.client else "unknown")
        )
        asyncio.create_task(_record_usage(api_key, user_id, path, client_ip))

        safe_headers = {
            k: v for k, v in response.headers.items()
            if k.lower() not in _HOP_BY_HOP_HEADERS
        }
        return Response(
            content=response.content,
            status_code=response.status_code,
            headers=safe_headers,
        )

    except httpx.TimeoutException:
        log.error("proxy_timeout",
                  extra={"req_id": req_id, "user_id": user_id, "path": path})
        raise HTTPException(status_code=504, detail="MCP Server timeout")
    except Exception as exc:
        log.error("proxy_error",
                  extra={"req_id": req_id, "user_id": user_id, "path": path,
                         "error": f"{type(exc).__name__}: {exc}"},
                  exc_info=True)
        raise HTTPException(status_code=502, detail="MCP Server error")


@app.get("/mcp/v1/{path:path}")
async def mcp_proxy_get(path: str, request: Request):
    """Proxy authenticated GET requests to the MCP server."""
    return await _proxy_to_mcp(request, path, "GET")


@app.post("/mcp/v1/{path:path}")
async def mcp_proxy_post(path: str, request: Request):
    """Proxy authenticated POST requests to the MCP server."""
    return await _proxy_to_mcp(request, path, "POST")


async def _record_usage(api_key: str, user_id: str, path: str, client_ip: str):
    """Persist usage to Redis. Falls back to a local file on Redis failure."""
    from datetime import datetime as _datetime

    entry = json.dumps({
        "key_prefix": redact_key(api_key),
        "user_id":    user_id,
        "path":       path,
        "ip":         client_ip,
        "ts":         time.time(),
    }, ensure_ascii=False)
    try:
        await redis_client.lpush("usage_logs", entry)
        await redis_client.ltrim("usage_logs", 0, 9_999)
    except Exception as exc:
        log.error(
            "USAGE_RECORD_FAILURE user=%s ts=%s err=%s",
            user_id, time.time(), exc,
        )
        try:
            USAGE_FALLBACK_PATH.parent.mkdir(parents=True, exist_ok=True)
            with USAGE_FALLBACK_PATH.open("a") as f:
                f.write(json.dumps({
                    "user": user_id,
                    "ts":   str(_datetime.utcnow()),
                    "err":  str(exc),
                    "entry": entry,
                }) + "\n")
        except Exception as fallback_exc:
            log.error("USAGE_FALLBACK_WRITE_FAILURE path=%s err=%s",
                      USAGE_FALLBACK_PATH, fallback_exc)


# ---------------------------------------------------------------------------
# Usage Stats
# ---------------------------------------------------------------------------

@app.get("/api/v1/usage/stats")
async def get_usage_stats(request: Request, api_key: str = Depends(get_api_key)):
    """Get usage statistics for this API Key."""
    key_info = await verify_api_key(api_key, request)
    key_id   = key_info["key_id"]
    user_id  = key_info["user_id"]

    if not await check_rate_limit(key_id, key_info["rate_limit"]):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
    if not await check_quota(key_id, key_info["quota_limit"], user_id=user_id):
        raise HTTPException(status_code=429, detail="Quota exceeded")

    try:
        quota_key    = f"quota:{key_id}:{time.strftime('%Y-%m', time.gmtime())}"
        used         = int(await redis_client.get(quota_key) or 0)
        rate_key     = f"rate_limit:{key_id}"
        current_rate = int(await redis_client.get(rate_key) or 0)
    except (redis_exc.RedisError, Exception) as exc:
        log.error("redis_unavailable", extra={"error": str(exc)})
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")

    return {
        "quota": {
            "limit":     key_info["quota_limit"],
            "used":      used,
            "remaining": max(0, key_info["quota_limit"] - used),
        },
        "rate_limit": {
            "limit":   key_info["rate_limit"],
            "current": current_rate,
            "window":  RATE_LIMIT_WINDOW,
        },
    }


# ---------------------------------------------------------------------------
# KB Member Management (JWT-authenticated)
# ---------------------------------------------------------------------------

@app.post("/api/v1/knowledge-bases/{kb_id}/members")
async def add_kb_member(kb_id: str, data: KBMemberAdd, user: dict = Depends(verify_jwt_token)):
    """Add or update a member on the caller's KB."""
    owner_id = user["sub"]
    if not await check_rate_limit(f"jwt_member:{owner_id}", JWT_MEMBER_RATE_LIMIT):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

    if data.role not in ("read", "write"):
        raise HTTPException(status_code=400, detail="role must be 'read' or 'write'")

    email = _normalize_email(data.email)
    try:
        member_data = await redis_client.hgetall(f"user_account:{email}")
    except Exception as exc:
        log.error("redis_unavailable", extra={"error": str(exc)})
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")

    if not member_data:
        raise HTTPException(status_code=404, detail="User not found or cannot be added as a member")

    member_user_id = member_data["user_id"]

    if _http_client is None:
        raise HTTPException(status_code=503, detail="Gateway not initialized")

    try:
        resp = await _http_client.post(
            f"{MCP_SERVER_URL}/api/v1/knowledge-bases/members",
            headers={"X-User-ID": owner_id, "X-KB-ID": kb_id, "X-Internal-Token": INTERNAL_SECRET},
            json={"member_user_id": member_user_id, "role": data.role},
            timeout=10.0,
        )
    except Exception as exc:
        log.error("mcp_member_add_failed", extra={"error": str(exc)})
        raise HTTPException(status_code=502, detail="MCP server error")

    if resp.status_code == 404:
        raise HTTPException(status_code=404, detail="Knowledge base not found")
    if resp.status_code == 403:
        raise HTTPException(status_code=403, detail="Not the KB owner")
    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code, detail="Failed to add member")

    return {"kb_id": kb_id, "email": email, "member_user_id": member_user_id, "role": data.role}


@app.delete("/api/v1/knowledge-bases/{kb_id}/members/{member_email}")
async def remove_kb_member(
    kb_id: str, member_email: str, user: dict = Depends(verify_jwt_token)
):
    """Remove a member from the caller's KB."""
    owner_id = user["sub"]
    if not await check_rate_limit(f"jwt_member:{owner_id}", JWT_MEMBER_RATE_LIMIT):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

    email = member_email.lower().strip()
    try:
        member_data = await redis_client.hgetall(f"user_account:{email}")
    except Exception as exc:
        log.error("redis_unavailable", extra={"error": str(exc)})
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")

    if not member_data:
        raise HTTPException(status_code=404, detail="User not found or cannot be added as a member")

    member_user_id = member_data["user_id"]

    if _http_client is None:
        raise HTTPException(status_code=503, detail="Gateway not initialized")

    try:
        resp = await _http_client.delete(
            f"{MCP_SERVER_URL}/api/v1/knowledge-bases/members/{member_user_id}",
            headers={"X-User-ID": owner_id, "X-KB-ID": kb_id, "X-Internal-Token": INTERNAL_SECRET},
            timeout=10.0,
        )
    except Exception as exc:
        log.error("mcp_member_remove_failed", extra={"error": str(exc)})
        raise HTTPException(status_code=502, detail="MCP server error")

    if resp.status_code == 404:
        raise HTTPException(status_code=404, detail="Knowledge base or member not found")
    if resp.status_code == 403:
        raise HTTPException(status_code=403, detail="Not the KB owner")
    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code, detail="Failed to remove member")

    return {"kb_id": kb_id, "email": email, "member_user_id": member_user_id, "action": "removed"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

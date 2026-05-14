#!/usr/bin/env python3
"""
API Gateway — Entry point for third-party access.
Handles routing, authentication, rate limiting, and logging.
"""

import json
import os
import shutil
import sys
import time
import asyncio
import calendar
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


# Configuration
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
JWT_SECRET = os.getenv("JWT_SECRET", "")
JWT_ALGORITHM = "HS256"
if not JWT_SECRET or JWT_SECRET in ("your-secret-key", "changeme", "secret"):
    raise RuntimeError("JWT_SECRET env var is not set or uses an insecure default — set a strong random value")

# CORS — set CORS_ORIGINS to a comma-separated list of allowed origins (e.g. "https://app.example.com")
_cors_env = os.getenv("CORS_ORIGINS", "")
CORS_ORIGINS: list[str] = [o.strip() for o in _cors_env.split(",") if o.strip()]

# Rate limiting
RATE_LIMIT_WINDOW = 60   # seconds per window
RATE_LIMIT_DEFAULT = 100  # requests per window

# Redis client
redis_client: Optional[redis.Redis] = None

# Upstream MCP server URL
MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", "http://localhost:8001")

# Shared HTTP client — reuses connections across proxy requests
_http_client: Optional[httpx.AsyncClient] = None


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


class _RequestLogMiddleware(BaseHTTPMiddleware):
    """
    Assign a request ID to every inbound request, time the response,
    and emit one structured log line per request.

    The request ID is taken from the incoming X-Request-ID header if present
    (so upstream load balancers can inject it), otherwise a new 8-hex-char ID
    is generated.  The ID is forwarded to the MCP server via the same header
    and included in the response so clients can correlate logs.
    """

    async def dispatch(self, request: Request, call_next):
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
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============ Data Models ============

class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = 3600

_ALLOWED_PERMISSIONS = {"read", "write", "admin"}


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

class APIKeyResponse(BaseModel):
    id: str
    name: str
    key: str  # returned only once at creation time
    permissions: List[str]
    rate_limit: int
    quota_limit: int
    created_at: str


# ============ Authentication ============

security = HTTPBearer(auto_error=False)

async def get_api_key(request: Request) -> Optional[str]:
    """Extract API Key from request headers"""
    # 1. X-API-Key header
    api_key = request.headers.get("X-API-Key")
    if api_key:
        return api_key

    # 2. Authorization: Bearer <token>
    auth = request.headers.get("Authorization")
    if auth and auth.startswith("Bearer "):
        return auth[7:]

    return None

async def verify_api_key(api_key: str, request: Optional[Request] = None) -> dict:
    """Verify API Key against Redis store. Raises 503 if Redis is unavailable."""
    req_id = getattr(getattr(request, "state", None), "req_id", "-") if request else "-"

    if not api_key:
        log.warning("auth_fail", extra={"req_id": req_id, "error": "no_api_key"})
        raise HTTPException(status_code=401, detail="API Key required")

    try:
        key_data = await redis_client.hgetall(f"api_key:{api_key}")
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
        "user_id":    key_data.get("user_id", ""),
        "key_id":     key_data["key_id"],
        "permissions": key_data.get("permissions", "read").split(","),
        "rate_limit": int(key_data.get("rate_limit", RATE_LIMIT_DEFAULT)),
        "quota_limit": int(key_data.get("quota_limit", 10000)),
    }

async def verify_jwt_token(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    """Verify JWT token and check against per-jti revocation keys."""
    if not credentials:
        raise HTTPException(status_code=401, detail="Authorization required")

    try:
        payload = jwt.decode(credentials.credentials, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        jti = payload.get("jti")
        if jti and redis_client:
            try:
                if await redis_client.exists(f"jwt_jti:{jti}"):
                    raise HTTPException(status_code=401, detail="Token has been revoked")
            except HTTPException:
                raise
            except Exception:
                pass  # Redis unavailable — fail open, token assumed valid
        return payload
    except HTTPException:
        raise
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


# ============ Rate Limiting ============

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


async def check_rate_limit(api_key: str, limit: int) -> bool:
    """
    Check per-minute rate limit using atomic Lua script.
    Fails closed on Redis errors — returns False (deny) rather than allow through.
    """
    global _rate_limit_script
    try:
        if _rate_limit_script is None:
            _rate_limit_script = redis_client.register_script(_RATE_LIMIT_LUA)
        key = f"rate_limit:{api_key}"
        result = await _rate_limit_script.execute(keys=[key], args=[limit, RATE_LIMIT_WINDOW])
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


async def check_quota(api_key: str, limit: int) -> bool:
    """
    Check monthly call quota using atomic Lua script.
    Auto-resets at month end via Redis TTL.
    Fails closed on Redis errors — returns False (deny).
    """
    global _quota_script
    key = f"quota:{api_key}:{time.strftime('%Y-%m', time.gmtime())}"
    try:
        if _quota_script is None:
            _quota_script = redis_client.register_script(_RATE_LIMIT_LUA)
        ttl = _seconds_until_month_end()
        result = await _quota_script.execute(keys=[key], args=[limit, ttl])
        return int(result) > 0
    except (redis_exc.RedisError, Exception) as exc:
        log.error("redis_quota_error", extra={"error": str(exc)})
        return False  # fail closed


# ============ Routes ============

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
        disk_ok = disk_free_gb > 1.0  # warn if less than 1 GB free
    except Exception:
        pass
    status = "ok" if (redis_ok and disk_ok) else "degraded"
    return {
        "status":      status,
        "redis":       "ok" if redis_ok else "unavailable",
        "disk_free_gb": disk_free_gb,
        "version":     "1.0.0",
    }


# ============ Auth Routes ============

def _make_tokens(user_id: str, email: str) -> TokenResponse:
    """Issue a fresh access + refresh token pair, each with a unique jti claim."""
    access_jti = str(uuid.uuid4())
    refresh_jti = str(uuid.uuid4())
    access_token = jwt.encode(
        {"sub": user_id, "email": email, "jti": access_jti, "exp": int(time.time()) + 3600},
        JWT_SECRET,
        algorithm=JWT_ALGORITHM
    )
    refresh_token = jwt.encode(
        {"sub": user_id, "email": email, "type": "refresh", "jti": refresh_jti, "exp": int(time.time()) + 7 * 24 * 3600},
        JWT_SECRET,
        algorithm=JWT_ALGORITHM
    )
    return TokenResponse(access_token=access_token, refresh_token=refresh_token)


@app.post("/api/v1/auth/register")
async def register(data: UserRegister):
    """
    Register a new user. Stores bcrypt-hashed password in Redis.
    Redis key: user_account:{email}
    """
    email = data.email.lower().strip()
    if len(data.password) < 8:
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
        lambda: bcrypt.hashpw(data.password.encode(), bcrypt.gensalt()).decode()
    )
    created_at = time.strftime("%Y-%m-%dT%H:%M:%SZ")

    try:
        await redis_client.hset(f"user_account:{email}", mapping={
            "user_id": user_id,
            "email": email,
            "name": data.name,
            "password_hash": password_hash,
            "created_at": created_at,
        })
    except (redis_exc.RedisError, Exception) as exc:
        log.error("redis_unavailable", extra={"error": str(exc)})
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")

    return {"user_id": user_id, "email": email, "created_at": created_at}


@app.post("/api/v1/auth/login", response_model=TokenResponse)
async def login(data: UserLogin):
    """User login — verifies bcrypt password against Redis store."""
    email = data.email.lower().strip()

    try:
        user_data = await redis_client.hgetall(f"user_account:{email}")
    except (redis_exc.RedisError, Exception) as exc:
        log.error("redis_unavailable", extra={"error": str(exc)})
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")
    if not user_data:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    stored_hash = user_data.get("password_hash", "")
    match = await asyncio.to_thread(
        lambda: bcrypt.checkpw(data.password.encode(), stored_hash.encode())
    )
    if not match:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    return _make_tokens(user_data["user_id"], email)


@app.post("/api/v1/auth/refresh", response_model=TokenResponse)
async def refresh_token(request: Request):
    """Refresh access token — validates and checks the refresh token for revocation."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    token = body.get("refresh_token")

    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        if payload.get("type") != "refresh":
            raise HTTPException(status_code=401, detail="Invalid refresh token")

        jti = payload.get("jti")
        if jti and redis_client:
            try:
                if await redis_client.exists(f"jwt_jti:{jti}"):
                    raise HTTPException(status_code=401, detail="Refresh token has been revoked")
            except HTTPException:
                raise
            except Exception:
                pass  # Redis unavailable — fail open

        user_id = payload["sub"]
        email = payload.get("email", "")
        return _make_tokens(user_id, email)

    except HTTPException:
        raise
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid refresh token")


@app.post("/api/v1/auth/logout")
async def logout(user: dict = Depends(verify_jwt_token)):
    """Revoke the current access token (and optionally a refresh token) via per-jti Redis keys."""
    jti = user.get("jti")
    if jti and redis_client:
        exp = user.get("exp", int(time.time()) + 3600)
        ttl = max(exp - int(time.time()), 1)
        try:
            await redis_client.setex(f"jwt_jti:{jti}", ttl, "1")
        except Exception as exc:
            log.warning("logout_revocation_failed", extra={"error": str(exc)})
    return {"message": "Logged out"}


# ============ API Key Management ============

@app.get("/api/v1/api-keys")
async def list_api_keys(user: dict = Depends(verify_jwt_token)):
    """List API Keys for the authenticated user (requires JWT)"""
    user_id = user["sub"]
    try:
        keys = await redis_client.smembers(f"user:{user_id}:api_keys")
        result = []
        for key_id in keys:
            key_data = await redis_client.hgetall(f"api_key_data:{key_id}")
            if key_data:
                result.append({
                    "id": key_id,
                    "name": key_data.get("name"),
                    "permissions": key_data.get("permissions", "read").split(","),
                    "rate_limit": int(key_data.get("rate_limit", 100)),
                    "quota_limit": int(key_data.get("quota_limit", 10000)),
                    "created_at": key_data.get("created_at"),
                    "is_active": key_data.get("is_active") == "true"
                })
    except (redis_exc.RedisError, Exception) as exc:
        log.error("redis_unavailable", extra={"error": str(exc)})
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")
    return {"api_keys": result}


@app.post("/api/v1/api-keys", response_model=APIKeyResponse)
async def create_api_key(
    data: APIKeyCreate,
    user: dict = Depends(verify_jwt_token)
):
    """Create a new API Key"""
    if not data.name.strip():
        raise HTTPException(status_code=400, detail="API key name is required")
    if not data.permissions:
        raise HTTPException(status_code=400, detail="At least one permission is required")
    unknown = set(data.permissions) - _ALLOWED_PERMISSIONS
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown permissions: {sorted(unknown)}")

    user_id = user["sub"]
    key_id = str(uuid.uuid4())
    api_key = f"kb_live_{uuid.uuid4().hex}"

    created_at = time.strftime("%Y-%m-%dT%H:%M:%SZ")

    try:
        async with redis_client.pipeline(transaction=True) as pipe:
            pipe.hset(f"api_key:{api_key}", mapping={
                "user_id": user_id,
                "key_id": key_id,
                "permissions": ",".join(data.permissions),
                "rate_limit": str(RATE_LIMIT_DEFAULT),
                "quota_limit": "10000",
                "is_active": "true"
            })
            pipe.hset(f"api_key_data:{key_id}", mapping={
                "user_id": user_id,
                "api_key": api_key,
                "name": data.name,
                "permissions": ",".join(data.permissions),
                "rate_limit": str(RATE_LIMIT_DEFAULT),
                "quota_limit": "10000",
                "created_at": created_at,
                "is_active": "true"
            })
            pipe.sadd(f"user:{user_id}:api_keys", key_id)
            await pipe.execute()
    except (redis_exc.RedisError, Exception) as exc:
        log.error("redis_unavailable", extra={"error": str(exc)})
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")
    
    return APIKeyResponse(
        id=key_id,
        name=data.name,
        key=api_key,  # returned only once at creation time
        permissions=data.permissions,
        rate_limit=RATE_LIMIT_DEFAULT,
        quota_limit=10000,
        created_at=created_at
    )


@app.delete("/api/v1/api-keys/{key_id}")
async def delete_api_key(key_id: str, user: dict = Depends(verify_jwt_token)):
    """Delete (deactivate) an API Key"""
    user_id = user["sub"]
    try:
        key_data = await redis_client.hgetall(f"api_key_data:{key_id}")
        if not key_data or key_data.get("user_id") != user_id:
            raise HTTPException(status_code=404, detail="API Key not found")

        raw_key = key_data.get("api_key", "")
        async with redis_client.pipeline(transaction=True) as pipe:
            pipe.hset(f"api_key_data:{key_id}", "is_active", "false")
            if raw_key:
                pipe.hset(f"api_key:{raw_key}", "is_active", "false")
            pipe.srem(f"user:{user_id}:api_keys", key_id)
            await pipe.execute()
    except HTTPException:
        raise
    except (redis_exc.RedisError, Exception) as exc:
        log.error("redis_unavailable", extra={"error": str(exc)})
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")
    return {"message": "API Key deleted"}


# Headers that must not be forwarded when proxying responses
_HOP_BY_HOP_HEADERS = frozenset({
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade",
    "content-encoding", "server",
})


# ============ MCP Proxy (core) ============

async def _proxy_to_mcp(request: Request, path: str, method: str) -> Response:
    """
    Core proxy logic: verify API Key → rate limit → quota → forward to MCP Server.
    Shared by GET and POST routes so all MCP endpoints require authentication.
    """
    req_id  = getattr(request.state, "req_id", uuid.uuid4().hex[:8])
    api_key = await get_api_key(request)
    key_info = await verify_api_key(api_key, request)

    user_id = key_info["user_id"]
    kb_id   = request.headers.get("X-KB-ID") or request.query_params.get("kb_id", "default")

    if not await check_rate_limit(api_key, key_info["rate_limit"]):
        log.warning(
            "rate_limit_exceeded",
            extra={"req_id": req_id, "user_id": user_id, "kb_id": kb_id,
                   "key_id": key_info["key_id"]},
        )
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

    if not await check_quota(api_key, key_info["quota_limit"]):
        log.warning(
            "quota_exceeded",
            extra={"req_id": req_id, "user_id": user_id, "kb_id": kb_id,
                   "key_id": key_info["key_id"]},
        )
        raise HTTPException(status_code=429, detail="Quota exceeded")

    forwarded_headers = {
        "X-User-ID":    user_id,
        "X-KB-ID":      kb_id,
        "X-API-Key-ID": key_info["key_id"],
        "X-Permissions": ",".join(key_info["permissions"]),
        "X-Request-ID": req_id,          # propagate correlation ID to MCP server
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
                headers={
                    "Content-Type": "application/json",
                    **forwarded_headers,
                },
            )

        # Fire-and-forget usage logging
        asyncio.create_task(
            _record_usage(api_key, user_id, path, request)
        )

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
        log.error(
            "proxy_timeout",
            extra={"req_id": req_id, "user_id": user_id, "path": path},
        )
        raise HTTPException(status_code=504, detail="MCP Server timeout")

    except Exception as exc:
        log.error(
            "proxy_error",
            extra={"req_id": req_id, "user_id": user_id, "path": path,
                   "error": f"{type(exc).__name__}: {exc}"},
            exc_info=True,
        )
        raise HTTPException(status_code=502, detail="MCP Server error")


@app.get("/mcp/v1/{path:path}")
async def mcp_proxy_get(path: str, request: Request):
    """Proxy authenticated GET requests to the MCP server (e.g. list-tools, list-resources)."""
    return await _proxy_to_mcp(request, path, "GET")


@app.post("/mcp/v1/{path:path}")
async def mcp_proxy_post(path: str, request: Request):
    """Proxy authenticated POST requests to the MCP server (e.g. tool calls)."""
    return await _proxy_to_mcp(request, path, "POST")


async def _record_usage(api_key: str, user_id: str, path: str, request: Request):
    """
    Persist usage to Redis for the /api/v1/usage/stats endpoint.
    Stores a JSON string so it's machine-parseable if ever replayed.
    Keeps the last 10 000 entries (LPUSH + LTRIM).
    """
    try:
        req_id = getattr(getattr(request, "state", None), "req_id", "-")
        entry = json.dumps({
            "req_id":     req_id,
            "key_prefix": redact_key(api_key),
            "user_id":    user_id,
            "path":       path,
            "ip":         request.client.host if request.client else "?",
            "ts":         time.time(),
        }, ensure_ascii=False)
        await redis_client.lpush("usage_logs", entry)
        await redis_client.ltrim("usage_logs", 0, 9_999)
    except Exception as exc:
        log.warning("_record_usage failed: %s", exc)


# ============ Usage Stats ============

@app.get("/api/v1/usage/stats")
async def get_usage_stats(request: Request, api_key: str = Depends(get_api_key)):
    """Get usage statistics for this API Key"""
    key_info = await verify_api_key(api_key, request)
    if not await check_rate_limit(api_key, key_info["rate_limit"]):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
    if not await check_quota(api_key, key_info["quota_limit"]):
        raise HTTPException(status_code=429, detail="Quota exceeded")

    try:
        quota_key = f"quota:{api_key}:{time.strftime('%Y-%m', time.gmtime())}"
        used = int(await redis_client.get(quota_key) or 0)
        rate_key = f"rate_limit:{api_key}"
        current_rate = int(await redis_client.get(rate_key) or 0)
    except (redis_exc.RedisError, Exception) as exc:
        log.error("redis_unavailable", extra={"error": str(exc)})
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")

    return {
        "quota": {
            "limit": key_info["quota_limit"],
            "used": used,
            "remaining": max(0, key_info["quota_limit"] - used)
        },
        "rate_limit": {
            "limit": key_info["rate_limit"],
            "current": current_rate,
            "window": RATE_LIMIT_WINDOW
        }
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

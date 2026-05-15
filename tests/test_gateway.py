"""
Unit tests for cloud_platform/src/gateway/main.py

What "mocking" means in these tests
────────────────────────────────────
The gateway talks to Redis for every auth operation.  We don't want tests to
need a running Redis server, so we replace the Redis client with a fake
object whose methods we control.

  with patch("main.redis_client") as mock_redis:
      mock_redis.hget = AsyncMock(return_value=None)  # "no user found"

`patch` temporarily swaps out the real object; `AsyncMock` is a fake async
function that returns whatever you tell it to.  After the `with` block, the
real object is restored.

How to run:  pytest tests/test_gateway.py -v

Groups:
  1. TestPureFunctions   — functions that need no Redis or HTTP (always run)
  2. TestAuthRoutes      — HTTP routes via FastAPI TestClient (skipped if
                           FastAPI/Starlette version mismatch is present)
"""

import os
import sys
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

# ── Gateway needs JWT_SECRET set before import ───────────────────────────────
# The module raises RuntimeError at import time if it's blank or a known weak value.
os.environ.setdefault("JWT_SECRET", "test-secret-key-for-pytest-min32chars!!")

# ── Add gateway directory to sys.path ────────────────────────────────────────
_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_GATEWAY_DIR = os.path.join(_REPO, "cloud_platform", "src", "gateway")
if _GATEWAY_DIR not in sys.path:
    sys.path.insert(0, _GATEWAY_DIR)

_CLOUD_SRC = os.path.join(_REPO, "cloud_platform", "src")
if _CLOUD_SRC not in sys.path:
    sys.path.insert(0, _CLOUD_SRC)

# ── Try to import gateway module ──────────────────────────────────────────────
try:
    import main as gateway
    _GATEWAY_IMPORT_ERROR = None
except Exception as exc:
    gateway = None
    _GATEWAY_IMPORT_ERROR = str(exc)

_gateway_available = pytest.mark.skipif(
    _GATEWAY_IMPORT_ERROR is not None,
    reason=f"gateway import failed ({_GATEWAY_IMPORT_ERROR}). "
           "Fix: pip install 'starlette>=0.40,<1.0'",
)


# ═══════════════════════════════════════════════════════════════════════════
# 1. Pure functions — no Redis, no HTTP needed
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.skipif(
    _GATEWAY_IMPORT_ERROR is not None,
    reason="gateway not importable",
)
class TestPureFunctions:
    """
    These functions do pure computation (no I/O).
    They're the easiest to test and the fastest to run.
    """

    # ── _make_tokens ─────────────────────────────────────────────────────────

    def test_make_tokens_returns_token_response(self):
        result = gateway._make_tokens("user-123", "alice@example.com")
        assert hasattr(result, "access_token")
        assert hasattr(result, "refresh_token")
        assert result.token_type == "bearer"

    def test_make_tokens_encodes_correct_subject(self):
        import jwt
        result = gateway._make_tokens("user-abc", "alice@example.com")
        payload = jwt.decode(
            result.access_token,
            os.environ["JWT_SECRET"],
            algorithms=["HS256"],
        )
        assert payload["sub"] == "user-abc"
        assert payload["email"] == "alice@example.com"

    def test_make_tokens_email_in_refresh_token(self):
        """Round 3 bug: email was missing from refresh token payload."""
        import jwt
        result = gateway._make_tokens("user-abc", "alice@example.com")
        payload = jwt.decode(
            result.refresh_token,
            os.environ["JWT_SECRET"],
            algorithms=["HS256"],
        )
        assert payload.get("email") == "alice@example.com", (
            "email must be embedded in the refresh token (needed when issuing new access tokens)"
        )

    def test_make_tokens_refresh_has_type_claim(self):
        import jwt
        result = gateway._make_tokens("user-abc", "alice@example.com")
        payload = jwt.decode(
            result.refresh_token,
            os.environ["JWT_SECRET"],
            algorithms=["HS256"],
        )
        assert payload.get("type") == "refresh"

    def test_make_tokens_access_has_no_type_claim(self):
        import jwt
        result = gateway._make_tokens("user-abc", "alice@example.com")
        payload = jwt.decode(
            result.access_token,
            os.environ["JWT_SECRET"],
            algorithms=["HS256"],
        )
        assert "type" not in payload

    def test_make_tokens_each_call_produces_unique_jtis(self):
        import jwt
        r1 = gateway._make_tokens("u1", "a@b.com")
        r2 = gateway._make_tokens("u1", "a@b.com")
        p1 = jwt.decode(r1.access_token, os.environ["JWT_SECRET"], algorithms=["HS256"])
        p2 = jwt.decode(r2.access_token, os.environ["JWT_SECRET"], algorithms=["HS256"])
        assert p1["jti"] != p2["jti"], "Each token must have a unique jti (for revocation)"

    def test_access_token_expires_in_one_hour(self):
        import jwt
        before = int(time.time())
        result = gateway._make_tokens("u", "u@u.com")
        payload = jwt.decode(
            result.access_token,
            os.environ["JWT_SECRET"],
            algorithms=["HS256"],
        )
        assert payload["exp"] - before == pytest.approx(3600, abs=5)

    # ── _seconds_until_month_end ──────────────────────────────────────────────

    def test_month_end_returns_positive_integer(self):
        ttl = gateway._seconds_until_month_end()
        assert isinstance(ttl, int)
        assert ttl > 0

    def test_month_end_at_most_31_days(self):
        ttl = gateway._seconds_until_month_end()
        assert ttl <= 31 * 24 * 3600

    # ── _ALLOWED_PERMISSIONS ─────────────────────────────────────────────────

    def test_allowed_permissions_contains_expected_values(self):
        assert "read"  in gateway._ALLOWED_PERMISSIONS
        assert "write" in gateway._ALLOWED_PERMISSIONS
        assert "admin" in gateway._ALLOWED_PERMISSIONS

    def test_allowed_permissions_has_exactly_three_entries(self):
        assert len(gateway._ALLOWED_PERMISSIONS) == 3


# ═══════════════════════════════════════════════════════════════════════════
# 2. HTTP routes via FastAPI TestClient
# ═══════════════════════════════════════════════════════════════════════════

@_gateway_available
class TestAuthRoutes:
    """
    End-to-end route tests using FastAPI's TestClient.

    TestClient lets you call HTTP routes directly in-process — no real server,
    no network.  It works like requests.get/post but talks to your app.

    Each test patches `main.redis_client` so tests are isolated from each
    other and no Redis server is required.
    """

    @pytest.fixture(autouse=True)
    def mock_redis(self):
        """
        Replace the global redis_client with a MagicMock for every test.

        `autouse=True` means pytest injects this fixture into every test in
        the class automatically — you don't have to list it as a parameter.
        """
        mock = MagicMock()
        # Default: no user exists, no key exists, all writes succeed
        mock.hget     = AsyncMock(return_value=None)
        mock.hgetall  = AsyncMock(return_value={})
        mock.hset     = AsyncMock(return_value=True)
        mock.exists   = AsyncMock(return_value=0)
        mock.setex    = AsyncMock(return_value=True)
        mock.smembers = AsyncMock(return_value=set())
        mock.pipeline = MagicMock()

        # Rate-limit Lua script: return 1 (allowed) by default
        lua_script = MagicMock()
        lua_script.execute = AsyncMock(return_value=1)
        mock.register_script = MagicMock(return_value=lua_script)

        # Stub the MCP HTTP client so provisioning POST is a no-op in tests
        mock_http = MagicMock()
        mock_http.post = AsyncMock(return_value=MagicMock(status_code=200))

        with patch.object(gateway, "redis_client", mock), \
             patch.object(gateway, "_http_client", mock_http), \
             patch.object(gateway, "_rate_limit_script", None), \
             patch.object(gateway, "_quota_script", None):
            yield mock

    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient
        return TestClient(gateway.app)

    @pytest.fixture
    def registered_user(self, mock_redis):
        """
        Pre-configure mock_redis so it looks like alice@example.com is
        already registered with password 'correct-password'.

        bcrypt.hashpw is the slow part — we patch it too so tests run fast.
        """
        import bcrypt
        hashed = bcrypt.hashpw(b"correct-password", bcrypt.gensalt()).decode()
        mock_redis.hget.return_value = "existing-user-id"
        mock_redis.hgetall.return_value = {
            "user_id":       "existing-user-id",
            "email":         "alice@example.com",
            "password_hash": hashed,
        }
        return {"email": "alice@example.com", "password": "correct-password",
                "user_id": "existing-user-id"}

    # ── /register ────────────────────────────────────────────────────────────

    def test_register_success(self, client, mock_redis):
        # No existing user → hget returns None
        mock_redis.hget.return_value = None
        resp = client.post("/api/v1/auth/register", json={
            "email": "new@example.com",
            "password": "strongpassword123",
        })
        assert resp.status_code == 200
        body = resp.json()
        assert "user_id" in body
        assert body["email"] == "new@example.com"

    def test_register_duplicate_email_returns_409(self, client, mock_redis):
        # Simulate existing user
        mock_redis.hget.return_value = "existing-id"
        resp = client.post("/api/v1/auth/register", json={
            "email": "taken@example.com",
            "password": "strongpassword123",
        })
        assert resp.status_code == 409

    def test_register_short_password_returns_400(self, client, mock_redis):
        mock_redis.hget.return_value = None
        resp = client.post("/api/v1/auth/register", json={
            "email": "new@example.com",
            "password": "short",   # < 8 characters
        })
        assert resp.status_code == 400
        assert "8" in resp.json()["detail"]

    # ── /login ───────────────────────────────────────────────────────────────

    def test_login_success_returns_tokens(self, client, registered_user):
        resp = client.post("/api/v1/auth/login", json={
            "email": registered_user["email"],
            "password": registered_user["password"],
        })
        assert resp.status_code == 200
        body = resp.json()
        assert "access_token" in body
        assert "refresh_token" in body
        assert body["token_type"] == "bearer"

    def test_login_wrong_password_returns_401(self, client, registered_user):
        resp = client.post("/api/v1/auth/login", json={
            "email": registered_user["email"],
            "password": "wrong-password",
        })
        assert resp.status_code == 401

    def test_login_unknown_email_returns_401(self, client, mock_redis):
        mock_redis.hgetall.return_value = {}   # no user found
        resp = client.post("/api/v1/auth/login", json={
            "email": "ghost@example.com",
            "password": "anything",
        })
        assert resp.status_code == 401

    # ── /logout ──────────────────────────────────────────────────────────────

    def test_logout_with_valid_token_returns_200(self, client):
        tokens = gateway._make_tokens("u1", "u@u.com")
        resp = client.post(
            "/api/v1/auth/logout",
            headers={"Authorization": f"Bearer {tokens.access_token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["message"] == "Logged out"

    def test_logout_revokes_token_via_redis(self, client, mock_redis):
        """After logout the jti must be written to Redis."""
        tokens = gateway._make_tokens("u1", "u@u.com")
        client.post(
            "/api/v1/auth/logout",
            headers={"Authorization": f"Bearer {tokens.access_token}"},
        )
        mock_redis.setex.assert_called_once()
        call_args = mock_redis.setex.call_args[0]
        assert call_args[0].startswith("jwt_jti:")   # key format
        assert call_args[2] == "1"                   # value

    def test_logout_succeeds_even_when_redis_is_down(self, client, mock_redis):
        """Round 5 fix: Redis error during logout must not return 500."""
        mock_redis.setex = AsyncMock(side_effect=Exception("Redis down"))
        tokens = gateway._make_tokens("u1", "u@u.com")
        resp = client.post(
            "/api/v1/auth/logout",
            headers={"Authorization": f"Bearer {tokens.access_token}"},
        )
        # Must still return 200 — the client is done with the token regardless
        assert resp.status_code == 200

    # ── /refresh ─────────────────────────────────────────────────────────────

    def test_refresh_with_valid_token_returns_new_tokens(self, client, mock_redis):
        mock_redis.exists.return_value = 0  # not revoked
        tokens = gateway._make_tokens("u1", "u@u.com")
        resp = client.post("/api/v1/auth/refresh",
                           json={"refresh_token": tokens.refresh_token})
        assert resp.status_code == 200
        body = resp.json()
        assert "access_token" in body
        assert "refresh_token" in body

    def test_refresh_rejected_when_token_is_revoked(self, client, mock_redis):
        """Core security check: a revoked refresh token must not issue new tokens."""
        mock_redis.exists.return_value = 1  # jti is in the revoked set
        tokens = gateway._make_tokens("u1", "u@u.com")
        resp = client.post("/api/v1/auth/refresh",
                           json={"refresh_token": tokens.refresh_token})
        assert resp.status_code == 401

    def test_refresh_rejected_with_access_token(self, client, mock_redis):
        """Passing an access token instead of a refresh token must be rejected."""
        mock_redis.exists.return_value = 0
        tokens = gateway._make_tokens("u1", "u@u.com")
        resp = client.post("/api/v1/auth/refresh",
                           json={"refresh_token": tokens.access_token})  # wrong token
        assert resp.status_code == 401

    def test_refresh_preserves_email_in_new_access_token(self, client, mock_redis):
        """Round 3 regression guard: email must survive the refresh flow."""
        import jwt as pyjwt
        mock_redis.exists.return_value = 0
        tokens = gateway._make_tokens("u1", "alice@example.com")
        resp = client.post("/api/v1/auth/refresh",
                           json={"refresh_token": tokens.refresh_token})
        assert resp.status_code == 200
        new_payload = pyjwt.decode(
            resp.json()["access_token"],
            os.environ["JWT_SECRET"],
            algorithms=["HS256"],
        )
        assert new_payload["email"] == "alice@example.com"

    # ── /api-keys ────────────────────────────────────────────────────────────

    def test_create_api_key_rejects_empty_name(self, client):
        tokens = gateway._make_tokens("u1", "u@u.com")
        resp = client.post(
            "/api/v1/api-keys",
            json={"name": "   ", "permissions": ["read"]},
            headers={"Authorization": f"Bearer {tokens.access_token}"},
        )
        assert resp.status_code == 400

    def test_create_api_key_rejects_empty_permissions(self, client):
        tokens = gateway._make_tokens("u1", "u@u.com")
        resp = client.post(
            "/api/v1/api-keys",
            json={"name": "my key", "permissions": []},
            headers={"Authorization": f"Bearer {tokens.access_token}"},
        )
        assert resp.status_code == 400

    def test_create_api_key_rejects_unknown_permissions(self, client):
        tokens = gateway._make_tokens("u1", "u@u.com")
        resp = client.post(
            "/api/v1/api-keys",
            json={"name": "my key", "permissions": ["read", "superadmin"]},
            headers={"Authorization": f"Bearer {tokens.access_token}"},
        )
        assert resp.status_code == 400
        assert "superadmin" in resp.json()["detail"]

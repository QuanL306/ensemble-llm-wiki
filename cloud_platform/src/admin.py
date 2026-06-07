#!/usr/bin/env python3
"""
Knowledge Base Cloud Platform — Admin CLI

Manages users and API Keys directly via Redis.
The server does not need to be running.

Usage:
  python admin.py create-key --user <user_id> --name <name>
  python admin.py list-keys [--user <user_id>]
  python admin.py delete-key <key_id>
  python admin.py list-users

Required environment variables:
  ADMIN_TOKEN   Secret token — must be set to authenticate the operator (H7)
  API_KEY_SALT  HMAC salt — must match the gateway's API_KEY_SALT (C1)
  REDIS_URL     Redis connection string (default: redis://localhost:6379)

Optional:
  ADMIN_AUDIT_LOG  Path for append-only audit log (default: /data/admin_audit.log)
"""

import hashlib
import hmac as _hmac
import json
import os
import sys
import uuid
import argparse
import time
import datetime

try:
    import redis
except ImportError:
    print("Error: redis package not installed. Run: pip install redis")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

REDIS_URL        = os.getenv("REDIS_URL", "redis://localhost:6379")
RATE_LIMIT_DEFAULT = int(os.getenv("RATE_LIMIT_DEFAULT", "100"))
QUOTA_DEFAULT    = int(os.getenv("QUOTA_DEFAULT", "10000"))
ADMIN_AUDIT_LOG  = os.getenv("ADMIN_AUDIT_LOG", "/data/admin_audit.log")

# H7: ADMIN_TOKEN — required to authenticate the operator
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")

# C1: API_KEY_SALT — required to hash API keys consistently with the gateway
API_KEY_SALT = os.getenv("API_KEY_SALT", "")


def _check_prerequisites() -> None:
    """Verify required env vars are set before executing any command (H7)."""
    missing = []
    if not ADMIN_TOKEN:
        missing.append("ADMIN_TOKEN")
    if not API_KEY_SALT:
        missing.append("API_KEY_SALT")
    if missing:
        print(
            f"Error: required environment variable(s) not set: {', '.join(missing)}\n"
            "The admin CLI requires these for security. Set them before running.\n"
            "  ADMIN_TOKEN  — operator authentication secret\n"
            "  API_KEY_SALT — must match the gateway's API_KEY_SALT",
            file=sys.stderr,
        )
        sys.exit(1)


def _hash_api_key(raw_key: str) -> str:
    """HMAC-SHA256 of raw_key using API_KEY_SALT — must match gateway logic (C1)."""
    return _hmac.new(API_KEY_SALT.encode(), raw_key.encode(), "sha256").hexdigest()


def _key_fingerprint(api_key_hash: str) -> str:
    """Short fingerprint for safe display — does not leak the plaintext key (C1)."""
    short = hashlib.sha256(api_key_hash.encode()).hexdigest()[:8]
    return f"kb_l…{short}"


def _audit(command: str, details: str = "") -> None:
    """Append a structured audit log entry (H7)."""
    entry = json.dumps({
        "ts":      datetime.datetime.utcnow().isoformat() + "Z",
        "command": command,
        "details": details,
    })
    try:
        os.makedirs(os.path.dirname(ADMIN_AUDIT_LOG), exist_ok=True)
        with open(ADMIN_AUDIT_LOG, "a") as f:
            f.write(entry + "\n")
    except Exception as exc:
        print(f"Warning: could not write audit log ({ADMIN_AUDIT_LOG}): {exc}",
              file=sys.stderr)


# ---------------------------------------------------------------------------
# Redis connection
# ---------------------------------------------------------------------------

def get_redis() -> redis.Redis:
    try:
        r = redis.from_url(REDIS_URL, decode_responses=True)
        r.ping()
        return r
    except redis.ConnectionError:
        print(f"Error: Cannot connect to Redis at {REDIS_URL}")
        print("Make sure Redis is running, or set REDIS_URL environment variable.")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_create_key(args) -> None:
    """Create a new API Key for a user."""
    r = get_redis()

    user_id    = args.user
    key_name   = args.name
    rate_limit = args.rate_limit or RATE_LIMIT_DEFAULT
    quota      = args.quota or QUOTA_DEFAULT

    key_id   = str(uuid.uuid4())
    api_key  = f"kb_live_{uuid.uuid4().hex}"
    key_hash = _hash_api_key(api_key)   # C1: hash before writing to Redis
    created_at = time.strftime("%Y-%m-%dT%H:%M:%SZ")

    # C1: lookup key is the HMAC digest, not the plaintext
    r.hset(f"api_key:{key_hash}", mapping={
        "user_id":     user_id,
        "key_id":      key_id,
        "permissions": "read",
        "rate_limit":  str(rate_limit),
        "quota_limit": str(quota),
        "is_active":   "true",
    })

    # C1: store hash (not plaintext) in metadata for later deactivation
    r.hset(f"api_key_data:{key_id}", mapping={
        "user_id":      user_id,
        "name":         key_name,
        "api_key_hash": key_hash,   # never store the raw key
        "permissions":  "read",
        "rate_limit":   str(rate_limit),
        "quota_limit":  str(quota),
        "created_at":   created_at,
        "is_active":    "true",
    })

    r.sadd(f"user:{user_id}:api_keys", key_id)

    _audit("create-key", f"user={user_id} key_id={key_id} name={key_name!r}")

    print(f"\n✅ API Key created")
    print(f"   User:       {user_id}")
    print(f"   Name:       {key_name}")
    print(f"   Key ID:     {key_id}")
    print(f"   Rate limit: {rate_limit} req/min")
    print(f"   Quota:      {quota} req/month")
    print(f"\n   API Key (save this — shown only once, not stored in Redis):")
    print(f"\n   {api_key}\n")
    print(f"   Fingerprint: {_key_fingerprint(key_hash)}\n")


def cmd_list_keys(args) -> None:
    """List API Keys, optionally filtered by user."""
    r = get_redis()

    if args.user:
        user_ids = [args.user]
    else:
        all_key_data_keys = r.keys("api_key_data:*")
        user_ids = sorted({
            r.hget(k, "user_id")
            for k in all_key_data_keys
            if r.hget(k, "user_id")
        })

    if not user_ids:
        print("No users found.")
        return

    total = 0
    for user_id in user_ids:
        key_ids = r.smembers(f"user:{user_id}:api_keys")
        if not key_ids:
            continue

        print(f"\n👤 {user_id}")
        for key_id in sorted(key_ids):
            data = r.hgetall(f"api_key_data:{key_id}")
            if not data:
                continue

            active  = data.get("is_active") == "true"
            status  = "active" if active else "revoked"
            # C1: display a short fingerprint — never shows the raw key or full hash
            key_hash = data.get("api_key_hash", "")
            masked   = _key_fingerprint(key_hash) if key_hash else "(unknown)"

            print(f"   {'✅' if active else '❌'} {data.get('name', '(unnamed)')}")
            print(f"      Key:        {masked}")
            print(f"      Key ID:     {key_id}")
            print(f"      Status:     {status}")
            print(f"      Rate limit: {data.get('rate_limit')} req/min")
            print(f"      Quota:      {data.get('quota_limit')} req/month")
            print(f"      Created:    {data.get('created_at')}")
            total += 1

    print(f"\nTotal: {total} key(s)")
    _audit("list-keys", f"user_filter={args.user or 'all'}")


def cmd_delete_key(args) -> None:
    """Revoke (deactivate) an API Key by key_id."""
    r = get_redis()

    key_id = args.key_id
    data   = r.hgetall(f"api_key_data:{key_id}")

    if not data:
        print(f"Error: Key ID '{key_id}' not found.")
        sys.exit(1)

    if data.get("is_active") == "false":
        print(f"Key '{data.get('name')}' is already revoked.")
        return

    r.hset(f"api_key_data:{key_id}", "is_active", "false")

    # C1: use stored hash to deactivate the lookup entry
    key_hash = data.get("api_key_hash", "")
    if key_hash:
        r.hset(f"api_key:{key_hash}", "is_active", "false")

    user_id = data.get("user_id")
    if user_id:
        r.srem(f"user:{user_id}:api_keys", key_id)

    _audit("delete-key", f"key_id={key_id} user={user_id} name={data.get('name')!r}")
    print(f"✅ Key '{data.get('name')}' (user: {user_id}) has been revoked.")


def cmd_list_users(args) -> None:
    """List all users who have at least one API Key."""
    r = get_redis()

    user_keys = r.keys("user:*:api_keys")
    if not user_keys:
        print("No users found.")
        return

    print(f"\n{'User ID':<30} {'Keys':>6}")
    print("-" * 38)

    for user_key in sorted(user_keys):
        user_id   = user_key.split(":")[1]
        key_count = r.scard(user_key)
        print(f"{user_id:<30} {key_count:>6}")

    print(f"\nTotal: {len(user_keys)} user(s)")
    _audit("list-users", "")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    # H7: authenticate the operator before parsing or executing any subcommand
    _check_prerequisites()

    parser = argparse.ArgumentParser(
        description="Knowledge Base Cloud Platform — Admin CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python admin.py create-key --user alice --name "Production"
  python admin.py create-key --user alice --name "Dev" --rate-limit 20 --quota 500
  python admin.py list-keys
  python admin.py list-keys --user alice
  python admin.py delete-key <key_id>
  python admin.py list-users
        """
    )

    subparsers = parser.add_subparsers(dest="command", help="Command")

    # create-key
    p_create = subparsers.add_parser("create-key", help="Create a new API Key")
    p_create.add_argument("--user", "-u", required=True, help="User ID")
    p_create.add_argument("--name", "-n", required=True, help="Key name")
    p_create.add_argument("--rate-limit", type=int,
                          help=f"Requests per minute (default: {RATE_LIMIT_DEFAULT})")
    p_create.add_argument("--quota", type=int,
                          help=f"Requests per month (default: {QUOTA_DEFAULT})")

    # list-keys
    p_list = subparsers.add_parser("list-keys", help="List API Keys")
    p_list.add_argument("--user", "-u", help="Filter by user ID")

    # delete-key
    p_delete = subparsers.add_parser("delete-key", help="Revoke an API Key")
    p_delete.add_argument("key_id", help="Key ID to revoke (from list-keys output)")

    # list-users
    subparsers.add_parser("list-users", help="List all users")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    commands = {
        "create-key":  cmd_create_key,
        "list-keys":   cmd_list_keys,
        "delete-key":  cmd_delete_key,
        "list-users":  cmd_list_users,
    }

    commands[args.command](args)


if __name__ == "__main__":
    main()

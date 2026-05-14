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

Environment variables:
  REDIS_URL   Redis connection string (default: redis://localhost:6379)
"""

import os
import sys
import uuid
import argparse
import time

try:
    import redis
except ImportError:
    print("Error: redis package not installed. Run: pip install redis")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Redis connection
# ---------------------------------------------------------------------------

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
RATE_LIMIT_DEFAULT = int(os.getenv("RATE_LIMIT_DEFAULT", "100"))
QUOTA_DEFAULT = int(os.getenv("QUOTA_DEFAULT", "10000"))


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

def cmd_create_key(args):
    """Create a new API Key for a user"""
    r = get_redis()

    user_id = args.user
    key_name = args.name
    rate_limit = args.rate_limit or RATE_LIMIT_DEFAULT
    quota = args.quota or QUOTA_DEFAULT

    key_id = str(uuid.uuid4())
    api_key = f"kb_live_{uuid.uuid4().hex}"
    created_at = time.strftime("%Y-%m-%dT%H:%M:%SZ")

    # Store key lookup (used by gateway to verify requests)
    r.hset(f"api_key:{api_key}", mapping={
        "user_id": user_id,
        "key_id": key_id,
        "permissions": "read",
        "rate_limit": str(rate_limit),
        "quota_limit": str(quota),
        "is_active": "true",
    })

    # Store key metadata (used by list/delete operations)
    r.hset(f"api_key_data:{key_id}", mapping={
        "user_id": user_id,
        "name": key_name,
        "api_key": api_key,
        "permissions": "read",
        "rate_limit": str(rate_limit),
        "quota_limit": str(quota),
        "created_at": created_at,
        "is_active": "true",
    })

    # Add to user's key set
    r.sadd(f"user:{user_id}:api_keys", key_id)

    print(f"\n✅ API Key created")
    print(f"   User:       {user_id}")
    print(f"   Name:       {key_name}")
    print(f"   Key ID:     {key_id}")
    print(f"   Rate limit: {rate_limit} req/min")
    print(f"   Quota:      {quota} req/month")
    print(f"\n   API Key (save this — shown only once):")
    print(f"\n   {api_key}\n")


def cmd_list_keys(args):
    """List API Keys, optionally filtered by user"""
    r = get_redis()

    if args.user:
        user_ids = [args.user]
    else:
        # Collect all users from key data
        all_key_data_keys = r.keys("api_key_data:*")
        user_ids = list({
            r.hget(k, "user_id")
            for k in all_key_data_keys
            if r.hget(k, "user_id")
        })
        user_ids = sorted(user_ids)

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

            active = data.get("is_active") == "true"
            status = "active" if active else "revoked"
            # Mask the actual key value for security
            raw_key = data.get("api_key", "")
            masked = raw_key[:12] + "..." if raw_key else "(unknown)"

            print(f"   {'✅' if active else '❌'} {data.get('name', '(unnamed)')}")
            print(f"      Key:        {masked}")
            print(f"      Key ID:     {key_id}")
            print(f"      Status:     {status}")
            print(f"      Rate limit: {data.get('rate_limit')} req/min")
            print(f"      Quota:      {data.get('quota_limit')} req/month")
            print(f"      Created:    {data.get('created_at')}")
            total += 1

    print(f"\nTotal: {total} key(s)")


def cmd_delete_key(args):
    """Revoke (deactivate) an API Key by key_id"""
    r = get_redis()

    key_id = args.key_id
    data = r.hgetall(f"api_key_data:{key_id}")

    if not data:
        print(f"Error: Key ID '{key_id}' not found.")
        sys.exit(1)

    if data.get("is_active") == "false":
        print(f"Key '{data.get('name')}' is already revoked.")
        return

    # Deactivate
    r.hset(f"api_key_data:{key_id}", "is_active", "false")

    # Also deactivate the lookup entry
    raw_key = data.get("api_key", "")
    if raw_key:
        r.hset(f"api_key:{raw_key}", "is_active", "false")

    # Remove from user's key set so list-users counts are accurate
    user_id = data.get("user_id")
    if user_id:
        r.srem(f"user:{user_id}:api_keys", key_id)

    print(f"✅ Key '{data.get('name')}' (user: {user_id}) has been revoked.")


def cmd_list_users(args):
    """List all users who have at least one API Key"""
    r = get_redis()

    user_keys = r.keys("user:*:api_keys")
    if not user_keys:
        print("No users found.")
        return

    print(f"\n{'User ID':<30} {'Keys':>6}")
    print("-" * 38)

    for user_key in sorted(user_keys):
        user_id = user_key.split(":")[1]
        key_count = r.scard(user_key)
        print(f"{user_id:<30} {key_count:>6}")

    print(f"\nTotal: {len(user_keys)} user(s)")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
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
    p_create.add_argument("--user", "-u", required=True, help="User ID (e.g. alice, client-a)")
    p_create.add_argument("--name", "-n", required=True, help="Key name (e.g. Production, Dev)")
    p_create.add_argument("--rate-limit", type=int, help=f"Requests per minute (default: {RATE_LIMIT_DEFAULT})")
    p_create.add_argument("--quota", type=int, help=f"Requests per month (default: {QUOTA_DEFAULT})")

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
        "create-key": cmd_create_key,
        "list-keys": cmd_list_keys,
        "delete-key": cmd_delete_key,
        "list-users": cmd_list_users,
    }

    commands[args.command](args)


if __name__ == "__main__":
    main()

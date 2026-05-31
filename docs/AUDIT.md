# Codebase Audit Report

**Last updated:** 2026-05-26  
**Scope:** ensemble-llm-wiki — all four components  
**Audit rounds completed:** 6 (2026-05-07 through 2026-05-26)

---

## Summary

The codebase has undergone six rounds of systematic security and quality auditing since 2026-05-07. A total of **~87 defects** have been found and fixed (20 + 18 + 15 + 11 + 10 + 12 = 86, plus one pre-existing test failure). The test suite has grown from ~26 tests (mostly builder utility functions) to **288 tests across 7 files**, covering all four components.

| Round | Date | Issues Fixed | Critical | High | Medium | Low |
|-------|------|-------------|---------|------|--------|-----|
| 1 | 2026-05-07 | 20 | 4 | 7 | 7 | 2 |
| 2 | 2026-05-07 | 18 | 1 | 5 | 8 | 4 |
| 3 | 2026-05-07 | 15 | 2 | 4 | 7 | 2 |
| 4 | 2026-05-07 | 11 | 0 | 2 | 5 | 4 |
| 5 | 2026-05-15 | 4 | 0 | 0 | 2 | 2 |
| 6 | 2026-05-26 | 12 | 4 | 8 | 0 | 0 |
| **Total** | | **80** | **11** | **26** | **29** | **14** |

---

## Component Assessment

### Tier 1 — Cloud Platform (gateway + MCP server)

**Status: HARDENED**

The highest-risk component. Exposed to the network; handles authentication, authorization, rate limiting, and multi-tenant data isolation.

#### Security posture (post round-6)

| Control | Status | Detail |
|---------|--------|--------|
| API key storage | ✅ HMAC-SHA256 | Raw key never stored in Redis; `API_KEY_SALT` required at startup |
| JWT algorithm | ✅ Hard-coded HS256 | Algorithm not read from token header or env; iss/aud/type validated |
| JWT secret strength | ✅ Entropy check | Shannon entropy < 3.5 or length < 32 → RuntimeError on startup |
| Refresh token rotation | ✅ Single-use | Consumed jti recorded; replay triggers `_revoke_all_sessions` |
| Access/refresh token types | ✅ Enforced | `type="access"` claim required; wrong type → 401 |
| Login brute-force | ✅ Rate limited | Per-IP (60/min) + per-email (10/min) before bcrypt |
| Login timing | ✅ Constant-time | `_DUMMY_HASH` ensures bcrypt always runs regardless of email existence |
| Internal token | ✅ Constant-time | `hmac.compare_digest` for `X-Internal-Token` comparison |
| Header spoofing | ✅ Stripped | `_BLOCKED_INBOUND_HEADERS` middleware strips `X-User-ID`, `X-Permissions`, etc. |
| Permission model | ✅ `{read, write}` | `"admin"` removed; MCP enforces at tool dispatch |
| Per-user key cap | ✅ Max 10 keys | Plus hourly creation rate limit (5/hour) |
| Aggregate quota | ✅ Per-user/month | `quota_user:{user_id}:{month}` cross-key tracking |
| Admin CLI auth | ✅ `ADMIN_TOKEN` | + `API_KEY_SALT` required; append-only audit log |
| CORS | ✅ Allowlist | `CORS_ORIGINS` env var; `"*"` blocked in production startup |
| Request size | ✅ 10 MB cap | Chunked-encoding bypass also closed |
| Write concurrency | ✅ Per-KB locks | `asyncio.Lock` per `(user_id, kb_id)` with bounded lock registry |
| Redis error handling | ✅ Wrapped | All Redis calls have try/except; auth fails open (not hard-crash) |
| Connection pooling | ✅ Singleton | `_http_client` created in lifespan, shared across proxy requests |
| Path traversal | ✅ Guarded | `wiki_path` resolution validated with `relative_to` containment check |
| LRU caches | ✅ Bounded | All caches are `OrderedDict` with `move_to_end` on hit, `popitem` on eviction |

#### Outstanding concerns

- **No TLS termination in this repo** — assumed handled by reverse proxy (Nginx/Caddy). Deployment without TLS exposes JWT tokens in transit.
- **Redis has no auth config in docker-compose** — `requirepass` not set. Must be added before any non-localhost deployment.
- **JWT_SECRET still loaded from env** — recommended to load from a secrets manager (Vault, AWS Secrets Manager) in production.

#### Test coverage

| File | Tests | Coverage areas |
|------|-------|----------------|
| `tests/test_gateway.py` | 31 | JWT generation/validation, login/register, API key CRUD, logout, rate limits |
| `tests/test_cloud.py` | 30 | MCP tools, KB access control, shared KB management, owner migration, rate limits |

---

### Tier 2 — Builder Core

**Status: GOOD — newly tested**

The document processing pipeline. Runs locally (cron/agent), not network-exposed. Primary risks: file I/O correctness, pipeline ordering, LLM call reliability.

#### Quality posture

| Control | Status | Detail |
|---------|--------|--------|
| Registry atomic writes | ✅ Atomic | `mkstemp` + `Path.replace()` — no partial writes |
| Pipeline stage ordering | ✅ Enforced | `graphify-first` (PDF/EPUB) vs `compile-first` (MD) — tested end-to-end |
| Lifecycle FSM | ✅ Correct | Invalid transitions raise ValueError; auto-advance tested at thresholds |
| LLM retry | ✅ Backoff | `LLMTransientError` vs `LLMPermanentError` distinction; max_retries respected |
| Credential scrubbing | ✅ Regex | Transcript harvester redacts API keys, passwords, Bearer tokens before disk write |
| Graphify guard | ✅ Enforced | `compile-llm` requires `edges.jsonl` to exist; `--skip-graphify-check` bypass available |
| CrossRef CJK skip | ✅ Fixed | CJK filenames bypass CrossRef lookup (returns garbage for Chinese characters) |

#### Test coverage

| File | Tests | Coverage areas |
|------|-------|----------------|
| `tests/test_builder.py` | 26 | Retrieval query extraction, chunking, compile retry, credential scrubbing |
| `tests/test_builder_core.py` | 82 | Lifecycle FSM, registry, pipeline detection, LLM backend detection, ingest, kbapi |
| `tests/test_scoring.py` | 40 | Keyword splitting, IDF, TF scoring, hybrid scoring, concept boost |

---

### Tier 2 — Dashboard

**Status: GOOD — newly tested**

Local-only FastAPI dashboard. Read-only; binds to `127.0.0.1` by default.

#### Quality posture

| Control | Status | Detail |
|---------|--------|--------|
| Localhost binding | ✅ Default | `--host 0.0.0.0` triggers stderr warning |
| Graph file serving | ✅ Guarded | `graph.html` path validated with `relative_to` before `FileResponse` |
| Graph cache | ✅ mtime-based | Cache invalidated when `graph.json` mtime changes |
| KB_ROOT | ✅ Configurable | `$KB_ROOT` env var or `--kb-root` CLI flag; was a hardcoded TODO path |
| CLI args | ✅ Full argparse | Port range 1024–65535 enforced; `--host`, `--kb-root` as proper flags |

#### Test coverage

| File | Tests | Coverage areas |
|------|-------|----------------|
| `tests/test_dashboard.py` | 30 | Graph analytics, cache, KB discovery, argparse, route smoke tests |

---

### Tier 3 — Local MCP Server

**Status: GOOD — newly tested**

Local stdio MCP server. Runs as a subprocess of the AI client; file access scoped to declared KB root.

#### Quality posture

| Control | Status | Detail |
|---------|--------|--------|
| Path traversal (`wiki_path`) | ✅ Guarded | `candidate.resolve().relative_to(kb_path.resolve())` — escape raises ValueError, skipped |
| Path traversal (resource URI) | ✅ Guarded | `articles/` and `concepts/` URI names validated same way |
| Fuzzy KB name match | ✅ Removed | Unknown `kb_name` raises ValueError immediately (no substring fallback) |
| LRU caches | ✅ Bounded | `_index_cache`, `_concepts_cache`, `_embeddings_cache` are `OrderedDict` with `_KB_CACHE_MAX = 50` |
| Index cache invalidation | ✅ mtime-based | Stale index auto-reloads when file changes on disk |
| Synthesis file writing | ✅ Slug-validated | Question slugified; empty question/answer rejected |

#### Outstanding concerns

- **No output size cap on `kb_get_document`** — very large articles (200K+ word compilations) are returned in full. Could be mitigated by a `max_chars` parameter.
- **`sys.exit(1)` in constructor** — if no KBs are found at startup, the server exits hard. A warning + graceful degradation would be more robust in production wrappers.

#### Test coverage

| File | Tests | Coverage areas |
|------|-------|----------------|
| `tests/test_local_server.py` | 49 | KB discovery, resolve, cache, syntheses, all 5 tool handlers, path traversal |

---

## Test Suite Overview

```
tests/test_builder.py         26  builder utility functions
tests/test_builder_core.py    82  builder core: lifecycle, registry, pipeline, kbapi
tests/test_cloud.py           30  cloud platform MCP tools + access control
tests/test_dashboard.py       30  dashboard analytics, CLI, routes
tests/test_gateway.py         31  API gateway: auth, JWT, rate limits, CRUD
tests/test_local_server.py    49  local MCP server: tools, cache, path traversal
tests/test_scoring.py         40  retrieval scoring model
────────────────────────────────
Total                        288  (0 failures, 0 skips)
```

Run: `python -m pytest tests/ -v`  
Time: ~6s on a 2023 MacBook Pro M2.

---

## Areas With No Automated Tests

| Area | Risk | Notes |
|------|------|-------|
| `transcript_harvester.py` | Low | Logic is simple (read JSONL, write MD); manually verified |
| `graphify_integration.py` | Low | Thin wrapper over `graphify` subprocess; integration-tested via cron |
| `exports.py` | Low | File generation; output format manually verified |
| `chaptered.py` | Low | Covered indirectly by `test_builder.py` compile tests |
| `contradictions.py` | Medium | No unit tests; negation pair logic could have edge cases |
| `session_start.py` | Low | Integration-only; end-to-end tested via cron |
| Cloud platform `mcp_http_server.py` write handlers | Medium | `kb_write_article`, `kb_append_note`, `kb_update_index` — tested via test_cloud.py MCP call path but file-write assertions limited |

---

## Recommended Next Steps

1. **Add `contradictions.py` tests** — the negation-pair matching is the most logic-heavy untested module.
2. **Redis auth in docker-compose** — set `requirepass` before any non-localhost deployment.
3. **TLS documentation** — add a deployment guide section making TLS requirement explicit.
4. **`kb_get_document` output cap** — add `max_chars` parameter to prevent token-budget issues with large documents.
5. **Shannon entropy test for `API_KEY_SALT`** — the same startup entropy check applied to `JWT_SECRET` should also apply to `API_KEY_SALT`.

"""
Multi-backend LLM client for knowledge-base-suite-en.

Supports 7 providers with a unified interface. Zero external SDK deps
(uses stdlib urllib). All providers compatible with OpenAI-style
/v1/chat/completions share a single code path.

Backends:
    claude    — Anthropic Messages API
    openai    — OpenAI Chat Completions
    deepseek  — DeepSeek (OpenAI-compat)
    kimi      — Moonshot (OpenAI-compat)
    zhipu     — Zhipu GLM (OpenAI-compat)
    minimax   — MiniMax Chat Completion v2
    gemini    — Google Generative Language API
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

# ── Backend definitions ──────────────────────────────────────────

BACKENDS: Dict[str, Dict[str, Any]] = {
    "claude": {
        "base_url": "https://api.anthropic.com/v1/messages",
        "api_key_env": "ANTHROPIC_API_KEY",
        "model": "claude-sonnet-4-20250514",
        "provider": "anthropic",
    },
    "openai": {
        "base_url": "https://api.openai.com/v1/chat/completions",
        "api_key_env": "OPENAI_API_KEY",
        "model": "gpt-4o",
        "provider": "openai-compat",
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1/chat/completions",
        "api_key_env": "DEEPSEEK_API_KEY",
        "model": "deepseek-chat",
        "provider": "openai-compat",
    },
    "kimi": {
        "base_url": "https://api.moonshot.cn/v1/chat/completions",
        "api_key_env": "MOONSHOT_API_KEY",
        "model": "moonshot-v1-8k",
        "provider": "openai-compat",
    },
    "zhipu": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4/chat/completions",
        "api_key_env": "ZHIPU_API_KEY",
        "model": "glm-4-flash",
        "provider": "openai-compat",
    },
    "minimax": {
        "base_url": "https://api.minimax.chat/v1/text/chatcompletion_v2",
        "api_key_env": "MINIMAX_API_KEY",
        "model": "abab6.5s-chat",
        "provider": "minimax",
    },
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent",
        "api_key_env": "GEMINI_API_KEY",
        "model": "gemini-2.0-flash",
        "provider": "gemini",
    },
}

# Priority order for auto-detection
_AUTO_PRIORITY = ["deepseek", "openai", "kimi", "claude", "gemini", "zhipu", "minimax"]


# ── Public API ───────────────────────────────────────────────────

def detect_backend() -> Optional[str]:
    """Auto-detect which backend has an API key available."""
    for name in _AUTO_PRIORITY:
        cfg = BACKENDS[name]
        if os.environ.get(cfg["api_key_env"]):
            return name
    return None


def list_available() -> List[str]:
    """List backends with API keys available."""
    return [name for name in _AUTO_PRIORITY
            if os.environ.get(BACKENDS[name]["api_key_env"])]


def chat(
    prompt: str,
    backend: Optional[str] = None,
    model: Optional[str] = None,
    system: Optional[str] = None,
    temperature: float = 0.3,
    max_tokens: int = 1024,
) -> Dict[str, Any]:
    """
    Send a single-turn chat completion.

    Args:
        prompt: User message
        backend: Provider name. Auto-detected if None.
        model: Override default model for the backend.
        system: Optional system prompt.
        temperature: Sampling temperature (0.0-2.0).
        max_tokens: Max tokens to generate.

    Returns:
        {"content": "...", "input_tokens": N, "output_tokens": N, "backend": "..."}
    """
    if backend is None:
        backend = detect_backend()
    if backend is None:
        raise RuntimeError(
            "No LLM API key found. Set one of: "
            + ", ".join(cfg["api_key_env"] for cfg in BACKENDS.values())
        )

    cfg = BACKENDS[backend]
    api_key = os.environ.get(cfg["api_key_env"], "")
    if not api_key:
        raise RuntimeError(f"{cfg['api_key_env']} is not set for backend '{backend}'")

    actual_model = model or cfg["model"]
    provider = cfg["provider"]

    try:
        if provider == "openai-compat":
            result = _call_openai_compat(
                cfg["base_url"], api_key, actual_model,
                prompt, system, temperature, max_tokens, backend,
            )
        elif provider == "anthropic":
            result = _call_anthropic(
                cfg["base_url"], api_key, actual_model,
                prompt, system, temperature, max_tokens,
            )
        elif provider == "gemini":
            result = _call_gemini(
                cfg["base_url"], api_key, actual_model,
                prompt, system, temperature, max_tokens,
            )
        elif provider == "minimax":
            result = _call_minimax(
                cfg["base_url"], api_key, actual_model,
                prompt, system, temperature, max_tokens,
            )
        else:
            raise ValueError(f"Unknown provider: {provider}")

        result["backend"] = backend
        return result

    except Exception as e:
        raise RuntimeError(f"[{backend}] LLM call failed: {e}") from e


# ── Retry helper ─────────────────────────────────────────────────

def _retry_with_backoff(fn, max_attempts=3, base_delay=2.0):
    """Retry with exponential backoff on transient HTTP errors.

    Retries on: 429 (rate limit), 5xx (server error), URLError (network).
    Does NOT retry on: 4xx (except 429), JSON parse errors.
    """
    last_err = None
    for attempt in range(max_attempts):
        try:
            return fn()
        except urllib.error.HTTPError as e:
            if e.code == 429 or 500 <= e.code < 600:
                last_err = e
                if attempt < max_attempts - 1:
                    delay = base_delay * (2 ** attempt)
                    print(f"[llm] HTTP {e.code}, retrying in {delay:.0f}s "
                          f"(attempt {attempt + 1}/{max_attempts})",
                          file=sys.stderr)
                    time.sleep(delay)
                else:
                    raise
            else:
                raise
        except (urllib.error.URLError, OSError) as e:
            last_err = e
            if attempt < max_attempts - 1:
                delay = base_delay * (2 ** attempt)
                print(f"[llm] Network error, retrying in {delay:.0f}s "
                      f"(attempt {attempt + 1}/{max_attempts})",
                      file=sys.stderr)
                time.sleep(delay)
            else:
                raise
    raise last_err  # type: ignore[misc]


# ── Provider implementations ─────────────────────────────────────

def _call_openai_compat(
    base_url: str,
    api_key: str,
    model: str,
    prompt: str,
    system: Optional[str],
    temperature: float,
    max_tokens: int,
    backend: str,
) -> Dict[str, Any]:
    """OpenAI-compatible /v1/chat/completions (openai, deepseek, kimi, zhipu)."""
    messages: List[Dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    body = json.dumps({
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }).encode("utf-8")

    req = urllib.request.Request(
        base_url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )

    def _do_request():
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read())

    data = _retry_with_backoff(_do_request)

    return {
        "content": data["choices"][0]["message"]["content"],
        "input_tokens": data.get("usage", {}).get("prompt_tokens", 0),
        "output_tokens": data.get("usage", {}).get("completion_tokens", 0),
    }


def _call_anthropic(
    base_url: str,
    api_key: str,
    model: str,
    prompt: str,
    system: Optional[str],
    temperature: float,
    max_tokens: int,
) -> Dict[str, Any]:
    """Anthropic Messages API."""
    messages: List[Dict[str, Any]] = [{"role": "user", "content": prompt}]
    body_dict: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if system:
        body_dict["system"] = system

    body = json.dumps(body_dict).encode("utf-8")

    req = urllib.request.Request(
        base_url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
    )

    def _do_request():
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read())

    data = _retry_with_backoff(_do_request)

    content_blocks = data.get("content", [])
    text = "".join(
        block.get("text", "") for block in content_blocks
        if block.get("type") == "text"
    )

    return {
        "content": text,
        "input_tokens": data.get("usage", {}).get("input_tokens", 0),
        "output_tokens": data.get("usage", {}).get("output_tokens", 0),
    }


def _call_gemini(
    base_url: str,
    api_key: str,
    model: str,
    prompt: str,
    system: Optional[str],
    temperature: float,
    max_tokens: int,
) -> Dict[str, Any]:
    """Google Gemini generateContent API."""
    url = f"{base_url}?key={api_key}"

    contents: List[Dict[str, Any]] = []
    if system:
        # Inject system prompt as a user→model exchange (Gemini lacks
        # native system prompt in v1beta; revisit when GA adds it).
        contents.append({"role": "user", "parts": [{"text": system}]})
        contents.append({"role": "model", "parts": [{"text": "Understood."}]})
    contents.append({"role": "user", "parts": [{"text": prompt}]})

    body = json.dumps({
        "contents": contents,
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
        },
    }).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
    )

    def _do_request():
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read())

    data = _retry_with_backoff(_do_request)

    candidates = data.get("candidates", [])
    text = ""
    if candidates:
        parts = candidates[0].get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts)

    return {
        "content": text,
        "input_tokens": data.get("usageMetadata", {}).get("promptTokenCount", 0),
        "output_tokens": data.get("usageMetadata", {}).get("candidatesTokenCount", 0),
    }


def _call_minimax(
    base_url: str,
    api_key: str,
    model: str,
    prompt: str,
    system: Optional[str],
    temperature: float,
    max_tokens: int,
) -> Dict[str, Any]:
    """MiniMax Chat Completion v2 API."""
    messages: List[Dict[str, str]] = []
    if system:
        messages.append({"sender_type": "BOT", "sender_name": "system", "text": system})
    messages.append({"sender_type": "USER", "sender_name": "user", "text": prompt})

    body = json.dumps({
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }).encode("utf-8")

    req = urllib.request.Request(
        base_url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )

    def _do_request():
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read())

    data = _retry_with_backoff(_do_request)

    # MiniMax v2 returns {"reply": "text"} for simple responses.
    # Some models return OpenAI-compat format {"choices": [...]}.
    reply = data.get("reply")
    if isinstance(reply, str) and reply:
        content = reply
    elif "choices" in data and data["choices"]:
        content = data["choices"][0].get("message", {}).get("content", "")
    else:
        # Unknown format — return raw JSON so caller can debug
        content = json.dumps(data, ensure_ascii=False)
        print(f"[llm] MiniMax: unrecognized response format, keys={list(data.keys())[:5]}",
              file=sys.stderr)

    # MiniMax v2 API returns total_tokens (not split). We report
    # total as input_tokens and flag the estimate.
    total = data.get("usage", {}).get("total_tokens", 0)
    return {
        "content": content,
        "input_tokens": total,
        "output_tokens": 0,
        "_tokens_estimated": True,
    }


# ── Compatibility layer (drop-in for old utils/llm_client callers) ─

def has_api_key() -> bool:
    """Check if any LLM API key is configured (compat)."""
    return detect_backend() is not None


def chat_create(
    prompt: str,
    backend: Optional[str] = None,
    model: Optional[str] = None,
    system: Optional[str] = None,
    max_tokens: int = 1000,
    temperature: float = 0.3,
) -> str:
    """Non-streaming call, returns text only (compat with old chat_create).

    This replaces the SDK-based utils/llm_client.chat_create().
    """
    result = chat(
        prompt, backend=backend, model=model, system=system,
        max_tokens=max_tokens, temperature=temperature,
    )
    return result["content"]


def chat_stream(
    prompt: str,
    backend: Optional[str] = None,
    model: Optional[str] = None,
    system: Optional[str] = None,
    max_tokens: int = 4096,
    temperature: float = 0.3,
) -> str:
    """Streaming-compatible call (prints progress, returns full text).

    Currently non-streaming under the hood (urllib doesn't expose
    chunked transfer incrementally). Calls chat() with a progress
    indicator.
    """
    label = f"{backend or detect_backend() or 'auto'}"
    print(f"[llm] Calling {label} ({model or 'default'})...", file=sys.stderr)
    result = chat(
        prompt, backend=backend, model=model, system=system,
        max_tokens=max_tokens, temperature=temperature,
    )
    print(f"[llm] Done ({result.get('input_tokens', '?')} in / "
          f"{result.get('output_tokens', '?')} out tokens)", file=sys.stderr)
    return result["content"]


# ── High-level helpers ───────────────────────────────────────────

def compile_concepts(
    article_text: str,
    article_name: str,
    backend: Optional[str] = None,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Extract concepts + summary from an article via LLM.

    Returns parsed JSON: {"concepts": [...], "summary": "...", "topics": [...]}
    """
    system = (
        "You are a knowledge base compiler. Extract structured metadata from documents. "
        "Respond ONLY with valid JSON, no markdown, no explanation."
    )
    prompt = f"""Analyze the following article and extract:

1. **concepts**: 5-10 key concepts or named entities mentioned (proper nouns, technical terms, frameworks)
2. **summary**: A 2-3 sentence summary of the article
3. **topics**: 3-5 high-level topic categories

Article name: {article_name}

Article text:
{article_text[:8000]}

Respond ONLY with this JSON structure:
{{"concepts": ["concept1", "concept2", ...], "summary": "...", "topics": ["topic1", "topic2", ...]}}"""

    result = chat(prompt, backend=backend, model=model, system=system,
                  temperature=0.2, max_tokens=1024)

    try:
        # Strip markdown fences if present
        content = result["content"].strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[-1]
            if content.endswith("```"):
                content = content[:-3]
        data = json.loads(content)
        return {**data, "_backend": result["backend"],
                "_tokens": {"in": result["input_tokens"], "out": result["output_tokens"]}}
    except json.JSONDecodeError:
        print(f"[llm] JSON parse failed for {article_name}, raw: {result['content'][:200]}",
              file=sys.stderr)
        return {
            "concepts": [],
            "summary": result["content"][:300],
            "topics": [],
            "_backend": result["backend"],
            "_tokens": {"in": result["input_tokens"], "out": result["output_tokens"]},
            "_parse_error": True,
        }


def merge_concepts(
    all_doc_concepts: List[Dict[str, Any]],
    backend: Optional[str] = None,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Cross-document concept merging: deduplicate, group, and build
    inter-document concept links.
    """
    concept_text = "\n".join(
        f"From '{doc.get('name', '?')}': {json.dumps(doc.get('concepts', []))}"
        for doc in all_doc_concepts
    )

    system = (
        "You are a knowledge base compiler. Merge and deduplicate concepts across "
        "documents. Respond ONLY with valid JSON, no markdown, no explanation."
    )
    prompt = f"""Given concepts extracted from multiple documents, merge and deduplicate them:

1. **concepts**: Deduplicated list of unique concepts across all documents. Merge similar concepts (e.g. "Vote Propensity Model" and "Voter Propensity" → "Vote Propensity Model").
2. **cross_refs**: For each pair of documents that share 2+ concepts, note the shared concepts.

{concept_text}

Respond ONLY with:
{{"concepts": ["merged concept1", ...], "cross_refs": [{{"docs": ["docA", "docB"], "shared": ["conceptX"]}}, ...]}}"""

    result = chat(prompt, backend=backend, model=model, system=system,
                  temperature=0.2, max_tokens=2048)

    try:
        content = result["content"].strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[-1]
            if content.endswith("```"):
                content = content[:-3]
        return json.loads(content)
    except json.JSONDecodeError:
        print(f"[llm] JSON parse failed for merge: {result['content'][:200]}",
              file=sys.stderr)
        return {"concepts": [], "cross_refs": [], "_parse_error": True}

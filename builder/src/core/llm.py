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

    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read())

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

    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read())

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

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        # Try alternate endpoint format (some Gemini setups use different URL)
        body_text = e.read().decode()
        raise RuntimeError(f"Gemini API error: {body_text}") from e

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

    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read())

    reply = data.get("reply", "")
    # MiniMax returns reply as string or doesn't include usage
    return {
        "content": reply if isinstance(reply, str) else data.get("choices", [{}])[0].get("message", {}).get("content", ""),
        "input_tokens": data.get("usage", {}).get("total_tokens", 0) // 2,
        "output_tokens": data.get("usage", {}).get("total_tokens", 0) // 2,
    }


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

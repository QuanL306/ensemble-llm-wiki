"""
Unified LLM client abstraction.

Supports multiple providers via a single interface:
  - Claude (Anthropic)  — LLM_PROVIDER=claude (default)
  - Kimi  (Moonshot AI) — LLM_PROVIDER=kimi

Environment variables:
  LLM_PROVIDER     "claude" or "kimi" (default: "claude")
  ANTHROPIC_API_KEY  Claude API key (required when provider=claude)
  KIMI_API_KEY       Kimi API key (required when provider=kimi)
"""

import os
import sys


def get_llm_config() -> dict:
    """Read provider/key/model from environment. Exit with message if key missing."""
    provider = os.getenv("LLM_PROVIDER", "claude").lower()

    if provider == "kimi":
        key = os.getenv("KIMI_MOONSHOT_API_KEY") or os.getenv("KIMI_API_KEY")
        if not key:
            print("❌  KIMI_MOONSHOT_API_KEY or KIMI_API_KEY is not set.")
            print("    Export it:   export KIMI_API_KEY=sk-...")
            sys.exit(1)
        return {
            "provider": "kimi",
            "api_key": key,
            "model": "kimi-k2.6",
            "aux_model": "kimi-k2.6",
            "base_url": "https://api.moonshot.cn/v1",
        }
    else:
        key = os.getenv("ANTHROPIC_API_KEY")
        if not key:
            print("❌  ANTHROPIC_API_KEY is not set.")
            print("    Export it:   export ANTHROPIC_API_KEY=sk-ant-...")
            sys.exit(1)
        return {
            "provider": "claude",
            "api_key": key,
            "model": "claude-sonnet-4-5",
            "aux_model": "claude-haiku-4-5",
        }


def create_client(config: dict):
    """Return (client, provider_name).

    For Kimi: returns an OpenAI client pointing at Moonshot's base_url.
    For Claude: returns an anthropic.Anthropic client.
    """
    if config["provider"] == "kimi":
        from openai import OpenAI
        return OpenAI(
            api_key=config["api_key"],
            base_url=config.get("base_url", "https://api.moonshot.cn/v1"),
        ), "kimi"
    else:
        import anthropic
        return anthropic.Anthropic(api_key=config["api_key"]), "claude"


def stream_message(client, provider, model, prompt, max_tokens=4096) -> str:
    """Streaming call. Prints dots as tokens arrive. Returns full text."""
    if provider == "kimi":
        resp = client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            stream=True,
            messages=[{"role": "user", "content": prompt}],
        )
        text_parts = []
        for chunk in resp:
            delta = chunk.choices[0].delta.content
            if delta:
                text_parts.append(delta)
                print(".", end="", flush=True)
        print()
        return "".join(text_parts)
    else:
        full = []
        with client.messages.stream(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            for t in stream.text_stream:
                full.append(t)
                print(".", end="", flush=True)
        print()
        try:
            usage = stream.get_final_message().usage
            print(
                f"   [tokens: {usage.input_tokens} in / "
                f"{usage.output_tokens} out]",
                file=sys.stderr,
            )
        except Exception:
            pass
        return "".join(full)


def chat_create(client, provider, model, prompt, max_tokens=1000) -> str:
    """Non-streaming call (for servers). Returns response text."""
    if provider == "kimi":
        resp = client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.choices[0].message.content
    else:
        msg = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text


def chat_create_with_usage(client, provider, model, prompt, max_tokens=1000):
    """Non-streaming call. Returns (text, usage_dict)."""
    if provider == "kimi":
        resp = client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        usage = {
            "input_tokens": resp.usage.prompt_tokens,
            "output_tokens": resp.usage.completion_tokens,
        }
        return resp.choices[0].message.content, usage
    else:
        msg = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        usage = {
            "input_tokens": msg.usage.input_tokens,
            "output_tokens": msg.usage.output_tokens,
        }
        return msg.content[0].text, usage


def get_retry_exceptions(provider):
    """Return (RateLimitError, (conn_errors...), APIStatusError) for retry logic."""
    if provider == "kimi":
        from openai import (
            APIConnectionError,
            APIStatusError,
            RateLimitError,
        )
        return RateLimitError, (APIConnectionError,), APIStatusError
    else:
        import anthropic
        return (
            anthropic.RateLimitError,
            (anthropic.APIConnectionError, anthropic.APITimeoutError),
            anthropic.APIStatusError,
        )


def has_api_key() -> bool:
    """Check if any LLM API key is configured."""
    provider = os.getenv("LLM_PROVIDER", "claude").lower()
    if provider == "kimi":
        return bool(os.getenv("KIMI_API_KEY"))
    return bool(os.getenv("ANTHROPIC_API_KEY"))


def get_model_display() -> str:
    """Return a human-readable provider+model string for logging."""
    provider = os.getenv("LLM_PROVIDER", "claude").lower()
    if provider == "kimi":
        return "Kimi (moonshot-v1-128k)"
    return "Claude (sonnet-4-5)"

"""Model-provider layer: a thin wrapper around the Anthropic SDK (the LLM each agent reasons with).

This is the single point of model access. The agent *loop* that turns a model into a tool-using
agent lives separately in `app/harness.py`.
"""
from typing import Type, TypeVar

from pydantic import BaseModel

from .config import CLAUDE_FALLBACKS, CLAUDE_MODEL

T = TypeVar("T", bound=BaseModel)
_client = None


class LLMRefusal(RuntimeError):
    pass


def client():
    global _client
    if _client is None:
        import os

        import anthropic

        # Behind a TLS-intercepting proxy the Anthropic SDK (httpx) must be told the proxy's root CA.
        # httpx ignores SSL_CERT_FILE, so build an explicit context when CLOUDSHOP_EXTRA_CA /
        # SSL_CERT_FILE points at a bundle (same override the OpenAI-compatible path honors).
        ca = os.getenv("CLOUDSHOP_EXTRA_CA") or os.getenv("SSL_CERT_FILE")
        if ca and os.path.exists(ca):
            import httpx

            _client = anthropic.Anthropic(http_client=httpx.Client(verify=_https_ctx()))
        else:
            _client = anthropic.Anthropic()
    return _client


def _extra() -> dict:
    """Opt into server-side refusal fallbacks (beta) so a classifier false positive
    reroutes to another model instead of failing the shopper's request."""
    if not CLAUDE_FALLBACKS:
        return {}
    return {
        "extra_headers": {"anthropic-beta": "server-side-fallback-2026-07-01"},
        "extra_body": {"fallbacks": "default"},
    }


def _check(resp) -> None:
    if resp.stop_reason == "refusal":
        raise LLMRefusal("Claude declined this request.")


def parse(system: str, user: str, schema: Type[T], max_tokens: int = 16000) -> T:
    """Single structured-output call: returns a validated pydantic instance (Claude or Ollama)."""
    from .config import provider

    p = provider()
    if p == "ollama":
        return _ollama_parse(system, user, schema)
    if p == "openai":
        return _openai_parse(system, user, schema, max_tokens)
    resp = client().messages.parse(
        model=CLAUDE_MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
        output_format=schema,
        **_extra(),
    )
    _check(resp)
    return resp.parsed_output


def _ollama_parse(system: str, user: str, schema: Type[T]) -> T:
    """Structured output from a local Ollama model, using Ollama's JSON-schema `format` mode."""
    import json
    import urllib.request

    from .config import OLLAMA_MODEL, OLLAMA_URL

    body = json.dumps({
        "model": OLLAMA_MODEL,
        "stream": False,
        "format": schema.model_json_schema(),
        "options": {"temperature": 0.2},
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }).encode()
    req = urllib.request.Request(OLLAMA_URL + "/api/chat", data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        resp = json.loads(urllib.request.urlopen(req, timeout=180).read())
    except Exception as e:
        raise RuntimeError(f"Could not reach Ollama at {OLLAMA_URL} (is it running? `ollama serve`): {e}")
    return schema.model_validate_json(resp["message"]["content"])


def _https_ctx():
    """Build a verifying TLS context. Prefers certifi's public CA bundle; if the network does
    TLS interception (a corporate/campus proxy), set SSL_CERT_FILE or CLOUDSHOP_EXTRA_CA to the
    proxy's root CA .pem so the call can verify instead of failing with CERTIFICATE_VERIFY_FAILED."""
    import os
    import ssl

    override = os.getenv("SSL_CERT_FILE") or os.getenv("CLOUDSHOP_EXTRA_CA")
    if override and os.path.exists(override):
        return ssl.create_default_context(cafile=override)
    try:
        import certifi

        ctx = ssl.create_default_context(cafile=certifi.where())
    except Exception:
        ctx = ssl.create_default_context()
    extra = os.getenv("CLOUDSHOP_EXTRA_CA")
    if extra and os.path.exists(extra):
        ctx.load_verify_locations(cafile=extra)
    return ctx


def _openai_parse(system: str, user: str, schema: Type[T], max_tokens: int = 4000) -> T:
    """Structured output from any OpenAI-compatible endpoint (OpenAI / OpenRouter / Azure / Groq / proxy).

    `max_tokens` is capped and ALWAYS sent: OpenRouter reserves the worst-case request cost up front
    (prompt + max output) against your balance, so without a cap it reserves the model's full output
    and a near-empty balance is rejected with HTTP 402 even though the actual call costs cents."""
    import json
    import urllib.request

    from .config import OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL

    sys2 = (system + "\n\nReturn ONLY a single JSON object matching this JSON schema "
            "(no prose, no markdown, no code fences):\n" + json.dumps(schema.model_json_schema()))
    body = json.dumps({
        "model": OPENAI_MODEL,
        "temperature": 0.2,
        "max_tokens": min(max_tokens, 4000),
        "response_format": {"type": "json_object"},
        "messages": [{"role": "system", "content": sys2}, {"role": "user", "content": user}],
    }).encode()
    req = urllib.request.Request(
        OPENAI_BASE_URL.rstrip("/") + "/chat/completions", data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {OPENAI_API_KEY}",
                 "HTTP-Referer": "https://cloudshop.local", "X-Title": "CloudShop"})
    try:
        resp = json.loads(urllib.request.urlopen(req, timeout=120, context=_https_ctx()).read())
    except Exception as e:
        raise RuntimeError(f"OpenAI-compatible call to {OPENAI_BASE_URL} failed: {e}")
    if "choices" not in resp:
        raise RuntimeError(f"Unexpected response from {OPENAI_BASE_URL}: {str(resp)[:300]}")
    content = resp["choices"][0]["message"]["content"]
    s, e = content.find("{"), content.rfind("}")  # tolerate fences / stray prose around the JSON
    if s != -1 and e != -1:
        content = content[s:e + 1]
    return schema.model_validate_json(content)

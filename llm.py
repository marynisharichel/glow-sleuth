from __future__ import annotations

import json
import os

MODEL = os.environ.get("GLOW_MODEL", "claude-sonnet-5-5")


class LLMUnavailable(Exception):
    pass


def _client():
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise LLMUnavailable("ANTHROPIC_API_KEY is not set")
    try:
        import anthropic
    except ImportError as e:
        raise LLMUnavailable("anthropic package not installed") from e
    return anthropic.Anthropic(api_key=key)


def polish(query: str, block: str) -> str:
    """Rewrite the factual recommendation block in a friendly voice. Falls back to the block."""
    try:
        msg = _client().messages.create(
            model=MODEL, max_tokens=1500,
            messages=[{"role": "user", "content":
                f"A shopper asked: {query!r}\n\nHere are the recommendations (facts are final, "
                f"do not change prices, ratings or order, do not add products):\n\n{block}\n\n"
                "Rewrite them warmly and concisely, keeping every product."}],
        )
        return msg.content[0].text
    except Exception:
        return block


def ask_vision(images: list[tuple[str, bytes]], prompt: str) -> str:
    import base64
    client = _client()
    content = [{"type": "image", "source": {"type": "base64", "media_type": mt,
                "data": base64.b64encode(raw).decode()}} for mt, raw in images]
    content.append({"type": "text", "text": prompt})
    msg = client.messages.create(model=MODEL, max_tokens=1200,
                                 messages=[{"role": "user", "content": content}])
    return msg.content[0].text


def extract_json(text: str) -> dict:
    a, b = text.find("{"), text.rfind("}")
    return json.loads(text[a:b + 1])

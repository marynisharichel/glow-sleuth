"""Pros & cons for live (Google Shopping) products.

Live results carry prices and ratings but no review text, so a short AI summary is generated from the
product's name/claims and its ratings. It is labelled as an AI summary in the UI (not review quotes).
Uses your Gemini key, or your Anthropic key as a fallback. One batched call per question; results are cached.
"""
from __future__ import annotations

import json

from .scan import GEMINI, _candidate_models, _env, _gemini_key, _http, _parse_json

_CACHE: dict = {}
MAX_PRODUCTS = 20

PROMPT = """You help shoppers compare beauty products. For each product below write 2-3 short PROS and 1-3 short CONS.
Base them ONLY on what the product name/claims/ingredients say and on the ratings given. Do not invent facts,
percentages, studies or review quotes. Typical cons: patch-test advice for actives, results take weeks, few reviews
so the rating is early, price vs size, may not suit sensitive skin. Each point max 12 words.
Return ONLY JSON: {"p1": {"pros": [...], "cons": [...]}, "p2": {...}}

Products:
"""


def _gemini_text(prompt: str, key: str) -> str:
    payload = {"contents": [{"parts": [{"text": prompt}]}],
               "generationConfig": {"temperature": 0.3, "responseMimeType": "application/json"}}
    headers = {"x-goog-api-key": key, "Content-Type": "application/json"}
    for model in _candidate_models(key)[:2]:
        try:
            body = _http(f"{GEMINI}/models/{model}:generateContent", payload, headers, timeout=30)
            parts = body["candidates"][0]["content"]["parts"]
            return "".join(p.get("text", "") for p in parts if not p.get("thought"))
        except Exception as e:
            print(f"[insights] {model} failed: {type(e).__name__}: {e}")
    raise RuntimeError("gemini failed")


def _claude_text(prompt: str) -> str:
    from .llm import MODEL, _client
    msg = _client().messages.create(model=MODEL, max_tokens=4000, messages=[{"role": "user", "content": prompt}])
    return msg.content[0].text


def _describe(i: int, p) -> str:
    rated = [o for o in p.offers if o.reviews]
    n = sum(o.reviews for o in rated)
    avg = sum(o.rating * o.reviews for o in rated) / n if n else 0
    shops = ", ".join(sorted({o.platform for o in p.offers}))
    return f'p{i}: {p.brand} {p.name} {p.size} | rating {avg:.1f}/5 from {n} reviews | sold on {shops}'


def fill_pros_cons(products) -> None:
    """Fills .pros / .cons of live products in place. Never raises."""
    todo = []
    for p in products[:MAX_PRODUCTS]:
        if p.source != "live" or p.pros or p.cons:
            continue
        if p.id in _CACHE:
            p.pros, p.cons = _CACHE[p.id]
        else:
            todo.append(p)
    if not todo:
        return
    prompt = PROMPT + "\n".join(_describe(i, p) for i, p in enumerate(todo, 1))
    text = None
    if _gemini_key():
        try:
            text = _gemini_text(prompt, _gemini_key())
        except Exception as e:
            print(f"[insights] Gemini unavailable ({e}); trying Claude")
    if text is None and _env("ANTHROPIC_API_KEY"):
        try:
            text = _claude_text(prompt)
        except Exception as e:
            print(f"[insights] Claude failed: {type(e).__name__}: {e}")
    if text is None:
        print("[insights] no AI key worked, so no pros/cons for live products")
        return
    try:
        data = _parse_json(text)
    except Exception as e:
        print(f"[insights] could not read the answer: {e}")
        return
    for i, p in enumerate(todo, 1):
        d = data.get(f"p{i}") or {}
        pros = [str(x) for x in (d.get("pros") or [])][:3]
        cons = [str(x) for x in (d.get("cons") or [])][:3]
        if pros or cons:
            p.pros, p.cons = pros, cons
            _CACHE[p.id] = (pros, cons)
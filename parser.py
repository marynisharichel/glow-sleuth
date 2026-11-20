from __future__ import annotations

import re

from .models import Prefs

CATEGORIES = {
    "serum": r"serums?|ampoules?",
    "moisturizer": r"moisturi[sz]ers?|moisturi[sz]ing|creams?|lotions?|gel cream",
    "sunscreen": r"sunscreens?|sun screens?|spf|sunblock",
    "cleanser": r"cleansers?|face ?wash(?:es)?",
    "toner": r"toners?|face mist",
    "mask": r"face masks?|sheet masks?|masks?",
    "lip": r"lip balms?|lip care",
}
SKIN = {
    "dry": r"dry|dehydrated|flaky",
    "oily": r"oily|greasy|shiny",
    "combination": r"combination|combo",
    "sensitive": r"sensitive|reactive",
    "normal": r"normal",
}
CONCERNS = {
    "acne": r"acne|pimples?|breakouts?|blemish\w*|zits?",
    "pigmentation": r"pigmentation|dark spots?|melasma|tan|uneven (?:skin )?tone|marks?",
    "glow": r"glow\w*|dull\w*|bright\w*|brit\w*|radian\w*|whiten\w*",
    "anti-aging": r"anti.?aging|ageing|aging|wrinkles?|fine lines?",
    "hydration": r"hydrat\w*|plump\w*",
    "oil control": r"oil.?control|mattif\w*",
    "dark circles": r"dark circles?|under.?eye",
}

COMMON_PLATFORMS = ["Amazon", "Nykaa", "Purplle", "Flipkart", "Myntra", "Tira", "Meesho", "Smytten",
                    "Health & Glow", "JioMart", "Tata 1mg", "Sephora", "Ajio", "Netmeds", "PharmEasy",
                    "Minimalist", "Plum", "Dot & Key", "Mamaearth", "Foxtale"]

_NUM = r"(\d[\d,]*(?:\.\d+)?)\s*(k\b)?"
_BUDGET = [
    rf"(?:under|below|less than|within|upto|up to|max(?:imum)?|budget(?: of| is)?|<=?)\s*(?:rs\.?|inr|₹)?\s*{_NUM}",
    rf"(?:rs\.?|inr|₹)\s*{_NUM}",
    rf"{_NUM}\s*(?:rs\b|rupees|inr|₹)",
]


def _first(table, text):
    return [k for k, pat in table.items() if re.search(rf"\b(?:{pat})\b", text)]


def parse_budget(text: str):
    for pat in _BUDGET:
        m = re.search(pat, text)
        if m:
            val = float(m.group(1).replace(",", ""))
            return val * 1000 if m.group(2) else val
    return None


def parse_query(query: str, known_platforms=(), profile: dict | None = None) -> Prefs:
    t = query.lower()
    profile = profile or {}
    p = Prefs()
    cats = _first(CATEGORIES, t)
    p.category = cats[0] if cats else None
    skin = _first(SKIN, t)
    p.skin_types = skin or ([profile["skin_type"]] if profile.get("skin_type") else [])
    p.skin_type = p.skin_types[0] if p.skin_types else None
    p.concerns = _first(CONCERNS, t) or list(profile.get("concerns") or [])
    p.budget = parse_budget(t) or (float(profile["budget"]) if profile.get("budget") else None)
    m = re.search(r"\b(?:top|best)\s+(\d+)\b", t)
    p.top = int(m.group(1)) if m else None
    named = [pl for pl in known_platforms
             if re.search(rf"\b(?:on|from|at|in)\s+{re.escape(pl.lower())}\b", t)]
    p.platforms = named or None
    return p

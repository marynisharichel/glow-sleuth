"""Photo scan: Gemini (or Claude as a fallback) identifies the product,
Serper (Google Shopping India) finds prices + reviews."""
import base64
import io
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

from .live import normalize_platform, shop_url

GEMINI = "https://generativelanguage.googleapis.com/v1beta"
SERPER = "https://google.serper.dev/shopping"

PER_REQUEST_TIMEOUT = 45      # seconds for one Gemini call
TOTAL_BUDGET = 100            # seconds for the whole Gemini attempt
RETRIES_PER_MODEL = 2
RETRY_STATUS = {429, 500, 502, 503, 504}
MAX_IMAGE_SIDE = 1280         # photos are shrunk to this before upload

# Stable models first: the newest previews get the most traffic and fail most often.
PREFERRED = ["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-3.1-flash-lite",
             "gemini-3.5-flash-lite", "gemini-3.5-flash"]
SKIP_WORDS = ("tts", "image", "live", "embedding", "audio", "thinking", "robotics", "computer")

PROMPT = """You are a beauty-product identifier. Look at the photo(s) of a product (front label, box or bottle).
Return ONLY a JSON object with these keys:
{
 "is_product": true,
 "brand": "",
 "name": "",
 "category": "",
 "size": "",
 "skin_types": [],
 "ingredients": [],
 "pros": [],
 "cons": [],
 "summary": "",
 "confidence": 0.0
}
Rules:
- is_product is false if the photo does not show a beauty, skincare or personal-care product.
- name is the product name without the brand.
- category is e.g. serum, moisturizer, sunscreen, cleanser, toner, face wash, shampoo.
- skin_types only if the label says so (dry, oily, combination, normal, sensitive).
- ingredients: key ingredients readable on the label, max 8.
- pros and cons: short, honest points based on ingredients and claims, max 3 each.
- summary: 1-2 sentences on what the product is for.
- confidence: 0 to 1, how sure you are about brand and name.
Do not invent anything you cannot read or reasonably infer. Use "" or [] when unknown."""


class ScanError(Exception):
    def __init__(self, code, status=500):
        super().__init__(code)
        self.code = code
        self.status = status


# ---------------------------------------------------------------- environment
_ENV_LOADED = False


def _load_dotenv():
    """Read <project>/.env once. Real system environment variables take priority."""
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    _ENV_LOADED = True
    for folder in (Path(__file__).resolve().parent.parent, Path.cwd()):
        f = folder / ".env"
        if not f.is_file():
            continue
        for line in f.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.replace("export ", "").strip()
            v = v.strip().strip('"').strip("'")
            if k and v and not os.environ.get(k):
                os.environ[k] = v
        break


def _env(name):
    _load_dotenv()
    return (os.environ.get(name) or "").strip()


def _gemini_key():
    return _env("GEMINI_API_KEY") or _env("GOOGLE_API_KEY")


# ---------------------------------------------------------------- http helpers
def _http(url, payload=None, headers=None, timeout=PER_REQUEST_TIMEOUT):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=headers or {},
                                 method="POST" if payload is not None else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _rank(name):
    return (PREFERRED.index(name) if name in PREFERRED else len(PREFERRED), name)


def _candidate_models(key=None):
    """Flash models this key can use, stable ones first."""
    key = key or _gemini_key()
    if not key:
        raise ScanError("no_key", 500)
    try:
        body = _http(f"{GEMINI}/models?pageSize=200", headers={"x-goog-api-key": key}, timeout=20)
    except urllib.error.HTTPError as e:
        print(f"[scan] could not list models: HTTP {e.code} {e.read().decode('utf-8', 'replace')[:200]}")
        raise ScanError("bad_key" if e.code in (400, 401, 403) else "ai_error", 502)
    except Exception as e:
        print(f"[scan] could not list models: {e}")
        raise ScanError("ai_error", 502)
    names = []
    for m in body.get("models", []):
        n = m.get("name", "").replace("models/", "")
        if ("flash" in n and "generateContent" in m.get("supportedGenerationMethods", [])
                and not any(w in n for w in SKIP_WORDS)):
            names.append(n)
    names = sorted(set(names), key=_rank)
    print("[scan] Gemini models available to your key:", names)
    if not names:
        raise ScanError("ai_error", 502)
    return names


# ---------------------------------------------------------------- image preparation
def _mime(img):
    if img[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if img[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if img[:4] == b"RIFF" and img[8:12] == b"WEBP":
        return "image/webp"
    if img[4:12] in (b"ftypheic", b"ftypheix", b"ftypmif1"):
        return "image/heic"
    return "image/jpeg"


def _prepare(img):
    """Fix rotation, shrink and convert to JPEG: small, fast, and always a supported format.
    Falls back to the original bytes if Pillow is missing or the image can't be decoded."""
    try:
        from PIL import Image, ImageOps
        im = ImageOps.exif_transpose(Image.open(io.BytesIO(img))).convert("RGB")
        im.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=85)
        data = buf.getvalue()
        print(f"[scan] image {len(img) // 1024} KB -> {len(data) // 1024} KB")
        return data, "image/jpeg"
    except Exception as e:
        print(f"[scan] could not resize image ({type(e).__name__}: {e}); sending original")
        return img, _mime(img)


def _parse_json(text):
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    m = re.search(r"\{.*\}", text, re.S)
    data = json.loads(m.group(0) if m else text)
    if isinstance(data, list) and data:
        data = data[0]
    if not isinstance(data, dict):
        raise ValueError("answer is not a JSON object")
    return data


# ---------------------------------------------------------------- Gemini call
def _ask_gemini(prepared, note, key):
    parts = [{"text": PROMPT + (f"\nUser note: {note}" if note else "")}]
    for data, mime in prepared:
        parts.append({"inline_data": {"mime_type": mime, "data": base64.b64encode(data).decode()}})
    payload = {"contents": [{"parts": parts}],
               "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json"}}
    headers = {"x-goog-api-key": key, "Content-Type": "application/json"}

    deadline = time.time() + TOTAL_BUDGET
    for model in _candidate_models(key):
        for attempt in range(RETRIES_PER_MODEL):
            left = deadline - time.time()
            if left <= 3:
                print(f"[scan] Gemini: time budget of {TOTAL_BUDGET} s used up.")
                raise ScanError("ai_error", 503)
            try:
                body = _http(f"{GEMINI}/models/{model}:generateContent", payload, headers,
                             timeout=min(PER_REQUEST_TIMEOUT, left))
                cand_parts = body["candidates"][0]["content"]["parts"]
                text = "".join(p.get("text", "") for p in cand_parts if not p.get("thought"))
                result = _parse_json(text)
                print(f"[scan] {model} -> OK")
                return result
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", "replace")[:160].replace("\n", " ")
                print(f"[scan] {model} -> HTTP {e.code} (try {attempt + 1}): {detail}")
                if e.code == 401 or (e.code in (400, 403) and "API key" in detail):
                    raise ScanError("bad_key", 502)
                if e.code in RETRY_STATUS and attempt + 1 < RETRIES_PER_MODEL:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                break
            except (TimeoutError, OSError) as e:
                print(f"[scan] {model} -> {type(e).__name__}: {e}")
                break
            except (KeyError, IndexError, TypeError, ValueError) as e:
                print(f"[scan] {model} -> unreadable answer ({e})")
                break
    print("[scan] Every Gemini model failed.")
    raise ScanError("ai_error", 503)


# ---------------------------------------------------------------- Claude fallback
def _ask_claude(prepared, note):
    """Used when Gemini has no key or fails. Needs ANTHROPIC_API_KEY and the anthropic package."""
    from .llm import ask_vision, extract_json
    text = ask_vision(prepared, PROMPT + (f"\nUser note: {note}" if note else ""))
    return extract_json(text)


def identify(images, note=""):
    prepared = [_prepare(i) for i in images[:4]]
    errors = []

    key = _gemini_key()
    if key:
        try:
            return _ask_gemini(prepared, note, key)
        except ScanError as e:
            errors.append(f"gemini:{e.code}")
            print(f"[scan] Gemini failed ({e.code}); trying Claude instead")
    else:
        print("[scan] no GEMINI_API_KEY; trying Claude")

    if _env("ANTHROPIC_API_KEY"):
        try:
            result = _ask_claude(prepared, note)
            print("[scan] Claude -> OK")
            return result
        except Exception as e:
            errors.append("claude")
            print(f"[scan] Claude failed: {type(e).__name__}: {e}")
    else:
        print("[scan] no ANTHROPIC_API_KEY either")

    if not key and not _env("ANTHROPIC_API_KEY"):
        raise ScanError("no_key", 500)
    if errors and errors[0] == "gemini:bad_key" and "claude" not in errors:
        raise ScanError("bad_key", 502)
    raise ScanError("ai_error", 503)


# ---------------------------------------------------------------- reviews (Serper)
def _num(s):
    if isinstance(s, (int, float)):
        return float(s)
    m = re.search(r"\d+(?:\.\d+)?", str(s or "").replace(",", ""))
    return float(m.group(0)) if m else None


SHOPS_TO_TRY = ["Amazon", "Flipkart", "Nykaa", "Purplle", "Tira", "Myntra"]
MAX_EXTRA_QUERIES = 3         # each extra query costs one Serper credit


def _tokens(s):
    return set(re.findall(r"[a-z0-9]+", (s or "").lower()))


def _relevance(title, wanted):
    """Share of the product's words (brand, name, size) that appear in a listing title."""
    return len(wanted & _tokens(title)) / len(wanted) if wanted else 0.0


def _serper(q, key):
    body = _http(SERPER, {"q": q, "gl": "in", "num": 20},
                 {"X-API-KEY": key, "Content-Type": "application/json"}, timeout=20)
    return body.get("shopping") or []


# Shops Google Shopping rarely lists. domain -> (name, fallback search-page URL)
OTHER_SHOPS = {
    "amazon.in":      ("Amazon",    "https://www.amazon.in/s?k={q}"),
    "flipkart.com":   ("Flipkart",  "https://www.flipkart.com/search?q={q}"),
    "myntra.com":     ("Myntra",    "https://www.myntra.com/{slug}"),
    "tirabeauty.com": ("Tira",      "https://www.tirabeauty.com/search?q={q}"),
    "nykaa.com":      ("Nykaa",     "https://www.nykaa.com/search/result/?q={q}"),
}
# Quick-commerce apps have no public product pages (stock depends on your pincode),
# so we can only link to their search page.
QUICK_COMMERCE = {
    "Blinkit":   "https://blinkit.com/s/?q={q}",
    "Instamart": "https://www.swiggy.com/instamart/search?custom_back=true&query={q}",
    "Zepto":     "https://www.zepto.com/search?query={q}",
}


def _more_shops(q, key, have):
    """One Serper web search (1 credit) for product pages on big shops, plus search links
    for shops with no public pages. Skips shops already in the price table."""
    from urllib.parse import quote_plus
    qq, slug = quote_plus(q), quote_plus(q).replace("+", "-")
    have = {h.lower() for h in have}
    found = {}
    sites = " OR ".join(f"site:{d}" for d in OTHER_SHOPS)
    try:
        body = _http("https://google.serper.dev/search", {"q": f"{q} ({sites})", "gl": "in", "num": 20},
                     {"X-API-KEY": key, "Content-Type": "application/json"}, timeout=20)
        for r in body.get("organic") or []:
            link = r.get("link", "")
            for dom, (name, _) in OTHER_SHOPS.items():
                if dom in link and name.lower() not in found:
                    found[name.lower()] = {"platform": name, "url": link, "title": r.get("title", ""),
                                           "kind": "product_page"}
    except Exception as e:
        print(f"[scan] shop web-search failed: {e}")
    out = []
    for dom, (name, tpl) in OTHER_SHOPS.items():
        if name.lower() in have:
            continue
        out.append(found.get(name.lower()) or {"platform": name, "title": "", "kind": "search_link",
                                               "url": tpl.format(q=qq, slug=slug)})
    for name, tpl in QUICK_COMMERCE.items():
        if name.lower() not in have:
            out.append({"platform": name, "title": "", "kind": "search_link", "url": tpl.format(q=qq)})
    return out


def _lookup_reviews(product):
    """Search Google Shopping (India); keep ONE best offer per shop and pool ratings honestly."""
    key = _env("SERPER_API_KEY")
    if not key:
        return {"status": "no_serper_key", "offers": [], "pooled": None}
    q = " ".join(x for x in (product.get("brand"), product.get("name"), product.get("size")) if x).strip()
    if not q:
        return {"status": "no_query", "offers": [], "pooled": None}
    try:
        all_items = _serper(q, key)
    except Exception as e:
        print(f"[scan] Serper failed: {e}")
        return {"status": "serper_error", "query": q, "offers": [], "pooled": None}

    brand = (product.get("brand") or "").lower().strip()
    wanted = _tokens(q)

    def build(items):
        out = []
        for it in items:
            if brand and brand not in (it.get("title") or "").lower():
                continue
            plat = normalize_platform(it.get("source", ""))
            out.append({"platform": plat, "title": it.get("title", ""),
                        "price": _num(it.get("price")), "rating": _num(it.get("rating")),
                        "reviews": int(_num(it.get("ratingCount")) or 0),
                        "match": _relevance(it.get("title", ""), wanted),
                        "url": shop_url(plat, it.get("link", ""), q),
                        "image": it.get("imageUrl", "")})
        return out

    offers = build(all_items)
    if not offers and all_items:
        print(f"[scan] no result mentions brand '{brand}'; showing unfiltered results")
        brand = ""
        offers = build(all_items)

    # Ask specifically for big shops that did not show up (a few extra queries, capped).
    seen = {o["platform"].lower() for o in offers}
    missing = [s for s in SHOPS_TO_TRY if s.lower() not in seen][:MAX_EXTRA_QUERIES]
    for shop in missing:
        try:
            extra = build(_serper(f"{q} {shop}", key))
        except Exception as e:
            print(f"[scan] extra Serper query for {shop} failed: {e}")
            continue
        offers += [o for o in extra if shop.lower() in o["platform"].lower()]

    # One offer per shop: the listing that best matches the product, then the cheapest.
    best = {}
    for o in offers:
        k = o["platform"].strip().lower()
        if not k:
            continue
        cur = best.get(k)
        rank = (-round(o["match"], 2), o["price"] if o["price"] is not None else 1e9)
        if cur is None or rank < cur[0]:
            best[k] = (rank, o)
    offers = [o for _, o in best.values()]
    for o in offers:
        o.pop("match", None)

    # Pool ratings. Google repeats one product-level rating on many listings, so count
    # each distinct (rating, review count) pair once instead of once per listing.
    distinct = {(o["rating"], o["reviews"]): o for o in offers if o["rating"] and o["reviews"]}
    pooled = None
    if distinct:
        c, prior = 100, 4.0                               # Bayesian average: few reviews pull toward 4.0
        n = sum(r for _, r in distinct)
        total = sum(rt * r for rt, r in distinct)
        pooled = {"score": round((total + c * prior) / (n + c), 2),
                  "raw_average": round(total / n, 2),
                  "total_reviews": n,
                  "platforms": len(offers)}
    offers.sort(key=lambda o: (o["price"] is None, o["price"] or 0))   # cheapest first
    more = _more_shops(q, key, {o["platform"] for o in offers})
    return {"status": "ok" if offers else "no_results", "query": q, "pooled": pooled,
            "offers": offers, "more_shops": more}


# ---------------------------------------------------------------- public entry point
def scan(images, note="", profile=None, catalog=None):
    images = [i for i in (images or []) if i]
    if not images:
        raise ScanError("no_image", 400)

    product = identify(images, note)
    if not product.get("is_product", True):
        raise ScanError("not_a_product", 422)

    reviews = _lookup_reviews(product)
    return {
        "product": product,
        "reviews": reviews,
        # flat copies so UI code that reads these keys keeps working
        "brand": product.get("brand", ""),
        "name": product.get("name", ""),
        "summary": product.get("summary", ""),
        "offers": reviews.get("offers", []),
        "more_shops": reviews.get("more_shops", []),
        "rating": (reviews.get("pooled") or {}).get("score"),
    }
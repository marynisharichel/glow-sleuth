"""Live product + review data from Google Shopping through Serper.dev.

Serper's /shopping endpoint returns, per listing: title, source (the shop), price, rating,
ratingCount, link, imageUrl. Two things make the results useful:

1. GROUPING: the same product is listed under slightly different titles by different shops,
   so listings are compared on their *distinctive* words (brand + actives + size), ignoring
   marketing words like "face", "vegan", "glow", "hydration". Matching listings become ONE
   product with one offer per shop.
2. ENRICHMENT: a single Google Shopping search often shows a product on only one shop. For the
   top products we run product-specific searches (first a general one, then one per big shop
   such as Amazon / Flipkart / Nykaa / Purplle that is still missing) to collect the same
   product's listings and reviews on the other shops. Costs extra Serper credits per question:
   GLOW_ENRICH=<products to look up, default 10, 0 = off> and GLOW_ENRICH_SHOPS=<best products that
   also get one search per missing big shop, default 3> in .env.
3. SHOP LINKS: every offer gets a link that opens the shop itself (the product page when Google
   gives one, otherwise that shop's own search for the product), never a Google redirect.

Set SERPER_API_KEY (environment variable or .env file) to switch this on.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote, quote_plus, urlparse

from .models import Offer, Prefs, Product
from .parser import CATEGORIES, COMMON_PLATFORMS, CONCERNS, SKIN

BASE = os.environ.get("SERPER_BASE", "https://google.serper.dev")
URL = BASE + "/shopping"
_PLAT = {p.lower(): p for p in COMMON_PLATFORMS}


class LiveSearchError(Exception):
    pass


MAJOR_SHOPS = ["Amazon", "Flipkart", "Nykaa", "Purplle"]       # big shops we try to find reviews on

_SHOP_SEARCH = {                                               # each shop's own search page
    "amazon": "https://www.amazon.in/s?k={q}",
    "flipkart": "https://www.flipkart.com/search?q={q}",
    "nykaa": "https://www.nykaa.com/search/result/?q={q}&root=search&searchType=Manual",
    "purplle": "https://www.purplle.com/search?q={q}",
    "tira": "https://www.tirabeauty.com/products/?q={q}",
    "myntra": "https://www.myntra.com/{dash}?rawQuery={q}",
    "meesho": "https://www.meesho.com/search?q={q}",
    "ajio": "https://www.ajio.com/search/?text={q}",
    "jiomart": "https://www.jiomart.com/search/{q}",
    "health & glow": "https://www.healthandglow.com/catalogsearch/result/?q={q}",
}


def normalize_platform(name: str) -> str:
    n = re.split(r"\s+[-|]\s+", (name or "").strip())[0].strip()     # "Purplle.com - Purplle Beauty" -> "Purplle.com"
    n = re.sub(r"\.(co\.in|com|in|net|org)\b", "", n, flags=re.I).strip()   # "Purplle.com" / "Amazon.in" -> shop name
    low = n.lower()
    if low in _PLAT:
        return _PLAT[low]
    for k, v in _PLAT.items():                              # "Nykaa Beauty" / "Amazon Retail" -> Nykaa / Amazon
        if low.startswith(k + " "):
            return v
    return n or "Unknown"


def is_direct(link: str) -> bool:
    host = urlparse(link or "").netloc.lower()
    return bool(host) and "google." not in host            # Google Shopping redirects are not the shop itself


def shop_url(platform: str, link: str, query: str) -> str:
    """The link behind the 'shop' button: the shop's own page, never a Google redirect."""
    if is_direct(link):
        return link
    tpl = _SHOP_SEARCH.get((platform or "").lower())
    if tpl:
        words = " ".join(query.split()[:8])
        return tpl.format(q=quote_plus(words), dash=quote("-".join(words.split()).lower()))
    if platform:                                           # brand's own site etc.: a web search that lands on it
        lead = "" if query.lower().startswith(platform.lower()) else f"{platform} "
        return "https://www.google.com/search?q=" + quote_plus(lead + query)
    return link


def _price(v):
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r"\d[\d,]*(?:\.\d+)?", str(v or ""))
    return float(m.group().replace(",", "")) if m else None


def _int(v):
    try:
        return int(float(str(v).replace(",", "")))
    except (TypeError, ValueError):
        return 0


# Words that say nothing about WHICH product it is (shops stuff titles with them).
_GENERIC = {
    "for", "and", "the", "with", "of", "in", "by", "to", "on", "a", "an", "all", "types", "type", "new", "pack",
    "skin", "skins", "face", "facial", "dry", "oily", "sensitive", "combination", "normal", "women", "men", "unisex",
    "serum", "serums", "cream", "moisturizer", "moisturiser", "moisturizers", "moisturisers", "moisturizing",
    "moisturising", "lotion", "gel", "cleanser", "toner", "mask", "sunscreen", "wash", "facewash",
    "vegan", "best", "glow", "glowing", "hydration", "hydrated", "hydrating", "hydrate", "pump", "bottle",
    "bottleserum", "daily", "care", "skincare", "natural", "dermatologist", "tested", "free", "paraben",
    "sulphate", "sulfate", "whitening", "brightening", "bright", "radiance", "radiant", "lightweight", "kit",
    "combo", "set", "offer", "buy", "get", "white", "anti", "ageing", "aging", "acne", "pigmentation", "marks",
    "dark", "spots", "spot", "oil", "control", "reduces", "reduce", "clear", "clearing", "boost", "boosting",
    "ml", "gm", "gms", "g", "oz", "l", "fl", "pcs", "pc",
}


def _brand(title: str) -> str:
    w = title.split()
    if len(w) >= 3 and w[1] == "&":                    # "Dot & Key"
        return " ".join(w[:3])
    if w and w[0].lower() == "the" and len(w) > 1:     # "The Ordinary", "The Derma Co"
        return " ".join(w[:3] if len(w) > 2 and w[2].lower().strip(".") == "co" else w[:2])
    return w[0] if w else ""


def _size(title: str) -> str:
    m = re.search(r"\b(\d+(?:\.\d+)?)\s?(ml|gm|gms|g|oz|l)\b", title, re.I)
    if not m:
        return ""
    unit = "g" if m.group(2).lower() in ("gm", "gms", "g") else m.group(2).lower()
    return f"{m.group(1)}{unit}"


def _tokens(title: str, brand: str = "") -> list:
    """Distinctive words of a title, in order, without brand words / marketing words / sizes."""
    t = re.sub(r"(\d)\s+(ml|gms|gm|g|oz)\b", r"\1\2", title.lower())     # "30 ml" == "30ml"
    skip = set(re.findall(r"[a-z0-9]+", brand.lower())) | _GENERIC
    out = []
    for x in re.findall(r"[a-z0-9]+", t):
        if x in skip or re.fullmatch(r"\d+(?:ml|gms|gm|g|oz|l)", x) or x in out:
            continue
        out.append(x)
    return out


def _key(title: str):
    b = _brand(title)
    return (b, _tokens(title, b), _size(title))


def _same(a, b) -> bool:
    (ba, ta, sa), (bb, tb, sb) = a, b
    if not ba or ba.lower() != bb.lower():
        return False
    if sa and sb and sa != sb:                          # 30 ml vs 50 ml = a different listing
        return False
    ta, tb = set(ta), set(tb)
    if not ta and not tb:
        return True
    if not ta or not tb:
        return False
    both = len(ta & tb)
    return both >= 1 and both / min(len(ta), len(tb)) >= 0.75 and both / max(len(ta), len(tb)) >= 0.5


def _same_loose(a, b) -> bool:
    """Used only for listings found by a search made FOR that product: other shops word titles differently."""
    (ba, ta, sa), (bb, tb, sb) = a, b
    if not ba or ba.lower() != bb.lower():
        return False
    if sa and sb and sa != sb:
        return False
    ta, tb = set(ta), set(tb)
    if not ta and not tb:
        return True
    if not ta or not tb:
        return False
    both, m = len(ta & tb), min(len(ta), len(tb))
    return both >= min(m, 2) and both / m >= 0.5


class SerperShopping:
    def __init__(self, api_key: str | None = None, num: int = 40, enrich: int | None = None):
        self._key, self.num = api_key, num
        if enrich is None:
            try:
                enrich = int(os.environ.get("GLOW_ENRICH", "10"))
            except ValueError:
                enrich = 10
        self.enrich = max(0, min(enrich, 15))            # products that get a product-specific search (1 credit each)
        try:
            self.enrich_shops = max(0, min(int(os.environ.get("GLOW_ENRICH_SHOPS", "3")), self.enrich or 0))
        except ValueError:
            self.enrich_shops = min(3, self.enrich)      # best products that also get one search per missing big shop

    @property
    def key(self):
        k = (self._key or os.environ.get("SERPER_API_KEY") or "").strip().strip("\"'").strip()
        k = re.sub(r"^(SERPER_API_KEY\s*=\s*|Bearer\s+)", "", k, flags=re.I).strip().strip("\"'")
        return re.sub(r"[^\x21-\x7e]", "", k)          # drop spaces / invisible characters from copy-paste

    @property
    def enabled(self) -> bool:
        return bool(self.key)

    def _query(self, prefs: Prefs, original: str) -> str:
        if not (prefs.category or prefs.skin_types or prefs.concerns):
            return original
        parts = [prefs.category or "skincare"]
        if prefs.skin_types:
            parts.append(f"for {' '.join(prefs.skin_types)} skin")
        parts += prefs.concerns
        return " ".join(parts)

    def _post(self, payload: dict) -> dict:
        req = urllib.request.Request(
            URL, data=json.dumps(payload).encode(), method="POST",
            headers={"X-API-KEY": self.key, "Content-Type": "application/json",
                     "Accept": "application/json", "User-Agent": "Mozilla/5.0 (GlowSleuth)"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                body = e.read().decode("utf-8", "replace").strip()[:200]
            except Exception:
                body = ""
            hint = " - check SERPER_API_KEY / credits" if e.code in (400, 401, 403) else ""
            raise LiveSearchError(f"Serper HTTP {e.code}{hint}" + (f" | Serper says: {body}" if body else ""))
        except Exception as e:
            raise LiveSearchError(str(e) or e.__class__.__name__)

    @staticmethod
    def _items(data: dict, cat_re, keep_unrated: bool = False):
        """Serper response -> [(title, key, Offer, image)], number of listings that have no reviews.
        keep_unrated=True keeps those listings too (rating 0): they show where a product is sold."""
        items, skipped = [], 0
        for it in data.get("shopping") or []:
            price, rating, count = _price(it.get("price")), float(it.get("rating") or 0), _int(it.get("ratingCount"))
            title = (it.get("title") or "").strip()
            if not title or price is None or (cat_re and not cat_re.search(title)):
                continue
            if rating <= 0 or count <= 0:
                skipped += 1
                if not keep_unrated:
                    continue
                rating, count = 0.0, 0
            offer = Offer(normalize_platform(it.get("source")), price, rating, count, it.get("link", ""))
            items.append((title, _key(title), offer, it.get("imageUrl", "")))
        return items, skipped

    @staticmethod
    def _group(items):
        groups = []
        for item in sorted(items, key=lambda i: len(i[1][1])):   # cleanest (shortest) titles lead each group
            for g in groups:
                if _same(g[0][1], item[1]):
                    g.append(item)
                    break
            else:
                groups.append([item])
        return groups

    def _run(self, jobs, items, cat_re):
        """jobs = [(product key, product title, query)]. Adds listings of THOSE products found by the queries;
        each added listing is re-tagged with the product's own key/title so it joins that product exactly."""
        if not jobs:
            return items

        def fetch(job):
            _, _, q = job
            try:
                return self._items(self._post({"q": q, "gl": "in", "hl": "en", "num": 20}), cat_re, keep_unrated=True)[0]
            except LiveSearchError as e:
                print(f"[live] enrichment search failed for '{q}': {e}")
                return []

        with ThreadPoolExecutor(max_workers=min(8, len(jobs))) as pool:
            results = list(pool.map(fetch, jobs))
        seen = {(i[2].platform.lower(), i[2].url or i[0]) for i in items}
        added = 0
        for (key, title, q), found in zip(jobs, results):
            got = 0
            for it in found:
                ident = (it[2].platform.lower(), it[2].url or it[0])
                if ident in seen or not _same_loose(key, it[1]):    # only listings of the SAME product
                    continue
                seen.add(ident)
                items.append((title, key, it[2], it[3]))
                added += 1
                got += 1
            print(f"[live]   '{q}' -> {len(found)} listing(s), {got} new for this product")
        print(f"[live] enrichment: {len(jobs)} search(es), {added} extra listing(s) from other shops")
        return items

    def _enrich(self, groups, items, cat_re, category: str):
        """Find each top product's listings on the OTHER shops: a search with its own title for the top
        GLOW_ENRICH products, then one search per big shop still missing for the best GLOW_ENRICH_SHOPS."""
        if not self.enrich or not groups:
            return items
        top = sorted(groups, key=lambda g: -sum(i[2].reviews for i in g))[: self.enrich]
        keys = [(g[0][1], g[0][0]) for g in top]            # (product key, product title)

        def base(k, title):
            brand = k[0]
            rest = title[len(brand):] if title.lower().startswith(brand.lower()) else title
            return f"{brand} {' '.join(rest.split()[:11])}".strip()

        items = self._run([(k, t, base(k, t)) for k, t in keys], items, cat_re)
        groups = self._group(items)
        jobs = []
        for k, t in keys[: self.enrich_shops]:
            g = next((g for g in groups if g[0][1] == k), None)
            have = {i[2].platform.lower() for i in g} if g else set()
            jobs += [(k, t, f"{base(k, t)} {shop}") for shop in MAJOR_SHOPS if shop.lower() not in have]
        return self._run(jobs[:12], items, cat_re)

    def search(self, prefs: Prefs, original: str):
        q = self._query(prefs, original)
        data = self._post({"q": q, "gl": "in", "hl": "en", "num": self.num})
        cat_re = re.compile(rf"\b(?:{CATEGORIES[prefs.category]})\b", re.I) if prefs.category in CATEGORIES else None
        items, _ = self._items(data, cat_re, keep_unrated=True)
        items = self._enrich(self._group(items), items, cat_re, prefs.category or "")
        groups = self._group(items)
        kept = [g for g in groups if any(i[2].reviews for i in g)]          # products with at least one review
        skipped = sum(len(g) for g in groups if not any(i[2].reviews for i in g))
        return [self._product(g, prefs) for g in kept], skipped

    def _product(self, group, prefs: Prefs) -> Product:
        title = max((g[0] for g in group), key=lambda t: sum(1 for g in group if g[0] == t) * 1000 - len(t))
        tl = " ".join(g[0] for g in group).lower()     # words from every shop's title help tag skin type / concerns
        best = {}
        for _, _, o, _ in group:                       # one offer per platform: keep the best-reviewed
            if o.platform not in best or o.reviews > best[o.platform].reviews:
                best[o.platform] = o
        if len(best) >= 4:                             # e.g. Rs 250 and Rs 6,400 next to Rs 1,700: other size / overseas reseller
            prices = sorted(o.price for o in best.values())
            med = prices[len(prices) // 2]
            kept = {k: o for k, o in best.items() if med * 0.3 <= o.price <= med * 2.5}
            if kept:
                best = kept
        brand = _brand(title)
        query = " ".join(x for x in (brand, " ".join(group[0][1][1][:5]), group[0][1][2]) if x)
        for o in best.values():
            o.url = shop_url(o.platform, o.url, query)
        name = title[len(brand):].strip(" -–:|,") or title
        size = re.search(r"\b(\d+(?:\.\d+)?\s?(?:ml|gm|gms|g|oz|l))\b", title, re.I)
        skin = [k for k, p in SKIN.items() if re.search(rf"\b(?:{p})\b", tl)]
        if "all skin" in tl:
            skin = ["all"]
        concerns = [k for k, p in CONCERNS.items() if re.search(rf"\b(?:{p})\b", tl)]
        return Product(
            id="live-" + re.sub(r"[^a-z0-9]+", "-", f"{brand} {' '.join(group[0][1][1][:6])} {group[0][1][2]}".lower()).strip("-"),
            name=name, brand=brand, category=prefs.category or "",
            size=size.group(1) if size else "",
            skin_types=skin or list(prefs.skin_types),
            concerns=concerns or list(prefs.concerns),
            offers=list(best.values()), image=next((g[3] for g in group if g[3]), ""), source="live")
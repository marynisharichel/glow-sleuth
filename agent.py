"""Recommendation engine.

Key behaviours
* Returns EVERY product that matches (category + skin type + budget) - not just one.
  Only an explicit "top 5" in the query / --top flag trims the list.
* Works over ALL platforms found in the catalog (no hard-coded Amazon/Nykaa/Purplle).
* Ranks by review quality: a Bayesian average of ratings pooled across platforms,
  so 4.8* from 12 reviews does not beat 4.4* from 12,000 reviews.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace

from .models import Offer, Prefs, Product
from .live import LiveSearchError
from .parser import COMMON_PLATFORMS, parse_query
from .sources import merge_products

PRIOR_MEAN = 4.0      # what we assume for a product with no reviews
PRIOR_WEIGHT = 100    # how many "virtual reviews" the assumption is worth


def review_score(offers: list[Offer]) -> tuple[float, int]:
    n = sum(o.reviews for o in offers)
    total = sum(o.rating * o.reviews for o in offers)
    return (total + PRIOR_MEAN * PRIOR_WEIGHT) / (n + PRIOR_WEIGHT), n


@dataclass
class Recommendation:
    product: Product
    rank: int = 0
    score: float = 0.0
    review_score: float = 0.0
    total_reviews: int = 0
    best_offer: Offer | None = None
    reasons: list = field(default_factory=list)
    in_budget: set = field(default_factory=set)

    @property
    def explanation(self) -> str:
        p = self.product
        o = self.best_offer
        lines = [f"{self.rank}. {p.brand} {p.name} ({p.size}) - Rs {o.price:,.0f} on {o.platform}"]
        lines += [f"   - {r}" for r in self.reasons]
        if p.pros:
            lines.append("   - Reviewers love: " + "; ".join(p.pros))
        if p.cons:
            lines.append("   - Heads-up: " + "; ".join(p.cons))
        return "\n".join(lines)

    def to_dict(self) -> dict:
        p, best = self.product, self.best_offer
        offers = sorted(p.offers, key=lambda o: (o.reviews == 0, o.price))     # reviewed shops first
        return {
            "rank": self.rank, "id": p.id, "name": p.name, "brand": p.brand,
            "category": p.category, "size": p.size, "skin_types": p.skin_types,
            "concerns": p.concerns, "ingredients": p.ingredients,
            "pros": p.pros, "cons": p.cons, "image": p.image, "source": p.source,
            "review_score": round(self.review_score, 2),
            "total_reviews": self.total_reviews,
            "platform_count": len([o for o in p.offers if o.reviews]),
            "shop_count": len(p.offers),
            "best_offer": {"platform": best.platform, "price": best.price, "url": best.url},
            "reasons": self.reasons,
            "offers": [
                {"platform": o.platform, "price": o.price, "rating": o.rating,
                 "reviews": o.reviews, "url": o.url,
                 "in_budget": o.platform in self.in_budget,
                 "is_best": o is best}
                for o in offers
            ],
        }


def recommend(products: list[Product], prefs: Prefs, log: list | None = None):
    log = log if log is not None else []
    pool = list(products)

    if prefs.category:
        pool = [p for p in pool if p.category == prefs.category]
        log.append(f"Found {len(pool)} {prefs.category} product(s) in the catalog")

    if prefs.skin_types:
        pool = [p for p in pool if "all" in p.skin_types or any(s in p.skin_types for s in prefs.skin_types)]
        log.append(f"{len(pool)} suit {'/'.join(prefs.skin_types)} skin")

    if prefs.platforms:
        wanted = {x.lower() for x in prefs.platforms}
        pool = [replace(p, offers=[o for o in p.offers if o.platform.lower() in wanted]) for p in pool]
        pool = [p for p in pool if p.offers]
        log.append(f"Looked only at: {', '.join(prefs.platforms)} ({len(pool)} left)")
    else:
        names = sorted({o.platform for p in pool for o in p.offers})
        log.append(f"Compared reviews across {len(names)} platform(s): {', '.join(names)}")

    if prefs.budget:
        cheapest = min((o.price for p in pool for o in p.offers), default=None)
        pool = [p for p in pool if any(o.price <= prefs.budget for o in p.offers)]
        log.append(f"{len(pool)} have a listing at or under Rs {prefs.budget:,.0f}")
        if not pool and cheapest:
            log.append(f"Closest option starts at Rs {cheapest:,.0f}")

    recs = []
    for p in pool:
        rs, n = review_score(p.offers)
        affordable = [o for o in p.offers if not prefs.budget or o.price <= prefs.budget]
        best = min(affordable, key=lambda o: o.price)
        hit = [c for c in prefs.concerns if c in p.concerns]
        quality = max(0.0, min(1.0, (rs - 3.0) / 2.0))
        concern = len(hit) / len(prefs.concerns) if prefs.concerns else 0.5
        value = 1 - best.price / prefs.budget if prefs.budget else 0.5
        r = Recommendation(
            product=p, score=0.70 * quality + 0.20 * concern + 0.10 * max(0.0, value),
            review_score=rs, total_reviews=n, best_offer=best,
            in_budget={o.platform for o in affordable},
        )
        rated = [o for o in p.offers if o.reviews]
        more = f", sold on {len(p.offers)} shops in all" if len(p.offers) > len(rated) else ""
        r.reasons.append(f"Rated {rs:.1f}/5 from {n:,} reviews across {len(rated)} platform(s){more}")
        top_rated = max((o for o in p.offers if o.reviews >= 50), key=lambda o: o.rating, default=None)
        if top_rated:
            r.reasons.append(f"Best-reviewed on {top_rated.platform} ({top_rated.rating:.1f}/5)")
        if p.source == "live":
            asked = (["/".join(prefs.skin_types) + " skin"] if prefs.skin_types else []) + list(prefs.concerns)
            if asked:
                r.reasons.append("Matched your search: " + ", ".join(asked))
        else:
            fits = [s for s in prefs.skin_types if s in p.skin_types or "all" in p.skin_types]
            if fits:
                r.reasons.append(f"Suitable for {'/'.join(fits)} skin")
            if hit:
                r.reasons.append("Targets " + ", ".join(hit))
        if n < 200:
            r.reasons.append("Only a few reviews so far - treat the rating as early")
        recs.append(r)

    recs.sort(key=lambda r: (-r.score, -r.total_reviews))
    if prefs.top:
        recs = recs[: prefs.top]
    for i, r in enumerate(recs, 1):
        r.rank = i
    log.append(f"Ranked {len(recs)} product(s) by review quality" if recs else "Nothing matched")
    return recs


class GlowSleuth:
    def __init__(self, sources, live=None):
        self.sources, self.live = sources, live

    def catalog(self, include_demo: bool = True) -> list[Product]:
        items = merge_products([p for s in self.sources for p in s.all()])
        return [p for p in items if include_demo or p.source != "demo"]

    def search(self, query: str, top: int | None = None, platforms: list | None = None,
               profile: dict | None = None):
        """Returns (prefs, trace, recs, status). status.mode is live | live_error | demo | catalog."""
        local = self.catalog()
        known = sorted({o.platform for p in local for o in p.offers} | set(COMMON_PLATFORMS))
        prefs = parse_query(query, known, profile)
        if top:
            prefs.top = top
        if platforms and not prefs.platforms:
            prefs.platforms = platforms
        trace = [f"Understood: {prefs.describe()}"]
        status = {"mode": "demo" if any(p.source == "demo" for p in local) else "catalog", "error": ""}

        products = local
        if self.live and self.live.enabled:
            products = self.catalog(include_demo=False)       # never show fictional data when a key is set
            try:
                live, skipped = self.live.search(prefs, query)
                n = len({o.platform for p in live for o in p.offers})
                note = f" ({skipped} listing(s) skipped: no reviews)" if skipped else ""
                trace.append(f"Searched Google Shopping live: {len(live)} product(s) with reviews "
                             f"from {n} platform(s){note}")
                products = live + products
                status["mode"] = "live"
            except LiveSearchError as e:
                status.update(mode="live_error", error=str(e))
                trace.append(f"Live search failed: {e}")
        recs = recommend(products, prefs, trace)
        try:                                           # AI pros & cons for live products (they have no review text)
            from .insights import fill_pros_cons
            fill_pros_cons([r.product for r in recs])
        except Exception as e:
            print(f"[insights] skipped: {type(e).__name__}: {e}")
        return prefs, trace, recs, status

    def run(self, query: str, top: int | None = None, platforms: list | None = None,
            profile: dict | None = None):
        return self.search(query, top, platforms, profile)[:3]
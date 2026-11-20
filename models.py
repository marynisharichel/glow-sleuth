from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Offer:
    """One product listing on one shopping platform (any platform name works)."""
    platform: str
    price: float
    rating: float = 0.0
    reviews: int = 0
    url: str = ""


@dataclass
class Product:
    id: str
    name: str
    brand: str
    category: str
    size: str = ""
    skin_types: list = field(default_factory=list)
    concerns: list = field(default_factory=list)
    ingredients: list = field(default_factory=list)
    pros: list = field(default_factory=list)
    cons: list = field(default_factory=list)
    offers: list = field(default_factory=list)
    image: str = ""
    source: str = "catalog"      # catalog | demo | live

    @classmethod
    def from_dict(cls, d: dict) -> "Product":
        low = lambda xs: [str(x).strip().lower() for x in (xs or [])]
        hl = d.get("highlights") or {}
        brand, name = d.get("brand", ""), d["name"]
        return cls(
            id=str(d.get("id") or f"{brand}-{name}".lower().replace(" ", "-")),
            name=name,
            brand=brand,
            category=str(d.get("category", "")).strip().lower(),
            size=d.get("size", ""),
            skin_types=low(d.get("skin_types")),
            concerns=low(d.get("concerns")),
            ingredients=list(d.get("ingredients") or []),
            pros=list(hl.get("pros") or []),
            cons=list(hl.get("cons") or []),
            offers=[
                Offer(
                    platform=str(o["platform"]).strip(),
                    price=float(o["price"]),
                    rating=float(o.get("rating", 0)),
                    reviews=int(o.get("reviews", 0)),
                    url=o.get("url", ""),
                )
                for o in d.get("offers", [])
            ],
        )


@dataclass
class Prefs:
    category: str | None = None
    skin_type: str | None = None
    skin_types: list = field(default_factory=list)   # every skin type mentioned ("oily to dry" -> both)
    concerns: list = field(default_factory=list)
    budget: float | None = None
    top: int | None = None            # None = show EVERY match
    platforms: list | None = None     # None = EVERY platform

    def describe(self) -> str:
        bits = [self.category or "any product"]
        if self.skin_types:
            bits.append("/".join(self.skin_types) + " skin")
        if self.concerns:
            bits.append("for " + ", ".join(self.concerns))
        if self.budget:
            bits.append(f"under Rs {self.budget:,.0f}")
        return " · ".join(bits)

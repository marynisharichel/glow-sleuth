"""Catalog sources.

A source is anything with .all() -> list[Product]. To add a new shopping platform
you can either (a) add its offers to data/*.json, or (b) write a Source subclass that
calls that platform's API / your scraper. Offers for the same product id from
different sources are MERGED automatically, so there is no fixed platform list.
"""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from .models import Product


def merge_products(products: list[Product]) -> list[Product]:
    merged: dict[str, Product] = {}
    for p in products:
        if p.id not in merged:
            merged[p.id] = replace(p, offers=list(p.offers))
            continue
        have = {o.platform.lower() for o in merged[p.id].offers}
        merged[p.id].offers += [o for o in p.offers if o.platform.lower() not in have]
    return list(merged.values())


class Source:
    def all(self) -> list[Product]:
        raise NotImplementedError


class JsonCatalogSource(Source):
    """Reads one JSON file, or every *.json file inside a folder."""

    def __init__(self, path):
        self.path = Path(path)
        self._stamp = None
        self._products: list[Product] = []
        self._sample = False

    def _files(self):
        return sorted(self.path.glob("*.json")) if self.path.is_dir() else [self.path]

    def _load(self):
        files = self._files()
        stamp = tuple((f.name, f.stat().st_mtime) for f in files)
        if stamp == self._stamp:          # edit products.json without restarting
            return
        items, sample = [], False
        for f in files:
            data = json.loads(f.read_text(encoding="utf-8"))
            sample = sample or bool(data.get("sample"))
            items += [replace(Product.from_dict(d), source="demo" if data.get("sample") else "catalog")
                      for d in data.get("products", [])]
        self._products, self._sample, self._stamp = merge_products(items), sample, stamp

    def all(self) -> list[Product]:
        self._load()
        return self._products

    def raw(self) -> dict:
        """Small summary embedded in the web page (platform chips, suggestions)."""
        self._load()
        return {
            "sample": self._sample,
            "platforms": sorted({o.platform for p in self._products for o in p.offers}),
            "categories": sorted({p.category for p in self._products}),
            "count": len(self._products),
        }

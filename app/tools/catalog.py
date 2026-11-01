"""The CloudShop Marketplace catalog plus the tool functions agents may call.

The catalog is the gold output of the bronze->silver->gold data pipeline (see `pipeline/`): a
synthetic, licence-free storefront that stands in for real retailers. In a production system this
backend would be replaced by licensed feeds from real online stores; the agents are written against
one normalised product shape, so that swap would not touch them.
"""
import copy
import json
import time
from typing import Optional

from . import sizes
from ..config import CATALOG_PATH
from ..models import BOTTOM_CATEGORIES, ONE_SIZE_CATEGORIES, SHOE_CATEGORIES, matches_category

SOURCE_NAME = "CloudShop Marketplace"

_PRODUCTS: dict[str, dict] = {}
_STATUS: dict = {"source": None}


def _load() -> dict[str, dict]:
    if _STATUS["source"] is None:
        load_catalog()
    return _PRODUCTS


def load_catalog() -> dict:
    """Load the CloudShop Marketplace catalog (the gold output of the data pipeline). The datamart
    is built on demand if it has not been generated yet, so the app always has data."""
    if not CATALOG_PATH.exists():
        import pipeline

        pipeline.build_demo()
    _PRODUCTS.clear()
    for p in json.loads(CATALOG_PATH.read_text(encoding="utf-8")):
        _PRODUCTS[p["id"]] = p
    _STATUS.update(source=SOURCE_NAME, loaded_at=time.time(), count=len(_PRODUCTS))
    return status()


def status() -> dict:
    _load()
    return dict(_STATUS)


def reset_catalog() -> None:
    load_catalog()


def all_products() -> list[dict]:
    return list(_load().values())


def get(product_id: str) -> Optional[dict]:
    return _load().get(product_id)


def size_for(product: dict, brief) -> Optional[str]:
    """Pick which of the shopper's sizes applies to a product, as the catalog labels it."""
    size_top, size_bottom = brief.size_top, brief.size_bottom
    if product["category"] in ONE_SIZE_CATEGORIES:
        return next(iter(product["stock"]), "One Size")
    if product["category"] in SHOE_CATEGORIES:
        if not brief.size_shoe:
            return None
        match = sizes.pick(product["stock"], brief.size_shoe, brief.section, product["section"])
        eu = sizes.to_eu(brief.size_shoe, brief.section)
        return match or (f"EU {eu:g}" if eu else brief.size_shoe)  # no match: the check reports it missing
    if product["size_system"] == "waist" or product["category"] in BOTTOM_CATEGORIES:
        return size_bottom if product["size_system"] == "waist" else (size_top or size_bottom)
    return size_top


def _summary(p: dict) -> dict:
    return {
        "id": p["id"], "name": p["name"], "brand": p["brand"], "store": p.get("store", SOURCE_NAME),
        "section": p["section"], "category": p["category"], "price": p["price"], "colors": p["colors"],
        "materials": p["materials"], "style_tags": p["style_tags"], "occasions": p["occasions"],
        "sizes_in_stock": [s for s, q in p["stock"].items() if q > 0],
    }


# ----------------------------- tools ------------------------------------- #
def search_catalog(
    categories: Optional[list[str]] = None,
    section: Optional[str] = None,
    max_price: Optional[float] = None,
    colors: Optional[list[str]] = None,
    styles: Optional[list[str]] = None,
    occasion: Optional[str] = None,
    exclude_materials: Optional[list[str]] = None,
    avoid_colors: Optional[list[str]] = None,
    limit: int = 15,
) -> list[dict]:
    """Filter the catalog. Hard filters: categories, section, max_price, exclude_materials.
    Soft signals (colors, styles, occasion) only affect ranking; avoid_colors is a soft penalty
    (an item offered in a liked colour AND an avoided one is still valid, just ranked lower)."""
    results = []
    for p in _load().values():
        if categories and not matches_category(p["category"], categories):
            continue
        if section and section != "unisex" and p["section"] not in (section, "unisex"):
            continue
        if max_price is not None and p["price"] > max_price:
            continue
        if exclude_materials and set(m.lower() for m in exclude_materials) & set(p["materials"]):
            continue
        score = 0
        score += 2 * len(set(colors or []) & set(p["colors"]))
        score += 2 * len(set(styles or []) & set(p["style_tags"]))
        score += 3 if occasion and occasion in p["occasions"] else 0
        score -= len(set(c.lower() for c in (avoid_colors or [])) & set(p["colors"]))
        results.append((score, p))
    results.sort(key=lambda t: (-t[0], t[1]["price"]))
    return [_summary(p) | {"relevance": s} for s, p in results[:limit]]


def get_product(product_id: str) -> dict:
    p = get(product_id)
    if not p:
        return {"error": f"No product with id {product_id}"}
    return copy.deepcopy(p)


def check_stock(product_id: str, size: str) -> dict:
    p = get(product_id)
    if not p:
        return {"error": f"No product with id {product_id}"}
    qty = p["stock"].get(str(size), 0)
    return {"product_id": product_id, "size": size, "in_stock": qty > 0, "quantity": qty}


def reserve(product_id: str, size: str, qty: int) -> bool:
    p = get(product_id)
    if not p or p["stock"].get(size, 0) < qty:
        return False
    p["stock"][size] -= qty
    return True


def release(product_id: str, size: str, qty: int) -> None:
    p = get(product_id)
    if p:
        p["stock"][size] = p["stock"].get(size, 0) + qty

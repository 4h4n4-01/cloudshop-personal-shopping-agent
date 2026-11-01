"""Gold layer: the query-ready serving stores.

The analog of the MLE pipeline's feature store + label store:
  * catalog.json - the serving catalog the Stylist agent searches. Every silver field is
                   preserved (so the app is unchanged) plus a few derived fields the UI and
                   ranking can use directly: sizes_in_stock, in_stock, price_tier.
  * stock.json   - a per-product, per-size stock store (the "label store" counterpart),
                   kept as its own gold table for inspection and future stock accounting.
  * _manifest.json - counts and the category distribution: the pipeline's result summary.
"""
import json
import time
from pathlib import Path

from app.config import CATALOG_PATH, GOLD_DIR, STOCK_PATH
from app.models import ONE_SIZE_CATEGORIES, SHOE_CATEGORIES

# Fields embedded in the standalone catalogue viewer (a projection, to keep the file small).
_VIEW_FIELDS = ["id", "name", "brand", "section", "department", "category", "store", "price",
                "original_price", "colors", "emoji", "rating", "review_count", "origin", "in_stock",
                "sizes_in_stock", "free_shipping", "returnable", "badges", "price_tier", "fit",
                "silhouette", "sleeve_length", "neckline", "pattern", "hem_length", "rise",
                "heel_height", "materials", "occasions"]
_VIEWER_TEMPLATE = Path(__file__).parent / "catalog_viewer_template.html"


def _department(category: str) -> str:
    """Top-level department for partitioning the gold serving layer."""
    if category in SHOE_CATEGORIES:
        return "shoes"
    if category in ONE_SIZE_CATEGORIES:  # bags
        return "bags"
    return "clothing"


def _price_tier(price: float) -> str:
    return "budget" if price <= 60 else "mid" if price <= 120 else "premium"


# Emoji stand-in for a product photo: this is a licence-free prototype, so each item shows its
# category as an emoji tile instead of a copyrighted image. Swap for real images once licensed.
_EMOJI = {
    "dress": "👗", "blouse": "👚", "t-shirt": "👕", "shirt": "👔", "knitwear": "🧶",
    "hoodie": "🧥", "jacket": "🧥", "blazer": "🧥", "coat": "🧥", "jeans": "👖",
    "trousers": "👖", "skirt": "👗", "shorts": "🩳", "heels": "👠", "flats": "🥿",
    "loafers": "👞", "sneakers": "👟", "sandals": "🩴", "boots": "👢", "bag": "👜",
}


def build(clean: list[dict]) -> tuple[list[dict], dict]:
    catalog, stock_store = [], {}
    for p in clean:
        sizes_in_stock = [s for s, q in p["stock"].items() if q > 0]
        catalog.append({
            **p,
            "department": _department(p["category"]),
            "sizes_in_stock": sizes_in_stock,
            "in_stock": bool(sizes_in_stock) or p["category"] in ONE_SIZE_CATEGORIES,
            "price_tier": _price_tier(p["price"]),
            "emoji": _EMOJI.get(p["category"], "🛍️"),
        })
        stock_store[p["id"]] = p["stock"]
    return catalog, stock_store


def write(catalog: list[dict], stock_store: dict, report: dict) -> None:
    GOLD_DIR.mkdir(parents=True, exist_ok=True)
    # Combined serving catalog + stock store (what the app loads).
    CATALOG_PATH.write_text(json.dumps(catalog, indent=2), encoding="utf-8")
    STOCK_PATH.write_text(json.dumps(stock_store, indent=2), encoding="utf-8")

    # Partitioned serving views: gold/catalog/<department>/<section>.json
    # (department = clothing | shoes | bags). Mirrors how a warehouse partitions the serving
    # layer by the keys it is queried on; makes the inventory legible per department/section.
    partitions: dict[tuple[str, str], list[dict]] = {}
    for p in catalog:
        partitions.setdefault((p["department"], p["section"]), []).append(p)
    part_root = GOLD_DIR / "catalog"
    part_counts: dict[str, int] = {}
    for (dept, section), items in sorted(partitions.items()):
        d = part_root / dept
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{section}.json").write_text(json.dumps(items, indent=2), encoding="utf-8")
        part_counts[f"{dept}/{section}"] = len(items)

    # Standalone catalogue viewer: a single self-contained HTML file with the data embedded, so
    # it opens in any browser with no server (hand it to anyone who wants to inspect the catalogue).
    if _VIEWER_TEMPLATE.exists():
        slim = [{k: p.get(k) for k in _VIEW_FIELDS} for p in catalog]
        html = _VIEWER_TEMPLATE.read_text(encoding="utf-8").replace(
            "__CATALOG_DATA__", json.dumps(slim, ensure_ascii=False))
        (GOLD_DIR / "catalog_viewer.html").write_text(html, encoding="utf-8")

    by_category: dict[str, int] = {}
    for p in catalog:
        by_category[p["category"]] = by_category.get(p["category"], 0) + 1
    (GOLD_DIR / "_manifest.json").write_text(json.dumps({
        "built_at": time.time(),
        "products": len(catalog),
        "in_stock": sum(p["in_stock"] for p in catalog),
        "silver": report,
        "by_department_section": part_counts,
        "by_category": dict(sorted(by_category.items())),
    }, indent=2), encoding="utf-8")

"""Agent memory - IDEA: personalisation from the customer's choice history.

A returning customer has a profile derived from what they *bought / liked* (ranked up) and
*rejected* (filtered out) in the past, plus their sizes. The profile pre-fills the brief and biases
the Stylist, and is shown to the shopper so they can see and edit what is remembered (consent).

This is deliberately *not* fuzzy search: preferences are concrete, evidence-backed attributes
(colours, styles, materials, categories, sizes) aggregated from real past choices.
"""
import json
from collections import Counter

from .config import DATA_DIR
from .tools import catalog
from .models import ShoppingBrief

CUSTOMERS_PATH = DATA_DIR / "source" / "customers.json"
_CUSTOMERS: dict[str, dict] = {}


def _load() -> dict[str, dict]:
    if not _CUSTOMERS and CUSTOMERS_PATH.exists():
        for c in json.loads(CUSTOMERS_PATH.read_text(encoding="utf-8")):
            _CUSTOMERS[c["id"]] = c
    return _CUSTOMERS


def list_customers() -> list[dict]:
    """Only the featured demo personas are offered in the UI; the rest are the behavioural
    population used for collaborative filtering (see `neighbors`)."""
    return [{"id": c["id"], "name": c["name"], "section": c["section"]}
            for c in _load().values() if c.get("featured")]


def get(cid: str) -> dict | None:
    return _load().get(cid)


def _merge(*lists) -> list[str]:
    out, seen = [], set()
    for L in lists:
        for x in L or []:
            if x and x not in seen:
                seen.add(x)
                out.append(x)
    return out


def profile(cid: str) -> dict | None:
    """Derive the remembered preference profile from the customer's history + stated preferences."""
    c = get(cid)
    if not c:
        return None
    kept = [h for h in c["history"] if h["action"] != "rejected"]
    rejected = [h for h in c["history"] if h["action"] == "rejected"]
    kcol, ksty, kmat, kcat = Counter(), Counter(), Counter(), Counter()
    for h in kept:
        p = catalog.get(h["product_id"]) or {}
        kcol.update(p.get("colors", [])); ksty.update(p.get("style_tags", [])); kmat.update(p.get("materials", []))
        if p.get("category"):
            kcat[p["category"]] += 1
    rcol, rmat = Counter(), Counter()
    for h in rejected:
        p = catalog.get(h["product_id"]) or {}
        rcol.update(p.get("colors", [])); rmat.update(p.get("materials", []))

    colors_liked = _merge(c["likes"]["colors"], [x for x, _ in kcol.most_common()])
    styles = _merge(c["likes"]["styles"], [x for x, _ in ksty.most_common()])
    materials_liked = _merge(c["likes"].get("materials", []), [x for x, _ in kmat.most_common()])
    categories = _merge(c["likes"]["categories"], [x for x, _ in kcat.most_common()])
    # Avoids need a strong signal: an explicitly stated dislike, or an attribute seen in >=2
    # rejected items and never in a kept one (so one incidental colour isn't banned outright).
    colors_avoided = _merge(c["dislikes"]["colors"], [x for x, n in rcol.items() if n >= 2 and x not in kcol])
    materials_avoided = _merge(c["dislikes"]["materials"], [x for x, n in rmat.items() if n >= 2 and x not in kmat])

    return {
        "id": c["id"], "name": c["name"], "section": c["section"], "sizes": c["sizes"],
        "budget_hint": c.get("budget_hint", c.get("budget")), "colors_liked": colors_liked, "styles": styles,
        "materials_liked": materials_liked, "categories": categories,
        "colors_avoided": colors_avoided, "materials_avoided": materials_avoided,
        "evidence": {
            "kept": [{"name": h["name"], "action": h["action"]} for h in kept],
            "rejected": [h["name"] for h in rejected],
            "top_colors": [x for x, _ in kcol.most_common(3)],
            "top_styles": [x for x, _ in ksty.most_common(3)],
            "top_materials": [x for x, _ in kmat.most_common(3)],
        },
    }


def prefill_brief(prof: dict) -> ShoppingBrief:
    """Pre-fill a brief from the remembered profile; every filled field is tagged 'remembered'."""
    sizes = prof["sizes"]
    data = dict(
        section=prof["section"], size_top=sizes.get("size_top"), size_bottom=sizes.get("size_bottom"),
        size_shoe=sizes.get("size_shoe"), budget_total=prof["budget_hint"],
        colors_liked=prof["colors_liked"][:3], colors_avoided=prof["colors_avoided"],
        materials_avoided=prof["materials_avoided"], style=prof["styles"][:2],
    )
    prov = {k: "remembered" for k, v in data.items() if v not in (None, [], "")}
    return ShoppingBrief(**data, provenance=prov)


def reasons_for(product: dict, prof: dict) -> list[str]:
    """Why this product fits what we remember - shown on the pick to explain the personalisation."""
    r = []
    hit_c = sorted(set(product.get("colors", [])) & set(prof["colors_liked"]))
    if hit_c:
        r.append(f"in {', '.join(hit_c)}, a colour you've chosen before")
    hit_s = sorted(set(product.get("style_tags", [])) & set(prof["styles"]))
    if hit_s:
        r.append(f"{', '.join(hit_s)}, a style you like")
    hit_m = sorted(set(product.get("materials", [])) & set(prof["materials_liked"]))
    if hit_m:
        r.append(f"{', '.join(hit_m)}")
    return r


# --------------------------------------------------------------------------- #
# Collaborative filtering: find shoppers with a similar taste and what they chose.
# --------------------------------------------------------------------------- #
def _similarity(me: dict, other: dict) -> float:
    def jac(key: str) -> float:
        A, B = set(me["likes"].get(key, [])), set(other["likes"].get(key, []))
        return len(A & B) / len(A | B) if (A | B) else 0.0
    s = 2 * jac("colors") + 2 * jac("styles") + jac("materials") + jac("categories")
    if me["section"] == other["section"] or "unisex" in (me["section"], other["section"]):
        s += 0.5
    return s


def neighbors(cid: str, k: int = 25) -> list[tuple[float, dict]]:
    """The k customers whose taste is most similar to this one (by shared colours/styles/
    materials/categories and section)."""
    me = get(cid)
    if not me:
        return []
    scored = [(_similarity(me, c), c) for c in _load().values() if c["id"] != cid]
    scored.sort(key=lambda t: -t[0])
    return [(s, c) for s, c in scored[:k] if s > 0]


def collaborative_suggestions(cid: str, k: int = 25) -> dict[str, int]:
    """Products that similar shoppers bought/liked (and this shopper hasn't), with a count of how
    many of them chose each. "Shoppers like you also chose…", used to rank picks that fit the brief."""
    me = get(cid)
    if not me:
        return {}
    own = {h["product_id"] for h in me["history"]}
    counts: dict[str, int] = {}
    for _, nb in neighbors(cid, k):
        for h in nb["history"]:
            if h["action"] != "rejected" and h["product_id"] not in own:
                counts[h["product_id"]] = counts.get(h["product_id"], 0) + 1
    return counts


def remember(cid: str, product_id: str, action: str) -> None:
    """Record a new choice (e.g. the shopper added an item to the cart) into the customer's history."""
    c = get(cid)
    if not c:
        return
    p = catalog.get(product_id) or {}
    c["history"].append({"product_id": product_id, "name": p.get("name", product_id),
                         "category": p.get("category"), "action": action})

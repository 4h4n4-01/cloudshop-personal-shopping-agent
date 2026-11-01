"""Stylist Agent -: an agent that uses tools.

Searches the catalog with tools, checks stock in the shopper's size, and submits a shortlist
with a rationale for each pick. Its output is *not* trusted directly: every pick goes through
the Verifier Agent before the shopper sees it.
"""
from typing import Optional

from .. import harness, memory
from ..tools import catalog
from ..config import use_claude
from ..models import Recommendation, ShoppingBrief
from ..trace import Trace
from .intake import brief_to_prompt

AGENT = "Stylist"
MAX_PICKS = 6

SYSTEM = """You are the Stylist Agent of a clothes-shopping assistant. Use the tools to find
products that fit the shopper's brief, then call submit_recommendations exactly once.

- Only recommend products returned by search_catalog. Use check_stock with the shopper's size
  (size_top for tops/dresses/outerwear, size_bottom waist for jeans/trousers/shorts, size_shoe
  for footwear; bags are one size). Stores label shoe sizes EU, UK or US: pass check_stock the
  exact size label the product lists (e.g. "EU 38"), converting the shopper's size if needed.
- "shoes" in items_wanted means any footwear (sneakers, heels, flats, sandals, boots, loafers).
- Respect the budget, avoided colors and avoided materials.
- Pick up to 6 items; prefer variety across the categories the shopper asked for.
- Each rationale must be 1-2 sentences and cite only facts present in the product data
  (colors, materials, style_tags, occasions, price). Do not claim anything you cannot see.
"""

TOOLS = [
    {
        "name": "search_catalog",
        "description": "Search the clothing catalog. categories/section/max_price/exclude_materials are hard filters; colors/styles/occasion rank results.",
        "input_schema": {
            "type": "object",
            "properties": {
                "categories": {"type": "array", "items": {"type": "string"}},
                "section": {"type": "string", "enum": ["women", "men", "unisex"]},
                "max_price": {"type": "number"},
                "colors": {"type": "array", "items": {"type": "string"}},
                "styles": {"type": "array", "items": {"type": "string"}},
                "occasion": {"type": "string"},
                "exclude_materials": {"type": "array", "items": {"type": "string"}},
            },
        },
    },
    {
        "name": "get_product",
        "description": "Get full details for one product by id.",
        "input_schema": {
            "type": "object",
            "properties": {"product_id": {"type": "string"}},
            "required": ["product_id"],
        },
    },
    {
        "name": "check_stock",
        "description": "Check whether a product is in stock in a given size.",
        "input_schema": {
            "type": "object",
            "properties": {"product_id": {"type": "string"}, "size": {"type": "string"}},
            "required": ["product_id", "size"],
        },
    },
    {
        "name": "submit_recommendations",
        "description": "Submit the final shortlist. Call once, at the end.",
        "input_schema": {
            "type": "object",
            "properties": {
                "picks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "product_id": {"type": "string"},
                            "size": {"type": "string"},
                            "rationale": {"type": "string"},
                            "matched_preferences": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["product_id", "size", "rationale", "matched_preferences"],
                    },
                }
            },
            "required": ["picks"],
        },
    },
]


def run(brief: ShoppingBrief, feedback: Optional[str], trace: Trace,
        previous_ids: tuple[str, ...] = (), profile: Optional[dict] = None) -> list[Recommendation]:
    if use_claude():
        return _run_claude(brief, feedback, trace, previous_ids, profile)
    return _run_mock(brief, feedback, trace, previous_ids, profile)


def _run_claude(brief, feedback, trace, previous_ids, profile=None) -> list[Recommendation]:
    submitted: list[Recommendation] = []

    def submit(picks: list[dict]):
        submitted.clear()
        for p in picks[:MAX_PICKS]:
            submitted.append(Recommendation(**p))
        return {"ok": True, "received": len(submitted)}

    handlers = {
        "search_catalog": catalog.search_catalog,
        "get_product": catalog.get_product,
        "check_stock": catalog.check_stock,
        "submit_recommendations": submit,
    }
    user = f"Shopping brief:\n{brief_to_prompt(brief)}"
    if profile:
        user += (f"\n\nReturning customer. They have previously chosen colours {profile['colors_liked']}, "
                 f"styles {profile['styles']} and avoid {profile['colors_avoided']} / {profile['materials_avoided']}. "
                 "Prefer items matching these when the brief doesn't say otherwise, and mention the connection.")
    if feedback:
        user += f"\n\nThe shopper saw your previous picks and said: {feedback}"
        if previous_ids:
            user += f"\nPrevious picks: {', '.join(previous_ids)}. Prefer different items."
    trace.log(AGENT, "llm", "Claude stylist started", {"feedback": feedback})
    harness.run_tools(SYSTEM, user, TOOLS, handlers,
                  on_tool=lambda n, a, o: trace.log(AGENT, "tool", n, {"input": a, "output": _clip(o)}),
                  stop_tool="submit_recommendations")
    trace.log(AGENT, "decision", f"Submitted {len(submitted)} picks", [p.model_dump() for p in submitted])
    return submitted


def _run_mock(brief, feedback, trace, previous_ids, profile=None) -> list[Recommendation]:
    max_price = brief.budget_per_item or brief.budget_total
    query = dict(
        categories=brief.items_wanted or None, section=brief.section, max_price=max_price,
        colors=brief.colors_liked, styles=brief.style, occasion=brief.occasion,
        exclude_materials=brief.materials_avoided, avoid_colors=brief.colors_avoided,
    )
    results = catalog.search_catalog(**query)
    trace.log(AGENT, "tool", "search_catalog", {"input": query, "output": _clip(results)})

    # Collaborative filtering: among items that fit the brief, rank first the ones shoppers with a
    # similar taste have chosen. This only ever promotes items that already match the brief.
    collab = memory.collaborative_suggestions(profile["id"]) if profile else {}
    if collab:
        results.sort(key=lambda r: (-collab.get(r["id"], 0), -r.get("relevance", 0), r["price"]))
        n_pop = sum(1 for r in results if collab.get(r["id"]))
        trace.log(AGENT, "decision", f"Re-ranked by taste: {n_pop} matching item(s) chosen by similar shoppers")

    # crude refinement: when the shopper asks for alternatives, skip what they already saw
    seen_ids = set(previous_ids) if feedback else set()
    seen_names: set = set()  # live stores often list each colourway as its own product
    picks: list[Recommendation] = []
    per_cat: dict[str, int] = {}
    for r in results:
        if len(picks) >= MAX_PICKS or r["id"] in seen_ids or (r["store"], r["name"]) in seen_names:
            continue
        if r["colors"] and set(r["colors"]) <= set(brief.colors_avoided or []):
            continue
        p = catalog.get(r["id"])
        size = catalog.size_for(p, brief)
        if size:
            stock = catalog.check_stock(r["id"], size)
            trace.log(AGENT, "tool", "check_stock", {"input": {"product_id": r["id"], "size": size}, "output": stock})
            if stock["in_stock"] is False:  # None = the store doesn't say; the Verifier flags it
                continue
        if per_cat.get(r["category"], 0) >= 3:
            continue
        per_cat[r["category"]] = per_cat.get(r["category"], 0) + 1
        seen_names.add((r["store"], r["name"]))
        matched = [c for c in r["colors"] if c in brief.colors_liked] + \
                  [s for s in r["style_tags"] if s in brief.style] + \
                  ([brief.occasion] if brief.occasion in r["occasions"] else [])
        why = r["name"] + (f" in {'/'.join(r['colors'])}" if r["colors"] else "") + \
            (f", {', '.join(r['materials'])}" if r["materials"] else "") + f", S${r['price']:.0f}"
        if matched:
            why += f"; matches your {', '.join(matched)} preference"
        if profile:
            rs = memory.reasons_for(p, profile)
            if rs:
                why += ". From your history: " + "; ".join(rs)
                matched = matched + ["remembered"]
        if collab.get(r["id"]):
            why += f". Popular with {collab[r['id']]} shopper(s) who share your taste"
            matched = matched + ["similar shoppers"]
        picks.append(Recommendation(product_id=r["id"], size=size or "", rationale=why + ".",
                                    matched_preferences=matched))
    trace.log(AGENT, "decision", f"Submitted {len(picks)} picks", [p.model_dump() for p in picks])
    return picks


def _clip(obj, n: int = 6):
    return obj[:n] + [f"... {len(obj) - n} more"] if isinstance(obj, list) and len(obj) > n else obj

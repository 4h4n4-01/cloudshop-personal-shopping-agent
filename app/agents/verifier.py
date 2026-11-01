"""Verifier Agent - IDEA 2: supporting users in verifying AI output.

Every recommendation is checked against the shopper's brief *and* the raw product record:
  1. Hard-constraint checks (deterministic code, never an LLM): category, section, budget,
     size in stock, avoided colors, avoided materials. Each check carries its evidence.
  2. Claim grounding: each claim in the Stylist's rationale is checked against product data
     (Claude as a critic, or a keyword heuristic in mock mode).
The GUI shows the checklist on every card, so the shopper can see *why* an item is trusted
instead of taking the Stylist's word for it.
"""
import json
import re

from .. import llm
from ..tools import catalog, sizes
from ..config import use_claude
from ..models import (
    COLORS, OCCASIONS, SHOE_CATEGORIES, STYLES, Check, ClaimCheckOutput, ClaimVerdict, Recommendation,
    ShoppingBrief, VerifiedRecommendation, matches_category,
)
from ..trace import Trace

AGENT = "Verifier"

CLAIM_SYSTEM = """You are a Verifier Agent. For each product, split the stylist's rationale into
atomic factual claims about the product (color, material, fabric composition, care, origin, style,
occasion, price, sale/discount, returns, rating, fit, stock). Mark a claim supported only if the
product JSON directly supports it; give the JSON evidence or explain what is missing. Treat a claim
like "pure linen" or "100% cotton" as supported only if fabric_composition is 100% that fibre, and a
claim like "machine washable" as supported only if the care field says so. Ignore pure opinions
("looks great")."""


def run(recs: list[Recommendation], brief: ShoppingBrief, trace: Trace) -> list[VerifiedRecommendation]:
    out: list[VerifiedRecommendation] = []
    for r in recs:
        p = catalog.get(r.product_id)
        if p is None:
            trace.log(AGENT, "error", f"Stylist recommended unknown product {r.product_id}")
            continue
        size = r.size or catalog.size_for(p, brief)
        if p["category"] in SHOE_CATEGORIES and size and size not in p["stock"]:
            # the stylist may say "38" where the store lists "EU 38" or "US 7.5"
            size = sizes.pick(p["stock"], size, brief.section, p["section"]) or size
        out.append(VerifiedRecommendation(
            product=p, size=size, rationale=r.rationale, matched_preferences=r.matched_preferences,
            checks=constraint_checks(p, size, brief), verdict="pass",
        ))

    claims = _claim_check(out, trace)
    for v in out:
        v.claims = claims.get(v.product["id"], [])
        if any(not c.supported for c in v.claims):
            bad = [c.claim for c in v.claims if not c.supported]
            v.checks.append(Check(name="Rationale grounded", status="warn",
                                  evidence="Unsupported: " + "; ".join(bad)))
        elif v.claims:
            v.checks.append(Check(name="Rationale grounded", status="pass",
                                  evidence=f"{len(v.claims)} claim(s) match product data"))
        statuses = {c.status for c in v.checks}
        v.verdict = "fail" if "fail" in statuses else "warn" if "warn" in statuses else "pass"

    summary = {k: sum(v.verdict == k for v in out) for k in ("pass", "warn", "fail")}
    trace.log(AGENT, "decision", f"Verified {len(out)} picks: {summary}",
              [{"id": v.product["id"], "verdict": v.verdict,
                "failed": [c.name for c in v.checks if c.status != "pass"]} for v in out])
    return out


def constraint_checks(p: dict, size: str | None, b: ShoppingBrief) -> list[Check]:
    checks: list[Check] = []
    if b.items_wanted:
        ok = matches_category(p["category"], b.items_wanted)
        checks.append(Check(name="Category", status="pass" if ok else "fail",
                            evidence=f"Item is a {p['category']}; you asked for {', '.join(b.items_wanted)}"))
    if b.section and b.section != "unisex":
        ok = p["section"] in (b.section, "unisex")
        checks.append(Check(name="Section", status="pass" if ok else "fail",
                            evidence=f"Listed under {p['section']}"))

    limit = b.budget_per_item or b.budget_total
    if limit is not None:
        ok = p["price"] <= limit
        kind = "per-item budget" if b.budget_per_item else "total budget"
        checks.append(Check(name="Budget", status="pass" if ok else "fail",
                            evidence=f"S${p['price']:.2f} vs {kind} S${limit:.2f}"))
    else:
        checks.append(Check(name="Budget", status="warn", evidence="No budget set"))

    if size:
        qty = p["stock"].get(str(size), 0)
        status = "fail" if qty == 0 else "warn" if qty <= 1 else "pass"
        note = "out of stock" if qty == 0 else f"{qty} left"
        checks.append(Check(name="Size in stock", status=status, evidence=f"Size {size}: {note}"))
    else:
        checks.append(Check(name="Size in stock", status="warn",
                            evidence=f"Your size is unknown; available: {', '.join(s for s, q in p['stock'].items() if q)}"))

    if p["category"] in SHOE_CATEGORIES and b.size_shoe:
        # show the size conversion, so the shopper can check it rather than trust it
        note, exact = sizes.explain(b.size_shoe, size if size in p["stock"] else None, b.section, p["section"])
        c = next(c for c in checks if c.name == "Size in stock")
        c.evidence = f"{note}. {c.evidence}"
        if not exact and c.status == "pass":
            c.status = "warn"

    bad_colors = set(p["colors"]) & set(b.colors_avoided)
    if not p["colors"] and (b.colors_liked or b.colors_avoided):
        checks.append(Check(name="Colors", status="warn", evidence="Colour not stated in the listing; check the store page"))
    elif bad_colors and set(p["colors"]) <= bad_colors:
        checks.append(Check(name="Colors", status="fail", evidence=f"Only comes in {', '.join(p['colors'])}, which you avoid"))
    elif bad_colors:
        checks.append(Check(name="Colors", status="warn", evidence=f"Also comes in avoided {', '.join(bad_colors)}; pick another colorway"))
    elif b.colors_liked and not set(p["colors"]) & set(b.colors_liked):
        checks.append(Check(name="Colors", status="warn", evidence=f"{', '.join(p['colors'])} - not one of your listed colors"))
    elif b.colors_liked:
        checks.append(Check(name="Colors", status="pass", evidence=f"Available in {', '.join(set(p['colors']) & set(b.colors_liked))}"))

    if p.get("final_sale") or p.get("returnable") is False:
        checks.append(Check(name="Returns", status="warn",
                            evidence="Listed as FINAL SALE / non-returnable, so it probably can't be returned"))

    if p.get("original_price") and p["original_price"] > p["price"]:
        off = round(100 * (1 - p["price"] / p["original_price"]))
        checks.append(Check(name="Price drop", status="pass",
                            evidence=f"On sale: S${p['price']:.2f}, down {off}% from S${p['original_price']:.2f}"))

    if b.materials_avoided and not p["materials"]:
        checks.append(Check(name="Materials", status="warn",
                            evidence="Materials not listed; check the store page before buying"))
    elif b.materials_avoided:
        bad = set(p["materials"]) & set(m.lower() for m in b.materials_avoided)
        checks.append(Check(name="Materials", status="fail" if bad else "pass",
                            evidence=f"Contains {', '.join(bad)}" if bad else f"Made of {', '.join(p['materials'])}"))
    return checks


def _claim_check(items: list[VerifiedRecommendation], trace: Trace) -> dict[str, list[ClaimVerdict]]:
    if not items:
        return {}
    if use_claude():  # LLM critic only on the fast cloud model; Ollama/mock use the instant heuristic
        payload = [{"product_id": v.product["id"], "product": v.product, "rationale": v.rationale} for v in items]
        out = llm.parse(CLAIM_SYSTEM, json.dumps(payload), ClaimCheckOutput)
        trace.log(AGENT, "llm", "LLM checked rationale claims", out.model_dump())
        return {i.product_id: i.claims for i in out.items}
    result = {v.product["id"]: _heuristic_claims(v.product, v.rationale) for v in items}
    trace.log(AGENT, "llm", "Heuristic claim check (mock mode)",
              {k: [c.model_dump() for c in v] for k, v in result.items()})
    return result


_MATERIAL_WORDS = ["linen", "cotton", "silk", "wool", "polyester", "viscose", "nylon", "acrylic", "elastane", "merino",
                   "hemp", "cashmere", "modal", "lyocell", "leather"]


def _heuristic_claims(p: dict, rationale: str) -> list[ClaimVerdict]:
    """Every color/material/style/occasion word or price mentioned must appear in the record."""
    text = rationale.lower()
    materials = p["materials"] + (["merino"] if "wool" in p["materials"] else [])
    tags = set(p["colors"]) | set(materials) | set(p["style_tags"]) | set(p["occasions"])
    vocab = dict.fromkeys(COLORS + _MATERIAL_WORDS + STYLES + OCCASIONS)  # ordered, de-duplicated
    claims = []
    for w in vocab:
        if re.search(rf"\b{re.escape(w)}\b", text):
            claims.append(ClaimVerdict(claim=w, supported=w in tags,
                                       evidence="found in product tags" if w in tags else
                                       f"not in colors {p['colors']}, materials {materials}, "
                                       f"styles {p['style_tags']} or occasions {p['occasions']}"))
    for m in re.findall(r"\$(\d+(?:\.\d+)?)", rationale):
        ok = abs(float(m) - p["price"]) < 1
        claims.append(ClaimVerdict(claim=f"price: S${m}", supported=ok, evidence=f"price = S${p['price']}"))

    # precision fabric claims ("pure linen", "100% cotton", "all wool") vs fabric_composition
    comp = (p.get("fabric_composition") or "").lower()
    for mat in re.findall(r"(?:pure|100%|all|entirely|fully)\s+([a-z]+)", text):
        if mat in _MATERIAL_WORDS:
            claims.append(ClaimVerdict(claim=f"pure {mat}", supported=comp.startswith(f"100% {mat}"),
                                       evidence=f"fabric: {p.get('fabric_composition') or 'not listed'}"))
    # care claims vs the care field
    care = (p.get("care") or "").lower()
    if re.search(r"machine[\s-]?wash", text):
        claims.append(ClaimVerdict(claim="machine washable", supported="machine wash" in care,
                                   evidence=f"care: {p.get('care') or 'not listed'}"))
    if re.search(r"dry[\s-]?clean", text) and "machine" not in text:
        claims.append(ClaimVerdict(claim="dry clean only", supported="dry clean" in care,
                                   evidence=f"care: {p.get('care') or 'not listed'}"))
    # origin claims ("Made in Italy", "from Portugal") - match the prefix in either case,
    # but keep the captured country capitalised so "from the ..." isn't read as a country
    origin = p.get("origin") or ""
    for c in re.findall(r"(?:[Mm]ade in|[Ff]rom)\s+([A-Z][a-zA-Z]+)", rationale):
        claims.append(ClaimVerdict(claim=f"made in {c}", supported=(c.lower() == origin.lower()),
                                   evidence=f"origin: {origin or 'not listed'}"))
    # sale/discount claims vs original_price
    if re.search(r"\b(on sale|discount|marked down|reduced|% off|markdown)\b", text):
        claims.append(ClaimVerdict(claim="on sale", supported=bool(p.get("original_price")),
                                   evidence=f"original_price: {p.get('original_price') or 'none (not on sale)'}"))
    return claims

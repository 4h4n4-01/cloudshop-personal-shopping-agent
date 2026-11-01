"""Intake Agent ("Brief Builder") - IDEA 1: making intent expression easier.

Turns a vague free-text request into a structured, *editable* shopping brief, records which
fields the shopper stated vs. which the AI inferred, and - instead of asking open-ended
follow-up questions - offers clickable option chips for the few gaps that matter.
"""
import json
import re
from typing import Optional

from .. import llm
from ..config import use_llm
from ..models import (
    BRIEF_FIELDS, CATEGORIES, COLORS, OCCASIONS, STYLES, BOTTOM_CATEGORIES, NON_APPAREL, SHOE_CATEGORIES,
    Clarification, IntakeLLMOutput, ShoppingBrief,
)
from ..trace import Trace

AGENT = "Intake"

SYSTEM = f"""You are the Intake Agent of a clothes-shopping assistant. Your job is to help the
shopper cross the "gulf of envisioning": turn a vague, under-specified request into a structured,
editable shopping brief. If a current brief is given, update it (new information wins; keep the rest).

Real shoppers phrase requests in predictable styles. Recognise and NORMALISE each one before you
extract, the meaning is what matters, not the surface form:
- Terse keyword ("black jeans"): extract the item; the gaps become clarifications.
- Polite / social-lens ("Hi, could you please help me find a nice dress? Thanks 😊"): strip the
  greeting, "please", "thank you" and emoji. Politeness must NEVER change what you extract.
- Occasion-first ("something for a summer wedding"): infer a likely category and style from the
  occasion and LIST THEM in inferred_fields, then ask for the deciding criteria.
- Vague adjective ("elegant", "cool", "smart", "comfy", "fancy", "simple"): map it onto the style
  vocabulary below; if it is too ambiguous to map, ask with concrete options.
- Negation / constraint ("no polyester", "nothing black", "not into bright colours", "anything but
  skinny"): route the negated colour/material into colors_avoided / materials_avoided, NOT liked.
- Multi-item ("a dress and heels to match"): put every piece in items_wanted; record the
  "to match" relationship in notes.
- Shorthand / typo ("blk dress sz M summr"): read common abbreviations and misspellings.

Vocabulary (map the shopper's words onto these where possible):
- categories: {CATEGORIES}
- colors: {COLORS}
- styles: {STYLES}
- occasions: {OCCASIONS}
- section: women | men | unisex
- size_top uses XS/S/M/L/XL/XXL; size_bottom is a waist size such as 28 or 32; size_shoe is the
  shoe size exactly as the shopper gave it, keeping the system ("EU 38", "UK 5", "US 7"). Bags
  need no size. "shoes" means any footwear; use a specific type (heels, sneakers...) if named.

Rules:
1. Never invent a size or a budget. If the shopper did not give one, leave it null and ask.
2. List in inferred_fields every field you filled by inference instead of explicit words
   (e.g. inferring "elegant" style from "wedding"). The shopper can see and override these, so
   do not silently expand their request, surface your inferences.
3. Ask at most 3 clarifications, only for gaps that would change which products are shown
   (size for the categories wanted, including shoe size for footwear, budget, section, a vague
   style). Each needs 2-4 short concrete options the shopper can click, and a `gap` tag naming
   which envisioning gap it crosses: "capability" (what to even specify, e.g. the item itself),
   "instruction" (phrase a vague word as a concrete property, e.g. a fuzzy style or fabric), or
   "intentionality" (decide what a good result is, e.g. occasion or budget). Do not ask about
   fields that are already filled.
4. Put anything that does not fit a field into notes.
"""


def run(message: str, current: Optional[ShoppingBrief], trace: Trace) -> tuple[ShoppingBrief, list[Clarification], str]:
    trace.log(AGENT, "human", "Shopper request", message)
    if use_llm():
        user = message if current is None else (
            f"Current brief:\n{current.model_dump_json(exclude={'provenance'})}\n\nNew message: {message}"
        )
        out = llm.parse(SYSTEM, user, IntakeLLMOutput)
        out = _backfill(out, message, current)  # safety-net: recover fields the LLM dropped
        trace.log(AGENT, "llm", "LLM produced structured brief (+ deterministic backfill)", out.model_dump())
    else:
        out = _mock_parse(message, current)
        trace.log(AGENT, "llm", "Mock parser produced structured brief", out.model_dump())

    brief = _to_brief(out, current)
    # Don't ask a clarification the brief now answers (e.g. don't ask size after backfilling "XL").
    filled = {f for f in BRIEF_FIELDS if getattr(brief, f, None) not in (None, [], "")}
    clar = [c for c in out.clarifications if c.field in BRIEF_FIELDS and c.field not in filled][:3]
    trace.log(AGENT, "decision", f"{len(clar)} clarification(s) offered", [c.model_dump() for c in clar])
    return brief, clar, out.summary


def _backfill(out: IntakeLLMOutput, message: str, current: Optional[ShoppingBrief]) -> IntakeLLMOutput:
    """Safety-net for the LLM path. A capable model writes a great reply but sometimes omits a field
    the shopper stated plainly ("size XL", "a dress", a second avoided colour). Run the deterministic
    parser on the same message and reconcile:
      - scalars + liked/style: fill ONLY where the LLM left them empty (never override the LLM);
      - additive constraints (items_wanted, colors_avoided, materials_avoided): UNION, so every
        stated item/avoidance survives even if the LLM caught only some;
      - notes: append deterministic cues the LLM didn't already mention.
    Deterministic extractions come from the shopper's own words, so they count as 'stated' unless the
    mock itself marked them inferred (e.g. an adjective mapped to a style)."""
    mock = _mock_parse(message, current)
    d, m = out.model_dump(), mock.model_dump()
    inferred = list(out.inferred_fields)

    def empty(v) -> bool:
        return v in (None, [], "")

    for f in ("section", "occasion", "size_top", "size_bottom", "size_shoe",
              "budget_total", "budget_per_item", "colors_liked", "style"):
        if empty(d.get(f)) and not empty(m.get(f)):
            d[f] = m[f]
            if f in mock.inferred_fields and f not in inferred:
                inferred.append(f)

    liked = set(d.get("colors_liked") or [])
    for f in ("items_wanted", "colors_avoided", "materials_avoided"):
        merged = list(d.get(f) or [])
        for v in (m.get(f) or []):
            if v not in merged and not (f == "colors_avoided" and v in liked):
                merged.append(v)
        d[f] = merged

    if m.get("notes"):
        existing = d.get("notes") or ""
        add = [n.strip() for n in m["notes"].split(";")
               if n.strip() and n.strip().lower() not in existing.lower()]
        if add:
            d["notes"] = "; ".join(([existing] if existing else []) + add)

    d["inferred_fields"] = inferred
    return IntakeLLMOutput(**d)


def _to_brief(out: IntakeLLMOutput, current: Optional[ShoppingBrief]) -> ShoppingBrief:
    data = out.model_dump(exclude={"inferred_fields", "clarifications", "summary"})
    data["items_wanted"] = [c for c in data["items_wanted"] if c in CATEGORIES]
    prov = dict(current.provenance) if current else {}
    old = current.model_dump() if current else {}
    for f in BRIEF_FIELDS:
        val = data.get(f)
        if val in (None, [], ""):
            prov.pop(f, None)
        elif val != old.get(f) or f not in prov:
            prov[f] = "inferred" if f in out.inferred_fields else "stated"
    return ShoppingBrief(**data, provenance=prov)


def apply_answer(brief: ShoppingBrief, field: str, value: str, trace: Trace) -> ShoppingBrief:
    """A clicked clarification option is applied deterministically - no LLM round trip."""
    data = brief.model_dump()
    if field in ("budget_total", "budget_per_item"):
        nums = re.findall(r"\d+(?:\.\d+)?", value)
        data[field] = float(nums[-1]) if nums else None
    elif isinstance(data.get(field), list):
        data[field] = [v.strip().lower() for v in re.split(r",|/| and ", value) if v.strip()]
    else:
        data[field] = value.strip() if field.startswith("size") else value.strip().lower()
    data["provenance"][field] = "stated"
    trace.log(AGENT, "human", f"Shopper answered clarification: {field}", value)
    return ShoppingBrief(**data)


def missing_clarifications(brief: ShoppingBrief) -> list[Clarification]:
    """Rule-based gap finder (used by the mock agent and as a safety net)."""
    out: list[Clarification] = []
    wants_top = not brief.items_wanted or any(c not in BOTTOM_CATEGORIES | NON_APPAREL for c in brief.items_wanted)
    wants_bottom = any(c in BOTTOM_CATEGORIES for c in brief.items_wanted)
    wants_shoes = any(c in SHOE_CATEGORIES for c in brief.items_wanted)
    if not brief.items_wanted:
        out.append(Clarification(field="items_wanted", question="What kind of piece are you after?",
                                 options=["dress", "shirt", "jeans", "jacket"], gap="capability"))
    if wants_top and not brief.size_top:
        out.append(Clarification(field="size_top", question="What size do you usually wear on top?",
                                 options=["S", "M", "L", "XL"], gap="instruction"))
    if wants_bottom and not brief.size_bottom:
        out.append(Clarification(field="size_bottom", question="What's your waist size?",
                                 options=["28", "30", "32", "34"], gap="instruction"))
    if wants_shoes and not brief.size_shoe:
        out.append(Clarification(field="size_shoe", question="What's your shoe size? (EU, UK or US is fine)",
                                 options=["EU 37", "EU 38", "EU 40", "EU 42"], gap="instruction"))
    if brief.budget_total is None and brief.budget_per_item is None:
        out.append(Clarification(field="budget_total", question="What would you like to spend in total?",
                                 options=["Under $50", "Under $100", "Under $200", "Under $400"], gap="intentionality"))
    if brief.section is None:
        out.append(Clarification(field="section", question="Which section should I shop?",
                                 options=["women", "men", "unisex"], allow_free_text=False, gap="capability"))
    return out[:3]


# --------------------------------------------------------------------------- #
# Deterministic offline parser (mock mode + evaluation baseline)
# --------------------------------------------------------------------------- #
_CAT_WORDS = {
    "dress": "dress", "dresses": "dress", "tee": "t-shirt", "t-shirt": "t-shirt", "tshirt": "t-shirt",
    "tank": "t-shirt", "shirt": "shirt", "polo": "shirt", "blouse": "blouse", "top": "blouse",
    "sweater": "knitwear", "jumper": "knitwear", "cardigan": "knitwear", "knit": "knitwear",
    "hoodie": "hoodie", "sweatshirt": "hoodie", "jacket": "jacket", "blazer": "blazer",
    "suit jacket": "blazer", "coat": "coat", "puffer": "coat", "jeans": "jeans", "denim": "jeans",
    "trousers": "trousers", "pants": "trousers", "chinos": "trousers", "joggers": "trousers",
    "skirt": "skirt", "shorts": "shorts",
    "shoes": "shoes", "shoe": "shoes", "footwear": "shoes", "sneakers": "sneakers", "sneaker": "sneakers",
    "trainers": "sneakers", "heels": "heels", "pumps": "heels", "stilettos": "heels", "flats": "flats",
    "ballerinas": "flats", "mary janes": "flats", "loafers": "loafers", "loafer": "loafers",
    "sandals": "sandals", "sandal": "sandals", "slides": "sandals", "mules": "sandals", "boots": "boots",
    "bag": "bag", "bags": "bag", "handbag": "bag", "tote": "bag", "crossbody": "bag", "backpack": "bag",
    "clutch": "bag", "purse": "bag",
}
_OCC_WORDS = {
    "wedding": "wedding-guest", "office": "office", "work": "office", "interview": "interview",
    "meeting": "office", "client": "office", "clients": "office", "conference": "office",
    "presentation": "office", "business": "office",
    "date": "date", "party": "party", "club": "party", "clubby": "party", "clubbing": "party",
    "beach": "beach", "vacation": "beach", "holiday": "beach",
    "gym": "gym", "summer": "summer", "winter": "winter", "travel": "travel", "hiking": "outdoor",
}
# Free-text feelings/constraints that don't map to a vocab field but should be captured in notes,
# so the shopper sees that the agent heard them (the real LLM extracts these more fully).
_NOTE_CUES = [
    (r"\b(not too tight|nothing tight|not tight|loose|relaxed fit|comfy|comfortable)\b", "prefers a relaxed, non-tight fit"),
    (r"\b(sequin|sequins|sparkl|glitter|rhinestone)\b", "no sequins / sparkle"),
    (r"\bdark\b", "leaning toward dark shades"),
]
_OCC_STYLE = {"wedding-guest": "elegant", "interview": "classic", "party": "evening", "gym": "sporty"}
_MATERIALS = ["polyester", "wool", "linen", "silk", "cotton", "nylon", "acrylic", "viscose", "elastane",
              "hemp", "cashmere", "modal", "lyocell", "leather"]

# Vague adjectives (the "instruction gap") -> the controlled style vocabulary. Only SYNONYMS that
# are not themselves style words; the exact vocab words are matched directly by the styles regex.
_ADJ_STYLE = {
    "classy": "elegant", "dressy": "elegant", "sophisticated": "elegant", "chic": "elegant",
    "smart": "classic", "sharp": "classic", "polished": "classic", "timeless": "classic",
    "cool": "streetwear", "edgy": "streetwear", "trendy": "streetwear", "street": "streetwear",
    "comfy": "cozy", "comfortable": "cozy", "cosy": "cozy", "snug": "cozy",
    "simple": "minimalist", "clean": "minimalist", "understated": "minimalist", "plain": "minimalist",
    "fancy": "evening", "glam": "evening", "glamorous": "evening", "dressed up": "evening",
    "cute": "romantic", "pretty": "romantic", "feminine": "romantic", "girly": "romantic",
    "athletic": "sporty", "active": "sporty",
    "retro": "vintage", "old school": "vintage",
    "laid back": "casual", "chill": "casual", "relaxed": "casual",
}
# Common shorthand / misspellings normalised before extraction (the "shorthand" prompt style).
_ABBREV = {
    "blk": "black", "wht": "white", "gry": "grey", "nvy": "navy",
    "sz": "size", "summr": "summer", "wtr": "winter", "jkt": "jacket", "sneaks": "sneakers",
}


def _mock_parse(message: str, current: Optional[ShoppingBrief]) -> IntakeLLMOutput:
    text = " " + message.lower().replace("-", " ").replace("t shirt", "t-shirt") + " "
    # Normalise shorthand/typos before extraction (the "shorthand" prompt style).
    text = re.sub(r"\b(" + "|".join(_ABBREV) + r")\b", lambda m: _ABBREV[m.group(1)], text)
    b = (current or ShoppingBrief()).model_dump()
    inferred: list[str] = []

    # (?<![\w-]) so "shirt" does not also match inside "t-shirt"
    cats = [c for w, c in _CAT_WORDS.items() if re.search(rf"(?<![\w-]){re.escape(w)}\b", text)]
    if cats:
        if "shoes" in cats and set(cats) & (SHOE_CATEGORIES - {"shoes"}):
            cats = [c for c in cats if c != "shoes"]  # "shoes, heels" -> the specific type
        b["items_wanted"] = sorted(set(cats), key=cats.index)

    if re.search(r"\b(women|woman|womens|her|ladies|female)\b", text):
        b["section"] = "women"
    elif re.search(r"\b(men|man|mens|him|guys|male)\b", text):
        b["section"] = "men"

    # Negation / constraint handling. A trigger opens a scope; EVERY colour/material named inside
    # that scope is treated as avoided. The scope runs from the trigger to a boundary (a contrast
    # word like "but", a positive desire like "want"/"prefer", or the end of the clause), so forms
    # with a verb and multiple values work: "don't wanna wear black and white", "no polyester or
    # wool", "nothing black". The old regex only captured the one word right after the trigger.
    avoided_colors, avoided_mats = [], []
    neg_trigger = re.compile(
        r"\b(?:no|not|nothing|avoid|without|hate|skip|dislike|"
        r"don'?t\s+(?:want|wanna|like|wear)|dont\s+wanna|wanna\s+avoid|not\s+into|"
        r"rather\s+not|anything\s+but|steer\s+clear\s+of|stay\s+away\s+from)\b")
    neg_boundary = re.compile(
        r"\b(?:but|however|though|instead|prefer|want|wants|like|likes|love|need|needs)\b|[.;]")
    for m in neg_trigger.finditer(text):
        rest = text[m.end():]
        bnd = neg_boundary.search(rest)
        scope = (rest[:bnd.start()] if bnd else rest)[:40]  # keep the negation to its own clause
        for c in COLORS:
            if re.search(rf"\b{c}\b", scope) and c not in avoided_colors:
                avoided_colors.append(c)
        for mat in _MATERIALS:
            if re.search(rf"\b{mat}\b", scope) and mat not in avoided_mats:
                avoided_mats.append(mat)
    liked = [c for c in COLORS if re.search(rf"\b{c}\b", text) and c not in avoided_colors]
    if liked:
        b["colors_liked"] = liked
    if avoided_colors:
        b["colors_avoided"] = avoided_colors
    if avoided_mats:
        b["materials_avoided"] = avoided_mats

    styles = [s for s in STYLES if re.search(rf"\b{s}\b", text)]
    # Map vague adjectives ("classy", "comfy", "smart"...) onto the style vocabulary (instruction gap).
    for adj, mapped in _ADJ_STYLE.items():
        if re.search(rf"\b{re.escape(adj)}\b", text) and mapped not in styles:
            styles.append(mapped)
            inferred.append("style")  # surfaced, not silently expanded
    if styles:
        b["style"] = sorted(set(styles), key=styles.index)

    # Multi-item "to match" relationship (the "multi" prompt style): keep it in notes.
    if len(b["items_wanted"]) > 1 and re.search(r"\b(match|matching|go with|goes with)\b", text):
        note = "Shopper wants the pieces to match each other."
        b["notes"] = (b["notes"] + " " + note).strip() if b.get("notes") else note

    # Capture feelings/constraints that have no vocab field (fit, embellishments, "dark") in notes.
    cues = [note for pat, note in _NOTE_CUES if re.search(pat, text)]
    if cues:
        existing = [b["notes"]] if b.get("notes") else []
        b["notes"] = "; ".join(existing + cues)
    for w, occ in _OCC_WORDS.items():
        if re.search(rf"\b{w}\b", text):
            b["occasion"] = occ
            if not b["style"] and occ in _OCC_STYLE:
                b["style"] = [_OCC_STYLE[occ]]
                inferred.append("style")
            break

    m = re.search(r"\bsize\s+(xxs|xs|s|m|l|xl|xxl|small|medium|large)\b", text) or \
        re.search(r"\b(xxs|xs|xl|xxl)\b", text)
    if m:
        b["size_top"] = {"small": "S", "medium": "M", "large": "L"}.get(m.group(1), m.group(1).upper())
    wants_shoes = any(c in SHOE_CATEGORIES for c in b["items_wanted"])
    wants_bottom = any(c in BOTTOM_CATEGORIES for c in b["items_wanted"])
    m = re.search(r"\b(eu|uk|us)\s*(\d{1,2}(?:\.5)?)\b", text)
    if m:
        b["size_shoe"] = f"{m.group(1).upper()} {m.group(2)}"
    elif m := re.search(r"\bshoe size\s*(?:is\s*)?(\d{1,2}(?:\.5)?)\b", text) or (
            wants_shoes and re.search(r"\bsize\s+((?:3[4-9]|4[0-8])(?:\.5)?)\b", text)):
        b["size_shoe"] = m.group(1)  # a bare number; sizes.py reads 30+ as EU

    m = re.search(r"\b(2[4-9]|3[0-8])\s*(?:waist|w\b|inch)", text) or \
        re.search(r"\bwaist\s*(?:size\s*)?(2[4-9]|3[0-8])\b", text) or \
        ((wants_bottom or not wants_shoes) and re.search(r"\bsize\s+(2[4-9]|3[0-8])\b", text))
    if m:
        b["size_bottom"] = m.group(1)

    m = re.search(r"(?:under|below|less than|max|up to|budget(?: of| is)?|within)\s*(?:s?\$|sgd)?\s*(\d+(?:\.\d+)?)", text) \
        or re.search(r"(?:\$|\bsgd)\s*(\d+(?:\.\d+)?)", text) \
        or re.search(r"\b(\d+(?:\.\d+)?)\s*(?:sgd|dollars?|bucks)\b", text)
    if m:
        amount = float(m.group(1))
        if re.search(r"\b(each|per item|apiece|a piece)\b", text):
            b["budget_per_item"] = amount
        else:
            b["budget_total"] = amount

    brief = ShoppingBrief(**{k: v for k, v in b.items() if k != "provenance"})
    return IntakeLLMOutput(
        **{k: v for k, v in brief.model_dump().items() if k != "provenance"},
        inferred_fields=inferred,
        clarifications=missing_clarifications(brief),
        summary=_summarize(brief),
    )


def _summarize(b: ShoppingBrief) -> str:
    parts = [", ".join(b.items_wanted) or "clothes"]
    if b.section:
        parts.append(f"from the {b.section}'s section")
    if b.occasion:
        parts.append(f"for {b.occasion}")
    if b.budget_total:
        parts.append(f"under S${b.budget_total:.0f} total")
    elif b.budget_per_item:
        parts.append(f"under S${b.budget_per_item:.0f} each")
    return "Looking for " + " ".join(parts) + "."


def brief_to_prompt(brief: ShoppingBrief) -> str:
    return json.dumps(brief.model_dump(exclude={"provenance"}), indent=1)

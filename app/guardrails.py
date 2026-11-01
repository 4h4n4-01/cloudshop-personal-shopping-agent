"""Guardrails, the distinction made real.

Instructions GUIDE behaviour (a system prompt can be talked past). A GUARDRAIL automatically
CHECKS an input, output, or tool call and REJECTS it if the check fails, and, crucially, it is
enforced by the APPLICATION in code, not by the model's goodwill. There are three kinds:

    input: at the front door, is this request even in scope for a shopping agent?
    output: before the user acts on a pick, did it pass the Verifier (is it acceptable)?
    tool: before a payment tool runs, known card, positive amount, within a hard ceiling?

Approval (the human gates in secure_checkout) is the third leg; guardrails are the automatic one.
"""
import re
from typing import Optional

# A hard ceiling no single checkout may exceed, regardless of spending cap or override, a tool
# guardrail is a deterministic rule, not a judgement call.
PAYMENT_HARD_MAX = 5000.0

# Requests that clearly belong to a different agent. Paired with a fashion signal so a bare
# "checkout" or "remove the bag" is never mis-rejected.
_OUT_OF_SCOPE = [
    (r"\b(flight|flights|plane ticket|hotel|airbnb|book a (room|table|cab|taxi))\b", "travel booking"),
    (r"\b(pizza|grocer(y|ies)|takeout|take-out|uber eats|order food|deliver food)\b", "food ordering"),
    (r"\b(stocks?|crypto|bitcoin|ethereum|invest|day ?trading)\b", "finance / trading"),
    (r"\b(write|debug|fix) (me )?(some )?code\b|\bpython script\b|\bsql query\b", "software coding"),
    (r"\b(weather|forecast|temperature outside)\b", "weather"),
    (r"\b(medical|diagnos(e|is)|symptom|prescription|legal advice|lawyer)\b", "medical / legal advice"),
]
_FASHION = re.compile(
    r"\b(wear|outfit|dress|dresses|shirt|blouse|top|tee|t-?shirt|jeans|trousers|pants|skirt|shorts|"
    r"jacket|blazer|coat|knit|sweater|hoodie|shoe|shoes|heels|sneakers|boots|sandals|loafers|bag|"
    r"clothes|clothing|style|stylish|size|budget|colour|color|party|wedding|date|office|interview|"
    r"beach|gym|brunch|club|clubby|formal|casual|fit)\b", re.I)
_COMMAND = re.compile(r"\b(checkout|check out|pay|cart|add|remove|confirm|yes|no|that'?s it|different|cheaper)\b", re.I)


def check_in_scope(message: str) -> Optional[str]:
    """INPUT guardrail. Returns a rejection reason if the request is out of scope, else None."""
    text = " " + message.lower() + " "
    if _FASHION.search(text) or _COMMAND.search(text):
        return None  # has a fashion or flow signal, let the concierge handle it
    for pat, label in _OUT_OF_SCOPE:
        if re.search(pat, text):
            return (f"I'm a clothes-shopping assistant, so I can't help with {label}. "
                    "Tell me what you'd like to wear, an occasion, a vibe, or a specific piece.")
    return None


def check_recommendation_addable(verdict: Optional[str]) -> Optional[str]:
    """OUTPUT guardrail. A pick that failed a hard constraint (Verifier verdict 'fail') must not be
    added to the cart, however the shopper refers to it. Returns a reason to block, else None."""
    if verdict == "fail":
        return ("That item failed a hard check (e.g. out of stock in your size, over budget, or an "
                "avoided material), so I won't add it. Want me to find a verified alternative?")
    return None


def unmatched_specifiers(specifiers, cand_texts) -> list:
    """IDENTITY guardrail. `specifiers` are the distinctive words the shopper used to name an item
    (brand / product names, after category / colour / size / style words are stripped out). Returns the
    ones that appear in NONE of the on-screen candidates' "brand name" text, i.e. the shopper referred
    to something that isn't actually among the options, so the app says so instead of adding a different
    brand and inventing a reason."""
    low = [t.lower() for t in cand_texts]
    return [s for s in specifiers if not any(s in t for t in low)]


def check_add_satisfiable(named_color, named_size, candidates) -> Optional[dict]:
    """AVAILABILITY guardrail. When the shopper names a colour and/or size, confirm at least one
    on-screen option can meet them *together*. Returns None if satisfiable, else a dict naming what is
    unmet and what IS available, so the app states 'not available' with the reason instead of adding
    a mismatched item. `candidates` are dicts: {"colors": [...], "sizes": [...in stock...]}."""
    def cols(c):
        return {x.lower() for x in c.get("colors", [])}

    def szs(c):
        return {x.lower() for x in c.get("sizes", [])}

    def ok(c):
        return (not named_color or named_color in cols(c)) and (not named_size or named_size in szs(c))

    if not candidates or any(ok(c) for c in candidates):
        return None
    return {
        "color_bad": bool(named_color) and not any(named_color in cols(c) for c in candidates),
        "size_bad": bool(named_size) and not any(named_size in szs(c) for c in candidates),
        "joint": bool(named_color) and bool(named_size)
        and any(named_color in cols(c) for c in candidates)
        and any(named_size in szs(c) for c in candidates),
        "colors": sorted({x for c in candidates for x in c.get("colors", [])}),
        "sizes": sorted({x for c in candidates for x in c.get("sizes", [])}),
    }


def check_payment(amount: float, method: str, known_methods: set[str]) -> Optional[str]:
    """TOOL guardrail. A deterministic rule on the payment tool's arguments. Returns a reason to
    reject the call, else None (the tool body never runs on a rejection)."""
    if amount is None or amount <= 0:
        return "The amount to charge must be positive."
    if amount > PAYMENT_HARD_MAX:
        return f"S${amount:.2f} is above the S${PAYMENT_HARD_MAX:.0f} hard limit for a single checkout."
    if method not in known_methods:
        return f"Unknown payment method '{method}'."
    return None

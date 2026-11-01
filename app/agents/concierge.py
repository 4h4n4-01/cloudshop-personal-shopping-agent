"""Concierge Agent, the conversational front door of the whole agentic system.

The shopper talks naturally, in ONE thread:
    "party tomorrow, clubby but comfy, dark not black, and I hate sequins"
    "I like the dress but I don't need the shoes, I'll go with the bag and the dress"
    "anything else? maybe a top, I've got a date next Tuesday"
    "ok let's check out"

The concierge reads each message *in context* (the current brief, the items on screen, the cart,
whether checkout has started) and routes it into actions over the worker pipeline, shop, refine,
add/remove from the cart, confirm the cart, or start the secure checkout. This is the
orchestrator-workers pattern (Anthropic, "Building Effective Agents") with an LLM as the router;
mock mode uses a deterministic keyword router so the flow runs offline and in tests.
"""
import re
from typing import Optional

from .. import llm
from ..config import use_llm
from ..models import ConciergeAction, ConciergeTurn
from ..trace import Trace
from .intake import _CAT_WORDS, _OCC_WORDS

AGENT = "Concierge"

SYSTEM = """You are the Concierge of a clothes-shopping assistant: the single conversational
surface the shopper talks to. Read the shopper's latest message together with the context, and
decide the actions to run over the pipeline. Reply in one or two warm, natural sentences.

Action kinds:
- "shop": a new or changed shopping request (a garment, an occasion, or feelings/constraints like
  "clubby", "nothing tight", "dark but not black", "no sequins"). Put the shopper's words in query.
- "refine": they want different options for the same request. Put any steer in query.
- "add": they chose item(s) from what's on screen. Put the item categories in refs, e.g. ["dress","bag"].
- "remove": take item(s) out of the cart. Put categories in refs.
- "confirm_cart": they're happy with the cart / "that's it" / "anything else? no".
- "checkout": they want to pay / check out.
- "smalltalk": greetings or anything else; just reply.

Rules:
- A message can carry more than one action (e.g. add the dress AND the bag while skipping shoes).
- "I don't need the shoes" means do NOT add shoes, not remove them if they were never added.
- Mentioning a NEW occasion ("a date Tuesday") is usually a new "shop", even if items are on screen.
- Never invent a checkout; only act on what the shopper asked for."""

_NEG = r"(?:no|not|n't|dont|don't|do not|skip|without|drop|take out|remove|hate|avoid)"
_PICK = re.compile(r"\b(take|go with|i'?ll have|i'?ll go|add|i like|keep|get the|grab|yes|sure|just the)\b")
_REFINE = re.compile(
    r"\b(?:"
    r"different|other option|another option|something else|anything else to see|"
    r"not these|not right|not correct|not what i (?:asked|wanted|meant|said)|"
    r"these (?:are|aren'?t|do ?n'?t|look)|"
    r"change (?:them|it)|try again|again please|re-?run|redo|do it again|search again|start over|"
    r"wrong|incorrect|"
    r"(?:correct|right|different|new|more|other) (?:ones|option|options|item|items|pick|picks|suggestions)|"
    r"show me (?:more|others|something else|different)"
    r")\b")
_CONFIRM = re.compile(r"\b(that'?s (it|all)|looks good|i'?m done|that'?s fine|confirm the cart|nothing else|no that'?s all|all good)\b")
_CHECKOUT = re.compile(r"\b(check ?out|pay now|let'?s pay|place (the )?order|proceed to pay|ready to pay|buy (it|them)|take my money)\b")
# "new intent" cues: an occasion/event or an explicit fresh-request word
_OCC_WORDS_EXT = set(_OCC_WORDS) | {"party", "club", "clubby", "tonight", "tomorrow", "brunch", "funeral"}


def route(message: str, ctx: dict, trace: Trace) -> ConciergeTurn:
    """ctx: {has_brief, has_recs, rec_categories, cart_categories, in_checkout}."""
    trace.log(AGENT, "human", "Shopper said", message)
    if use_llm():
        user = f"Context: {ctx}\n\nShopper: {message}"
        try:
            turn = llm.parse(SYSTEM, user, ConciergeTurn)
        except Exception as e:
            # Small / local models sometimes emit malformed JSON. Don't break the conversation -
            # fall back to the deterministic router so the pipeline still runs.
            trace.log(AGENT, "error", "LLM routing failed; using deterministic router", str(e))
            turn = _mock_route(message, ctx)
            trace.log(AGENT, "decision", "Concierge routed the message (mock fallback)", turn.model_dump())
            return turn
        turn = _ensure_actionable(turn, message, ctx, trace)
        trace.log(AGENT, "llm", "Concierge routed the message", turn.model_dump())
    else:
        turn = _mock_route(message, ctx)
        trace.log(AGENT, "decision", "Concierge routed the message (mock)", turn.model_dump())
    return turn


def _ensure_actionable(turn: ConciergeTurn, message: str, ctx: dict, trace: Optional[Trace] = None) -> ConciergeTurn:
    """Weak-model safety net. A capable model returns a reply AND the action(s) to run. A small
    local model (e.g. llama3.2) often returns a friendly reply but NO action, so the pipeline never
    advances. When the LLM gives nothing actionable (empty, or "smalltalk" only), borrow the
    deterministic router's action so the workflow still moves, while keeping the LLM's natural reply.
    A turn that already carries a real action is left untouched."""
    if any(a.kind != "smalltalk" for a in turn.actions):
        return turn
    fb = _mock_route(message, ctx)
    if any(a.kind != "smalltalk" for a in fb.actions):
        if trace is not None:
            trace.log(AGENT, "decision", "LLM emitted no action; deterministic router supplied one",
                      {"from_llm_reply": turn.reply, "actions": [a.model_dump() for a in fb.actions]})
        return ConciergeTurn(reply=turn.reply or fb.reply, actions=fb.actions)
    return turn


def _referenced_categories(text: str) -> tuple[list[str], list[str]]:
    """Return (chosen, negated) product categories the message refers to."""
    chosen, negated = [], []
    for word, cat in _CAT_WORDS.items():
        for m in re.finditer(rf"(?<![\w-]){re.escape(word)}\b", text):
            window = text[max(0, m.start() - 18):m.start()]
            (negated if re.search(_NEG + r"[\w\s']{0,12}$", window) else chosen).append(cat)
    # de-duplicate, keep order; a category both chosen and negated counts as negated (don't add it)
    chosen = [c for c in dict.fromkeys(chosen) if c not in negated]
    return chosen, list(dict.fromkeys(negated))


def _has_new_occasion(text: str) -> bool:
    return any(re.search(rf"\b{re.escape(w)}\b", text) for w in _OCC_WORDS_EXT) \
        or bool(re.search(r"\b(another|also need|as well|and a|plus a|next (week|tuesday|monday|friday|weekend))\b", text))


def _mock_route(message: str, ctx: dict) -> ConciergeTurn:
    text = " " + message.lower().replace("-", " ") + " "
    chosen, negated = _referenced_categories(text)

    if _CHECKOUT.search(text):
        return ConciergeTurn(reply="Great, let's check out securely. I'll take you through it step by step.",
                             actions=[ConciergeAction(kind="checkout")])
    if _CONFIRM.search(text) and not chosen:
        return ConciergeTurn(reply="Perfect, your cart's all set whenever you're ready to check out.",
                             actions=[ConciergeAction(kind="confirm_cart")])

    picking = bool(_PICK.search(text)) or (chosen and not negated and len(message.split()) <= 8)
    new_request = _has_new_occasion(text) or (chosen and not ctx.get("has_recs"))

    # Pick from what's on screen: add the chosen items (as long as it isn't really a new request).
    if ctx.get("has_recs") and chosen and picking and not _has_new_occasion(text):
        names = " and ".join(chosen)
        return ConciergeTurn(reply=f"Nice choice, adding the {names} to your cart. Want anything else?",
                             actions=[ConciergeAction(kind="add", refs=chosen)])

    # Remove from cart.
    if negated and ctx.get("cart_categories") and any(c in ctx["cart_categories"] for c in negated):
        drop = [c for c in negated if c in ctx["cart_categories"]]
        return ConciergeTurn(reply=f"Done, took the {' and '.join(drop)} out of your cart.",
                             actions=[ConciergeAction(kind="remove", refs=drop)])

    if _REFINE.search(text) and not new_request:
        return ConciergeTurn(reply="Sure, let me pull some different options.",
                             actions=[ConciergeAction(kind="refine", query=message)])

    # A new or changed shopping request (incl. emotional/contextual phrasing).
    if new_request or chosen or re.search(r"\b(wear|outfit|looking for|need|want|something)\b", text):
        return ConciergeTurn(reply="On it, let me find a few options that fit what you described.",
                             actions=[ConciergeAction(kind="shop", query=message)])

    return ConciergeTurn(reply="Tell me what you're after, an occasion, a vibe, or a specific piece, "
                               "and I'll pull some options.", actions=[ConciergeAction(kind="smalltalk")])

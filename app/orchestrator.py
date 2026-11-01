"""Orchestration layer: the control plane that coordinates the worker agents.

One `Orchestrator` per shopper session. It owns the session state and sequences the narrow worker
agents - Intake → Stylist → Verifier → Checkout - and the memory layer, following the
orchestrator-workers pattern (Anthropic, "Building Effective Agents"). The orchestrator never calls
an LLM itself; each worker does, through the harness. Validation errors raise `ValueError`; the
interface layer (`app/main.py`) maps those to HTTP responses.
"""
import re
import uuid
from typing import Optional

from . import guardrails, memory
from .agents import checkout, concierge, intake, stylist, verifier
from .agents.secure_checkout import SecureCheckout
from .models import (
    BRIEF_FIELDS, COLORS, CartItem, CheckoutPlan, Clarification, ShoppingBrief, VerifiedRecommendation,
    matches_category,
)
from .tools import catalog
from .tools.payments import get_provider
from .trace import Trace


def _cats_in(s: str) -> set:
    """Product categories mentioned in a phrase (via the Intake category lexicon)."""
    s = " " + s.lower() + " "
    return {c for w, c in intake._CAT_WORDS.items() if re.search(rf"(?<![\w-]){re.escape(w)}\b", s)}


def _clause_for(text: str, ref: str) -> str:
    """Return just the part of the message about ONE item, so descriptors for a different item
    ("...trousers in OLIVE...") don't bleed onto this one ("Olive Street shirt"). Splits on strong
    separators, and on "and" only where it joins two item mentions, so a brand like "Quay and Co"
    stays intact. Falls back to the whole message when no clause clearly owns this category."""
    clauses: list[str] = []
    for part in re.split(r"\bas well as\b|\bplus\b|\balso\b|[;,]", text):
        segs = re.split(r"\band\b", part)
        buf = ""
        for i, seg in enumerate(segs):
            buf = (buf + " and " + seg) if buf else seg
            if i + 1 < len(segs) and _cats_in(buf) and _cats_in(segs[i + 1]):
                clauses.append(buf)
                buf = ""
        if buf.strip():
            clauses.append(buf)
    for c in clauses:
        if ref in _cats_in(c):
            return c
    return text


# Words that are NOT a brand/product name when the shopper refers to an item, pronouns, actions,
# fillers, sizes, and generic descriptors. Anything left after removing these (plus categories, colours,
# styles, occasions, materials) is treated as a distinctive specifier (likely a brand/product name).
_GENERIC_REF_WORDS = {
    "the", "a", "an", "and", "or", "as", "well", "also", "please", "one", "ones", "that", "this",
    "these", "those", "get", "add", "take", "want", "wanted", "wanna", "chose", "chosen", "choose",
    "pick", "picked", "like", "liked", "keep", "go", "going", "with", "both", "pair", "set", "them",
    "it", "its", "my", "me", "for", "of", "to", "on", "at", "size", "colour", "color", "option",
    "options", "item", "items", "piece", "pieces", "thing", "things", "something", "anything",
    "another", "other", "more", "too", "yes", "sure", "just", "only", "can", "could", "you", "give",
    "show", "find", "but", "actually", "instead", "rather", "chino", "chinos",
    "professional", "formal", "casual", "smart", "sharp", "polished", "comfy", "comfortable", "loose",
    "tight", "relaxed", "fitted", "slim", "oversize", "oversized", "cropped", "baggy", "fancy",
    "simple", "plain", "nice", "cool", "warm", "light", "dark", "linen", "cotton", "wool", "leather",
    "silk", "denim", "wear", "outfit", "please",
}


class Orchestrator:
    def __init__(self) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.trace = Trace()
        self.provider = get_provider()
        self.brief: Optional[ShoppingBrief] = None
        self.clarifications: list[Clarification] = []
        self.summary = ""
        self.recs: list[VerifiedRecommendation] = []
        self.cart: list[CartItem] = []
        self.plan: Optional[CheckoutPlan] = None
        self.secure: Optional[SecureCheckout] = None   # hardened multi-gate payment flow
        self.orders: list[dict] = []
        self.last_payment_error: Optional[str] = None
        self.customer: Optional[dict] = None   # {id, name} when signed in as a returning customer
        self.profile: Optional[dict] = None    # the remembered preference profile (memory layer)
        self.messages: list[dict] = []         # the running chat transcript [{role, text}]
        self.view_hint: Optional[str] = None   # which page the concierge wants the UI to show next

    def state(self) -> dict:
        return {
            "session_id": self.id,
            "brief": self.brief.model_dump() if self.brief else None,
            "clarifications": [c.model_dump() for c in self.clarifications],
            "summary": self.summary,
            "recommendations": [r.model_dump() for r in self.recs],
            "cart": [c.model_dump() | {"product": catalog.get(c.product_id)} for c in self.cart],
            "plan": self.plan.model_dump() if self.plan else None,
            "secure": self.secure.state() if self.secure else None,
            "orders": self.orders,
            "customer": self.customer,
            "memory": self.profile,
            "messages": self.messages,
            "view_hint": self.view_hint,
            "last_payment_error": self.last_payment_error,
            "trace": self.trace.events,
            "catalog": catalog.status(),
        }

    # ------------------------- memory layer ------------------------------- #
    def set_customer(self, customer_id: Optional[str]) -> dict:
        if not customer_id:
            self.customer = self.profile = None
            self.trace.log("Memory", "human", "Signed out - personalisation off")
            return self.state()
        prof = memory.profile(customer_id)
        if not prof:
            raise ValueError("Unknown customer.")
        self.customer = {"id": prof["id"], "name": prof["name"]}
        self.profile = prof
        self.brief = memory.prefill_brief(prof)
        self.summary = f"Welcome back, {prof['name']}. Your usual preferences are filled in - tell me what you need today."
        self.clarifications, self.recs, self.cart, self.plan = [], [], [], None
        self.trace.log("Memory", "decision", f"Loaded remembered profile for {prof['name']}", prof)
        return self.state()

    # ----------------- Concierge: the conversational front door ----------- #
    def chat(self, message: str) -> dict:
        """One natural-language turn: the Concierge routes it into pipeline actions."""
        self.messages.append({"role": "user", "text": message})
        self.view_hint = None
        reason = guardrails.check_in_scope(message)  # INPUT guardrail, app-enforced
        if reason:
            self.trace.log("Guardrail", "decision", "Input guardrail rejected an out-of-scope request", message)
            self.messages.append({"role": "assistant", "text": reason})
            return self.state()
        # While a secure checkout is open, handle checkout-control requests ourselves so the model
        # can never claim it switched the card or completed payment when it did not. The payment
        # itself is only ever completed by the human clicking through the on-screen gates.
        if self.secure and self.secure.stage not in ("captured", "rejected"):
            handled = self._checkout_chat(message)
            if handled is not None:
                self.view_hint = "checkout"
                self.messages.append({"role": "assistant", "text": handled})
                return self.state()
        ctx = {
            "has_brief": self.brief is not None,
            "has_recs": bool(self.recs),
            "rec_categories": sorted({r.product["category"] for r in self.recs}),
            "cart_categories": sorted({p["category"] for c in self.cart
                                       if (p := catalog.get(c.product_id))}),
            "in_checkout": bool(self.secure and self.secure.stage not in ("captured", "rejected")),
        }
        turn = concierge.route(message, ctx, self.trace)
        reply = turn.reply
        for action in turn.actions:
            reply = self._run_action(action, message) or reply
        self.messages.append({"role": "assistant", "text": reply})
        return self.state()

    def _run_action(self, action, message: str) -> Optional[str]:
        kind = action.kind
        if kind == "shop":
            self.build_brief(action.query or message)
            self.recommend(None)
            self.view_hint = "discover"
            n = len(self.recs)
            if n:
                return (f"Here {'is' if n == 1 else 'are'} {n} option(s) that fit what you described, "
                        "tell me which you'd like to keep.")
            # Explain WHICH constraint caused the empty result (size / budget / section), not a vague
            # "loosen the budget", the shopper shouldn't have to guess (describe = do; be honest).
            return self._no_match_reason(self.brief) or \
                "I couldn't find a match for that yet, want to adjust the request a little?"
        if kind == "refine":
            # A refine often carries NEW constraints ("also nothing too tight, I'm an XL"), so parse
            # the message into the brief first, otherwise those never reach the structured brief.
            self.build_brief(message)
            self.recommend(action.query or "show me different options")
            self.view_hint = "discover"
            return None
        if kind == "add":
            added, problems = self._add_refs(action.refs, message)
            self.view_hint = "checkout"
            parts = []
            if added:
                # State what was ACTUALLY added, never the raw request (describe = do).
                parts.append("Added " + self._join(added) + " to your bag.")
            parts.extend(problems)  # availability guardrail messages (named colour/size unavailable)
            if not parts:
                return "I couldn't spot that in your current options, which one did you mean?"
            if added:
                parts.append("Anything else?")
            return " ".join(parts)
        if kind == "remove":
            self._remove_refs(action.refs)
            self.view_hint = "checkout"
            return None
        if kind == "confirm_cart":
            self.view_hint = "checkout"
            return None
        if kind == "checkout":
            if not self.cart:
                return "Your cart's empty, pick an item first and then we can check out."
            self.begin_secure_checkout(self.brief.budget_total if self.brief else None, "pm_card_visa")
            self.view_hint = "checkout"
            return None
        return None  # smalltalk

    def _add_refs(self, refs: list[str], message: str = "") -> tuple[list[str], list[str]]:
        """Resolve a chat reference ('the dress', 'the Quay & Co shirt', 'the olive trousers in 36')
        to the BEST-matching on-screen recommendation, scored by the brand / name / colour words the
        shopper used, honouring a named colourway and size when the product offers them. Returns
        (added, problems): `added` describes each item ACTUALLY carted (so the reply states the truth,
        not the request); `problems` is the availability guardrail firing when a named colour/size
        can't be met, so the agent says what's unavailable rather than adding a mismatched item."""
        full = " " + (message or "").lower() + " "
        added: list[str] = []
        problems: list[str] = []
        for ref in refs:
            clause = _clause_for(full, ref)  # only the words about THIS item, not the other one
            cands = [r for r in self.recs
                     if matches_category(r.product["category"], [ref])
                     and not any(c.product_id == r.product["id"] for c in self.cart)]
            if not cands:
                continue
            # IDENTITY guardrail: did the shopper name a brand/product that isn't among the
            # options? Don't add a different brand and invent a reason, say it isn't there.
            missing = guardrails.unmatched_specifiers(
                self._specifiers(clause),
                [f"{r.product.get('brand', '')} {r.product['name']}" for r in cands])
            if missing:
                names = [f"{r.product['name']} ({r.product.get('brand') or r.product.get('store', '')})".strip()
                         for r in cands][:4]
                self.trace.log("Guardrail", "decision",
                               "Identity guardrail: a named item isn't among the options", {"unmatched": missing})
                problems.append(f"I couldn’t find “{self._join(missing)}” among the {ref} options on screen "
                                f"- they’re {self._join(names)}. Which did you mean, or shall I search for that?")
                continue
            ncolor, nsize = self._named_color(clause), self._named_size(clause)

            def colors_of(r):
                return {c.lower() for c in r.product.get("colors", [])}

            def sizes_of(r):
                return {s.lower() for s, q in r.product["stock"].items() if q}

            viable = [r for r in cands
                      if (not ncolor or ncolor in colors_of(r)) and (not nsize or nsize in sizes_of(r))]
            if not viable:  # AVAILABILITY guardrail: a named colour/size can't be met
                infos = [{"colors": r.product.get("colors", []),
                          "sizes": [s for s, q in r.product["stock"].items() if q]} for r in cands]
                v = guardrails.check_add_satisfiable(ncolor, nsize, infos) or {}
                self.trace.log("Guardrail", "decision",
                               "Availability guardrail: a named parameter can't be met", v)
                bits = []
                if v.get("color_bad"):
                    av = f" (these come in {self._join(sorted(v['colors'])[:4])})" if v.get("colors") else ""
                    bits.append(f"in {ncolor}{av}")
                if v.get("size_bad"):
                    av = f" (available sizes {self._join(sorted(v['sizes'])[:6])})" if v.get("sizes") else ""
                    disp = nsize.upper() if nsize and len(nsize) <= 3 else nsize
                    bits.append(f"in size {disp}{av}")
                if v.get("joint"):
                    bits.append(f"in {ncolor} and size {nsize} together")
                problems.append(f"I couldn’t add the {ref} {' and '.join(bits)}, "
                                "want a different colour or size, or another piece?")
                continue
            best = max(viable, key=lambda r: self._ref_score(r, clause))
            p = best.product
            color = ncolor or next((c for c in p.get("colors", []) if re.search(rf"\b{re.escape(c)}\b", clause)), None)
            size = self._pick_size(best, clause)
            if not size:
                continue
            self.add_to_cart(CartItem(product_id=p["id"], size=size, color=color))
            bits = ([color] if color else []) + [f"size {size}"]
            added.append(f"{p['name']} ({', '.join(bits)})")
        return added, problems

    @staticmethod
    def _specifiers(clause: str) -> list:
        """Distinctive words the shopper used to NAME an item, a brand/product reference. Only words in
        a "the X" or "X one(s)" position are candidates (so ordinary verbs/fillers in a sentence aren't
        mistaken for brands), minus category / colour / style / occasion / material / filler words."""
        stop = (_GENERIC_REF_WORDS | set(intake._CAT_WORDS) | set(COLORS) | set(intake.STYLES)
                | set(intake._ADJ_STYLE) | set(intake._OCC_WORDS))
        found = re.findall(r"\bthe\s+([a-z]{3,})", clause) + re.findall(r"\b([a-z]{3,})\s+ones?\b", clause)
        out = []
        for t in found:
            if t not in stop and t not in out:
                out.append(t)
        return out

    @staticmethod
    def _named_color(clause: str):
        return next((c for c in COLORS if re.search(rf"\b{re.escape(c)}\b", clause)), None)

    @staticmethod
    def _named_size(clause: str):
        m = (re.search(r"\bsize\s+([a-z0-9]{1,4})\b", clause) or re.search(r"\b(xxs|xs|xl|xxl)\b", clause)
             or re.search(r"\b(2[4-9]|3[0-9]|4[0-8])\b", clause))
        return m.group(1).lower() if m else None

    @staticmethod
    def _ref_score(r, text: str) -> int:
        """How strongly the shopper's words single out this recommendation (brand/name/colour hits)."""
        p = r.product
        score = 0
        for tok in re.findall(r"[a-z]{3,}", (p.get("brand") or p.get("store") or "").lower()):
            if re.search(rf"\b{re.escape(tok)}\b", text):
                score += 2
        for tok in re.findall(r"[a-z]{4,}", p["name"].lower()):
            if re.search(rf"\b{re.escape(tok)}\b", text):
                score += 1
        for c in p.get("colors", []):
            if re.search(rf"\b{re.escape(c)}\b", text):
                score += 2
        return score

    @staticmethod
    def _pick_size(r, text: str) -> str:
        """Honour a size the shopper named if the product stocks it; else the recommended / first
        in-stock size."""
        in_stock = [s for s, q in r.product["stock"].items() if q]
        low = {s.lower(): s for s in in_stock}
        m = re.search(r"\bsize\s+([a-z0-9]{1,4})\b", text)
        if m and m.group(1) in low:
            return low[m.group(1)]
        for s in in_stock:                       # a bare, standalone size token ("36", "M")
            if re.search(rf"\b{re.escape(s.lower())}\b", text):
                return s
        return r.size or (in_stock[0] if in_stock else "")

    @staticmethod
    def _join(items: list[str]) -> str:
        if len(items) <= 1:
            return items[0] if items else ""
        return ", ".join(items[:-1]) + " and " + items[-1]

    def _no_match_reason(self, brief) -> Optional[str]:
        """Diagnose an empty shortlist: name the binding constraint (size not stocked, budget too low,
        or nothing in that item/section) and say what IS available, instead of a vague 'loosen budget'."""
        cats = brief.items_wanted or None
        maxp = brief.budget_per_item or brief.budget_total
        item = self._join(brief.items_wanted) or "anything"
        sect = f" in the {brief.section}'s range" if brief.section else ""
        want = brief.size_bottom or brief.size_top or brief.size_shoe

        # What the hard filters return WITHOUT the size filter, if non-empty, size is the blocker.
        base = catalog.search_catalog(categories=cats, section=brief.section, max_price=maxp,
                                      exclude_materials=brief.materials_avoided, limit=80)
        if base:
            if want:
                sizes = sorted({s for r in base for s in r.get("sizes_in_stock", [])})
                if want not in sizes:
                    self.trace.log("Stylist", "decision", "No match: size not stocked",
                                   {"wanted": want, "available": sizes})
                    extra = f", available sizes are {self._join(sizes[:8])}" if sizes else ""
                    return f"I don't have {item}{sect} in size {want}{extra}. Want one of those sizes?"
            return None  # a rarer blocker (e.g. all colourways avoided), fall back to the generic line
        if maxp is not None:  # budget may be the blocker
            unbudgeted = catalog.search_catalog(categories=cats, section=brief.section,
                                                exclude_materials=brief.materials_avoided, limit=5)
            if unbudgeted:
                low = min(r["price"] for r in unbudgeted)
                return (f"I don't have {item}{sect} under S${maxp:.0f}, the lowest is S${low:.0f}. "
                        "Want to raise the budget?")
        return f"I don't have {item}{sect} at the moment. Want to try a different item or section?"

    def _remove_refs(self, refs: list[str]) -> None:
        for ref in refs:
            for i, c in enumerate(self.cart):
                p = catalog.get(c.product_id)
                if p and matches_category(p["category"], [ref]):
                    self.remove_from_cart(i)
                    break

    def _checkout_chat(self, message: str) -> Optional[str]:
        """Handle checkout-control requests during a secure checkout deterministically, so the model
        can't claim it switched the card or completed payment when it did not. Returns a reply, or
        None to let the normal chat handle the message."""
        t = " " + message.lower() + " "
        method = self._method_from_text(t)
        if method:
            try:
                self.secure.set_payment_method(self.cart, method)
            except Exception as e:
                return str(e)
            return (f"Switched the card to {self.provider.label(method)}. I can't complete the payment "
                    "from chat; you approve each step on the right: Confirm cart, Confirm card, 2FA, then Pay.")
        if re.search(r"\b(proceed|pay|complete|finish|place (the )?order|go ahead|charge( it)?)\b", t):
            return ("Payment only goes through when you approve each step here, so I can't complete it from "
                    "chat. Step through Confirm cart, Confirm card, 2FA, and Pay on the right.")
        if re.search(r"\b(cancel|abort|never ?mind|don'?t (buy|pay|check ?out))\b", t):
            self.sc_reject()
            return "Cancelled the checkout. Nothing was charged."
        return None

    def _method_from_text(self, text: str) -> Optional[str]:
        """Map a card the shopper names to a known payment-method id, or None."""
        ids = {m["id"] for m in self.provider.list_methods()}
        if re.search(r"\bmaster ?card\b|\bmc\b", text) and "pm_card_mc" in ids:
            return "pm_card_mc"
        if re.search(r"\bvisa\b", text) and "pm_card_visa" in ids:
            return "pm_card_visa"
        if "declined" in text and "pm_card_declined" in ids:
            return "pm_card_declined"
        if "insufficient" in text and "pm_card_insufficient" in ids:
            return "pm_card_insufficient"
        return None

    # ------------------------- Agent 1: Intake ---------------------------- #
    def build_brief(self, message: str) -> dict:
        self.brief, self.clarifications, self.summary = intake.run(message, self.brief, self.trace)
        return self.state()

    def clarify(self, field: str, value: str) -> dict:
        if not self.brief or field not in BRIEF_FIELDS:
            raise ValueError("Nothing to clarify.")
        self.brief = intake.apply_answer(self.brief, field, value, self.trace)
        self.clarifications = [c for c in self.clarifications if c.field != field]
        return self.state()

    def edit_brief(self, new: dict) -> dict:
        """Direct manipulation: the shopper edits brief fields in the form."""
        old = self.brief.model_dump() if self.brief else {}
        prov = dict(self.brief.provenance) if self.brief else {}
        changed = [f for f in BRIEF_FIELDS if new.get(f) != old.get(f)]
        for f in changed:
            if new.get(f) in (None, [], ""):
                prov.pop(f, None)
            else:
                prov[f] = "edited"
        self.brief = ShoppingBrief(**(new | {"provenance": prov}))
        self.clarifications = [c for c in self.clarifications if c.field not in changed]
        if changed:
            self.trace.log("Intake", "human", "Shopper edited brief", {f: new.get(f) for f in changed})
        return self.state()

    # -------------------- Agents 2+3: Stylist → Verifier ------------------ #
    def recommend(self, feedback: Optional[str]) -> dict:
        if not self.brief:
            raise ValueError("Build a brief first.")
        previous = tuple(r.product["id"] for r in self.recs)
        picks = stylist.run(self.brief, feedback, self.trace, previous, self.profile)
        self.recs = verifier.run(picks, self.brief, self.trace)
        return self.state()

    # ------------------------------ cart ---------------------------------- #
    def add_to_cart(self, item: CartItem) -> dict:
        p = catalog.get(item.product_id)
        if not p or item.size not in p["stock"]:
            raise ValueError("Unknown product or size.")
        verdict = next((r.verdict for r in self.recs if r.product["id"] == item.product_id), None)
        block = guardrails.check_recommendation_addable(verdict)  # OUTPUT guardrail
        if block:
            self.trace.log("Guardrail", "decision", "Output guardrail blocked a failed pick", item.product_id)
            raise ValueError(block)
        for c in self.cart:
            if c.product_id == item.product_id and c.size == item.size and c.color == item.color:
                c.qty += item.qty
                break
        else:
            self.cart.append(item)
        if self.customer:  # learn from the choice and refresh the remembered profile
            memory.remember(self.customer["id"], item.product_id, "added")
            self.profile = memory.profile(self.customer["id"])
        self._invalidate_plan(f"Added {p['name']} ({item.color + ', ' if item.color else ''}{item.size})")
        return self.state()

    def remove_from_cart(self, index: int) -> dict:
        if not 0 <= index < len(self.cart):
            raise ValueError("No such cart line.")
        removed = self.cart.pop(index)
        self._invalidate_plan(f"Removed {removed.product_id} ({removed.size})")
        return self.state()

    def _invalidate_plan(self, why: str) -> None:
        self.trace.log("Checkout", "human", why)
        if self.plan and self.plan.status == "awaiting_approval":
            self.plan.status = "stale"
            self.trace.log("Checkout", "decision", "Pending plan marked stale because the cart changed")

    # --------------------------- Agent 4: Checkout ------------------------ #
    def plan_checkout(self, spending_cap: Optional[float], payment_method: str) -> dict:
        verified = {r.product["id"]: r for r in self.recs}
        self.plan = checkout.build_plan(self.cart, verified, self.brief or ShoppingBrief(),
                                        spending_cap, payment_method, self.provider, self.trace)
        self.last_payment_error = None
        return self.state()

    def approve(self, plan_id: str, cap_override: bool) -> dict:
        if not self.plan or self.plan.plan_id != plan_id:
            raise ValueError("That plan is no longer current.")
        result = checkout.execute(self.plan, self.cart, cap_override, self.provider, self.trace)
        if result["ok"]:
            self.orders.append(result["order"])
            self.cart = []
            self.last_payment_error = None
        else:
            self.last_payment_error = result["reason"]
        return self.state()

    def reject(self, plan_id: str) -> dict:
        if self.plan and self.plan.plan_id == plan_id:
            self.plan.status = "rejected"
            self.trace.log("Checkout", "human", "Shopper rejected the plan - nothing charged")
        return self.state()

    # ------------- Agent 4 (hardened): multi-gate secure checkout ------------ #
    # Visa/Mastercard-rails flow with per-gate human confirmation + 3-D Secure OTP/2FA.
    def begin_secure_checkout(self, spending_cap: Optional[float], payment_method: str) -> dict:
        if not self.cart:
            raise ValueError("Cart is empty.")
        amount = round(sum(c.qty * catalog.get(c.product_id)["price"] for c in self.cart), 2)
        known = {m["id"] for m in self.provider.list_methods()}
        reject = guardrails.check_payment(amount, payment_method, known)  # TOOL guardrail
        if reject:
            self.trace.log("Guardrail", "decision", "Tool guardrail rejected the payment call", reject)
            raise ValueError(reject)
        verified = {r.product["id"]: r for r in self.recs}
        self.secure = SecureCheckout(self.cart, verified, self.brief, spending_cap,
                                     payment_method, self.provider, self.trace)
        self.last_payment_error = None
        return self.state()

    def _sc(self) -> SecureCheckout:
        if not self.secure:
            raise ValueError("No checkout in progress, start one first.")
        return self.secure

    def sc_confirm_cart(self) -> dict:
        self._sc().confirm_cart(self.cart)
        return self.state()

    def sc_confirm_card(self, payment_method: str) -> dict:
        self._sc().confirm_payment_method(self.cart, payment_method)
        return self.state()

    def sc_confirm_review(self, cap_override: bool) -> dict:
        self._sc().confirm_review(self.cart, cap_override)
        return self.state()

    def sc_submit_otp(self, code: str) -> dict:
        self._sc().submit_otp(code)
        return self.state()

    def sc_finalize(self) -> dict:
        result = self._sc().finalize(self.cart)
        if result["ok"]:
            self.orders.append(result["order"])
            self.cart = []
            self.last_payment_error = None
        else:
            self.last_payment_error = result["reason"]
        return self.state()

    def sc_reject(self) -> dict:
        self._sc().reject()
        return self.state()

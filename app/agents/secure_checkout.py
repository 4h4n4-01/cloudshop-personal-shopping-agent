"""Secure checkout, IDEA 3 taken to its strongest form: a hardened, multi-gate, human-in-the-loop
payment flow over the CardNetwork gateway (Visa/Mastercard rails).

This is the part of the agentic system where we DO NOT let the AI act on its own. It is a state
machine with explicit human gates; the agent may *prepare* each step, but every consequential
transition requires a fresh human confirmation. Each gate maps to a principle of relying on the AI
only as far as is safe:

    Stage              Human gate (what the person must do)         principle
    ----------------   ------------------------------------------   ------------------------------
 1  cart_review        confirm the cart (items, sizes, totals)      approval before action
 2  payment_method     confirm which card                           verification effort
 3  combined_review    re-confirm cart + card + amount TOGETHER      cognitive forcing
 4  otp (3-D Secure)   enter the OTP sent to the phone (2FA)         deferral / allocate
 5  authorized         final "Pay now" approver                     approval before action
    captured           money settled; order placed; no replay       idempotency

Guardrails that hold across the machine:
  * Gate order is enforced; stages cannot be skipped.
  * Every gate binds a HASH of what was shown. If the cart or card changes afterwards, the
    downstream approvals are void and the flow resets, no bait-and-switch.
  * A spending cap is a standing mandate; exceeding it needs an explicit override.
  * The OTP (second factor) is structurally HUMAN: the agent prepares the order but cannot produce
    the code, so it can never self-authorize a payment.
  * Capture is idempotent and single-use: an approval cannot be replayed into a double charge.
  * Every gate event is logged (observability / honest audit trail).
"""
import time
import uuid
from typing import Optional

from . import checkout
from ..models import CartItem, ShoppingBrief, VerifiedRecommendation
from ..tools import catalog
from ..trace import Trace

AGENT = "SecureCheckout"

# The question the shopper answers at each gate, surfaced so the UI can show one per gate.
RELIANCE_QUESTIONS = {
    "cart_review": "Do you accept these items? (accept correct help / reject errors)",
    "payment_method": "Is this the right card? (what will the charge hit?)",
    "combined_review": "Does the whole order, items, card and amount, look right together?",
    "otp": "Prove it's you: enter the one-time code (the agent cannot do this step)",
    "authorized": "Final approval: shall we capture the payment now?",
}


class SecureCheckout:
    """One hardened checkout attempt, owned by an Orchestrator session."""

    def __init__(self, cart, verified, brief, spending_cap, payment_method, provider, trace: Trace):
        self.provider = provider
        self.trace = trace
        self.id = "chk_" + uuid.uuid4().hex[:10]
        # Reuse the existing planner for lines/totals/warnings/cap detection.
        self.plan = checkout.build_plan(cart, verified, brief or ShoppingBrief(),
                                        spending_cap, payment_method, provider, trace)
        self.stage = "cart_review"
        self.confirmations: dict[str, str] = {}        # gate -> evidence bound at confirm time
        self.auth: Optional[dict] = None               # the CardNetwork authorization record (public view)
        self.order_draft: Optional[dict] = None        # details the AGENT prepares for the human to check
        self.order: Optional[dict] = None
        self.last_error: Optional[str] = None
        self.history: list[dict] = []
        self._log("created", f"plan {self.id} for S${self.plan.total:.2f}")

    # ------------------------------------------------------------------ util
    def _log(self, action: str, note: str) -> None:
        self.history.append({"stage": self.stage, "action": action, "note": note, "at": time.time()})
        self.trace.log(AGENT, "human" if action.startswith("confirm") or action == "otp" else "decision",
                       f"[{self.stage}] {action}", note)

    def _cart_sig(self, cart: list[CartItem]) -> str:
        return checkout._hash(checkout._lines(cart), self.plan.total, self.plan.payment_method)

    def _require(self, stage: str) -> None:
        if self.stage != stage:
            raise checkout.CheckoutError(
                f"Out of order: this step is for the '{stage}' stage, but checkout is at '{self.stage}'.")

    # ------------------------------------------------------------- gate 1: cart
    def confirm_cart(self, cart: list[CartItem]) -> None:
        self._require("cart_review")
        self.confirmations["cart"] = self._cart_sig(cart)
        self.stage = "payment_method"
        self._log("confirm_cart", f"{sum(l.qty for l in self.plan.lines)} item(s) confirmed")

    # -------------------------------------------------- gate 2: payment method
    def confirm_payment_method(self, cart: list[CartItem], payment_method: str) -> None:
        self._require("payment_method")
        if self.confirmations.get("cart") != self._cart_sig(cart):
            return self._reset("Cart changed since you confirmed it, please review it again.")
        if payment_method != self.plan.payment_method:
            # Re-planning the card changes the hash; rebuild the plan to stay bound to the truth.
            self.plan = checkout.build_plan(cart, {}, ShoppingBrief(), self.plan.spending_cap,
                                            payment_method, self.provider, self.trace)
            self.confirmations["cart"] = self._cart_sig(cart)
        self.confirmations["card"] = payment_method
        self.stage = "combined_review"
        self._log("confirm_card", self.provider.label(payment_method))

    def set_payment_method(self, cart: list[CartItem], method: str) -> None:
        """Switch the card before authorization. Re-plans so totals and the hash stay bound to the
        truth, and voids any card/review confirmation so the human must re-confirm the new card."""
        if self.stage in ("otp", "authorized", "captured"):
            raise checkout.CheckoutError("The payment is already being authorized. Cancel first to change the card.")
        self.plan = checkout.build_plan(cart, {}, ShoppingBrief(), self.plan.spending_cap,
                                        method, self.provider, self.trace)
        self.confirmations.pop("card", None)
        self.confirmations.pop("review", None)
        if "cart" in self.confirmations:
            self.confirmations["cart"] = self._cart_sig(cart)
        if self.stage == "combined_review":
            self.stage = "payment_method"
        self._log("set_card", self.provider.label(method))

    # ------------------------------------------- gate 3: combined re-confirm + authorize
    def confirm_review(self, cart: list[CartItem], cap_override: bool = False) -> None:
        """Cognitive-forcing re-confirmation of cart + card + amount, then ask the issuer to
        place an authorization hold (which may step up to 3-D Secure)."""
        self._require("combined_review")
        if self.confirmations.get("cart") != self._cart_sig(cart):
            return self._reset("Cart changed since you confirmed it, please review it again.")
        if self.plan.requires_cap_override and not cap_override:
            self.last_error = "over_cap"
            raise checkout.CheckoutError("This order exceeds your spending cap. Confirm the override to continue.")
        self.confirmations["review"] = self._cart_sig(cart) + "|" + self.plan.payment_method
        self._log("confirm_review", "cart + card + amount re-confirmed together")
        self._authorize()

    def _authorize(self) -> None:
        auth = self.provider.authorize(int(round(self.plan.total * 100)), self.plan.currency,
                                       self.plan.payment_method, {"checkout_id": self.id})
        self.auth = auth
        if auth["status"] == "declined":
            self.stage = "rejected"
            self.last_error = auth.get("decline_reason", "declined")
            self._log("authorize_declined", self.last_error)
            return
        if auth["status"] == "requires_3ds":
            # The agent now PREPARES the order, but the human must still pass 2FA and approve.
            self.order_draft = self._draft_order()
            self.stage = "otp"
            self._log("authorize_hold", f"hold placed; 3-D Secure sent to {auth.get('otp_sent_to')}")
        else:  # authorized without step-up (small amount / non-partnered)
            self.order_draft = self._draft_order()
            self.stage = "authorized"
            self._log("authorize_hold", "hold placed; no step-up required")

    # -------------------------------------------------- gate 4: OTP / 2FA (human only)
    def submit_otp(self, code: str) -> None:
        self._require("otp")
        res = self.provider.verify_3ds(self.auth["auth_id"], code)
        self.auth = res
        if res["status"] == "authorized":
            self.stage = "authorized"
            self.last_error = None
            self._log("otp", "2FA passed, awaiting final approval")
        elif res["status"] == "voided":
            self.stage = "rejected"
            self.last_error = res.get("decline_reason", "otp_failed")
            self._log("otp_failed", f"authorization voided: {self.last_error}")
        else:
            self.last_error = res.get("decline_reason", "otp_mismatch")
            self._log("otp_retry", f"{res.get('otp_attempts_left')} attempt(s) left")
            raise checkout.CheckoutError(
                f"Incorrect code, {res.get('otp_attempts_left')} attempt(s) left.")

    # --------------------------------------------- gate 5: final approver → capture
    def finalize(self, cart: list[CartItem]) -> dict:
        self._require("authorized")
        if self.confirmations.get("cart") != self._cart_sig(cart):
            return self._reset("Cart changed after authorization, nothing was charged; review again.")
        # Reserve stock, then capture (the only money-moving step). Idempotency key = checkout id.
        reserved = []
        for l in self.plan.lines:
            if not catalog.reserve(l.product_id, l.size, l.qty):
                for r in reserved:
                    catalog.release(*r)
                self.provider.void(self.auth["auth_id"])
                self.stage = "rejected"
                self.last_error = "out_of_stock"
                raise checkout.CheckoutError(f"{l.name} size {l.size} just sold out. Nothing was charged.")
            reserved.append((l.product_id, l.size, l.qty))

        cap = self.provider.capture(self.auth["auth_id"], idempotency_key=self.id)
        if cap["status"] != "captured":
            for r in reserved:
                catalog.release(*r)
            self.stage = "rejected"
            self.last_error = cap.get("decline_reason", "capture_declined")
            self._log("capture_declined", self.last_error)
            return {"ok": False, "reason": self.last_error}
        self.stage = "captured"
        self.order = {"order_id": "ord_" + uuid.uuid4().hex[:10], "capture_id": cap["capture_id"],
                      "auth_id": self.auth["auth_id"], "total": self.plan.total,
                      "currency": self.plan.currency, "lines": [l.model_dump() for l in self.plan.lines]}
        self._log("captured", f"order {self.order['order_id']} placed")
        return {"ok": True, "order": self.order}

    def reject(self) -> None:
        if self.stage == "captured":
            raise checkout.CheckoutError("This order is already paid; it cannot be cancelled here.")
        if self.auth and self.auth.get("auth_id"):
            self.provider.void(self.auth["auth_id"])
        self.stage = "rejected"
        self._log("reject", "shopper cancelled, any hold released, nothing captured")

    # ------------------------------------------------------------------ helpers
    def _reset(self, why: str):
        self.stage = "cart_review"
        self.confirmations.clear()
        if self.auth and self.auth.get("auth_id"):
            self.provider.void(self.auth["auth_id"])
        self.auth = None
        self.order_draft = None
        self.last_error = "cart_changed"
        self._log("reset", why)
        raise checkout.CheckoutError(why)

    def _draft_order(self) -> dict:
        """Details the AGENT fills in for the human to check, it prepares, the human disposes."""
        return {
            "prepared_by": "agent",
            "items": [f"{l.qty}× {l.name} ({l.size})" for l in self.plan.lines],
            "ship_to": "Your saved address",  # a real build would read the account profile
            "amount": f"S${self.plan.total:.2f} {self.plan.currency}",
            "note": "The agent prepared this order. You must pass 2FA and give final approval.",
        }

    # ------------------------------------------------------------------ view
    def state(self) -> dict:
        gates = ["cart_review", "payment_method", "combined_review", "otp", "authorized", "captured"]
        done = gates.index(self.stage) if self.stage in gates else len(gates)
        a = self.auth or {}
        return {
            "checkout_id": self.id,
            "stage": self.stage,
            "step_index": done,
            "step_count": len(gates) - 1,  # 'captured' is the terminal, not a gate to pass
            "question": RELIANCE_QUESTIONS.get(self.stage),
            "plan": self.plan.model_dump(),
            "confirmed": sorted(self.confirmations),
            "requires_cap_override": self.plan.requires_cap_override,
            "twofa": {"method": a.get("twofa_method"), "sent_to": a.get("otp_sent_to"),
                      "attempts_left": a.get("otp_attempts_left"),
                      "demo_otp": a.get("demo_otp")} if self.stage == "otp" else None,
            "order_draft": self.order_draft,
            "order": self.order,
            "last_error": self.last_error,
            "history": self.history,
        }

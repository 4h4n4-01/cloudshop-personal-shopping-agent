"""Checkout Agent - IDEA 3: human oversight and intervention.

Design choices:
  * The agent *proposes* a checkout plan (lines, totals, every step it will take, warnings)
    and stops. Nothing happens until the shopper approves that exact plan.
  * Approval is bound to a hash of the plan. If the cart, prices or payment method change
    afterwards, the approval is void and a fresh plan must be approved (no bait-and-switch).
  * A shopper-set spending cap acts as a standing mandate; exceeding it needs an explicit
    second confirmation.
  * The payment step is never exposed to an LLM - only this deterministic code path runs it,
    and only after approval.

Payment is handled by MockPay (a simulated provider shaped like Stripe): stock is reserved and a
simulated charge is taken. A real deployment would swap MockPay for a real `PaymentProvider`.
"""
import hashlib
import json
import uuid
from typing import Optional

from ..config import CURRENCY, FREE_SHIPPING_OVER, SHIPPING_FLAT, TAX_RATE
from ..models import CartItem, CheckoutPlan, PlanLine, ShoppingBrief, VerifiedRecommendation
from ..tools import catalog
from ..tools.payments.base import PaymentProvider
from ..trace import Trace

AGENT = "Checkout"


class CheckoutError(RuntimeError):
    pass


def _lines(cart: list[CartItem]) -> list[PlanLine]:
    lines = []
    for item in cart:
        p = catalog.get(item.product_id)
        if p is None:
            raise CheckoutError(f"{item.product_id} is no longer in the catalog. Remove it from the cart.")
        lines.append(PlanLine(
            product_id=p["id"], name=p["name"], size=item.size, qty=item.qty, unit_price=p["price"],
            line_total=round(p["price"] * item.qty, 2), color=item.color, store=p.get("store"),
        ))
    return lines


def build_plan(
    cart: list[CartItem],
    verified: dict[str, VerifiedRecommendation],
    brief: ShoppingBrief,
    spending_cap: Optional[float],
    payment_method: str,
    provider: PaymentProvider,
    trace: Trace,
) -> CheckoutPlan:
    if not cart:
        raise CheckoutError("Cart is empty.")
    lines = _lines(cart)

    warnings = []
    for line in lines:
        p = catalog.get(line.product_id)
        v = verified.get(line.product_id)
        line.verdict = v.verdict if v else None
        if p["stock"].get(line.size, 0) < line.qty:
            warnings.append(f"{p['name']} size {line.size}: not in stock")
        if v and v.verdict != "pass":
            issues = [f"{c.name} ({c.evidence})" for c in v.checks if c.status != "pass"]
            warnings.append(f"{p['name']} has verifier {v.verdict}s: " + "; ".join(issues))
        if v is None:
            warnings.append(f"{p['name']} was not in the latest recommendations, so it has not been verified")

    subtotal = round(sum(l.line_total for l in lines), 2)
    shipping = 0.0 if subtotal >= FREE_SHIPPING_OVER else SHIPPING_FLAT
    tax = round(subtotal * TAX_RATE, 2)
    total = round(subtotal + shipping + tax, 2)
    if brief.budget_total is not None and total > brief.budget_total:
        warnings.append(f"Total S${total:.2f} is over your brief's budget of S${brief.budget_total:.2f}")
    over_cap = spending_cap is not None and total > spending_cap
    if over_cap:
        warnings.append(f"Total S${total:.2f} exceeds your spending cap of S${spending_cap:.2f}")

    method_label = provider.label(payment_method) if hasattr(provider, "label") else payment_method
    steps = [
        f"Reserve stock for {sum(l.qty for l in lines)} item(s)",
        f"Create a {provider.name} payment intent for S${total:.2f} ({CURRENCY})",
        f"Charge {method_label}",
        "Place the order and show a receipt",
        "If the charge fails: release the reserved stock and charge nothing",
    ]
    plan = CheckoutPlan(
        plan_id="plan_" + uuid.uuid4().hex[:10], plan_hash=_hash(lines, total, payment_method),
        lines=lines, subtotal=subtotal, shipping=shipping, tax=tax, total=total, currency=CURRENCY,
        payment_method=payment_method, spending_cap=spending_cap, steps=steps, warnings=warnings,
        requires_cap_override=over_cap,
    )
    trace.log(AGENT, "decision", f"Proposed plan S${total:.2f}, awaiting shopper approval", plan.model_dump())
    return plan


def execute(
    plan: CheckoutPlan,
    current_cart: list[CartItem],
    cap_override: bool,
    provider: PaymentProvider,
    trace: Trace,
) -> dict:
    """Runs only after the shopper clicks Approve. Re-validates everything first."""
    if plan.status != "awaiting_approval":
        raise CheckoutError(f"Plan is {plan.status}; create a new plan.")
    if _hash(_lines(current_cart), plan.total, plan.payment_method) != plan.plan_hash:
        plan.status = "stale"
        trace.log(AGENT, "decision", "Approval rejected: cart changed after the plan was shown")
        raise CheckoutError("Your cart changed after this plan was created. Please review the new plan.")
    if plan.requires_cap_override and not cap_override:
        raise CheckoutError("This order exceeds your spending cap. Confirm the override to continue.")

    trace.log(AGENT, "human", "Shopper approved plan", {"plan_id": plan.plan_id, "cap_override": cap_override})
    plan.status = "approved"
    return _mockpay(plan, provider, trace)


def _mockpay(plan: CheckoutPlan, provider: PaymentProvider, trace: Trace) -> dict:
    reserved = []
    for l in plan.lines:
        if not catalog.reserve(l.product_id, l.size, l.qty):
            for r in reserved:
                catalog.release(*r)
            plan.status = "failed"
            raise CheckoutError(f"{l.name} size {l.size} just went out of stock. Nothing was charged.")
        reserved.append((l.product_id, l.size, l.qty))
    trace.log(AGENT, "tool", "reserve_stock", reserved)

    intent = provider.create_intent(int(round(plan.total * 100)), plan.currency, plan.payment_method,
                                    {"plan_id": plan.plan_id})
    trace.log(AGENT, "payment", "create_intent", intent)
    result = provider.confirm(intent["id"])
    trace.log(AGENT, "payment", "confirm", result)

    if result["status"] != "succeeded":
        for r in reserved:
            catalog.release(*r)
        plan.status = "failed"
        trace.log(AGENT, "decision", "Payment failed: stock released, nothing charged", result)
        return {"ok": False, "reason": result.get("failure_reason"), "intent": result}

    plan.status = "paid"
    order = {"order_id": "ord_" + uuid.uuid4().hex[:10], "payment_intent": result["id"],
             "total": plan.total, "currency": plan.currency, "lines": [l.model_dump() for l in plan.lines]}
    trace.log(AGENT, "decision", f"Order placed {order['order_id']}", order)
    return {"ok": True, "order": order}


def _hash(lines: list[PlanLine], total: float, payment_method: Optional[str]) -> str:
    key = [(l.product_id, l.size, l.qty, l.unit_price) for l in lines]
    return hashlib.sha256(json.dumps([key, total, payment_method]).encode()).hexdigest()[:16]

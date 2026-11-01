"""CardNetwork: a mock card-rails gateway shaped like the real Visa / Mastercard authorization
flow, for the hardened, human-in-the-loop checkout (IDEA 3).

No real money ever moves. It models what a bank-partnered payment service provider actually does,
so a real adapter (Adyen, Stripe, a direct acquirer link) would be a thin translation layer:

    authorize ─► (3-D Secure step-up: OTP + 2FA) ─► capture / settle ─► void
    hold funds     cardholder proves possession       money taken       release hold

Why this shape matters:
  * Authorization only places a HOLD. Nothing is captured (charged) until a separate, explicit
    step, "approval before consequential action".
  * High-value or bank-partnered payments trigger a 3-D Secure STEP-UP: a one-time passcode is
    sent to the cardholder's own device (second factor = "something you have"). The agent can
    prepare everything but CANNOT produce this code, the final factor is structurally human.
    This is deferral/escalation by stakes.
  * Capture is idempotent (an idempotency key), so a retried or replayed approval never
    double-charges.
"""
import hashlib
import os
import secrets
import time
import uuid

from .base import PaymentProvider

# Saved cards. `fail` reproduces the common decline paths; `network`/`last4` drive the display.
_CARDS = {
    "pm_card_visa": {"network": "Visa", "last4": "4242", "fail": None,
                     "label": "Visa •••• 4242 (succeeds)"},
    "pm_card_mc": {"network": "Mastercard", "last4": "5454", "fail": None,
                   "label": "Mastercard •••• 5454 (succeeds)"},
    "pm_card_declined": {"network": "Visa", "last4": "0002", "fail": "card_declined",
                         "label": "Visa •••• 0002 (declined at authorization)"},
    "pm_card_insufficient": {"network": "Mastercard", "last4": "9995", "fail": "insufficient_funds",
                             "label": "Mastercard •••• 9995 (insufficient funds)"},
}

# 3-D Secure step-up fires above this amount, or always when the card is bank-partnered (all are,
# here). Tunable so a demo can show the OTP flow on small carts.
STEP_UP_THRESHOLD_CENTS = int(os.getenv("CARD_STEP_UP_THRESHOLD_CENTS", "0"))
OTP_TTL_SECONDS = int(os.getenv("CARD_OTP_TTL_SECONDS", "180"))
OTP_MAX_ATTEMPTS = 3


class CardNetwork(PaymentProvider):
    name = "CardNetwork"

    def __init__(self) -> None:
        self.auths: dict[str, dict] = {}
        self.captures: dict[str, dict] = {}  # idempotency_key -> capture (no double charge)

    # ------------------------------ saved cards ----------------------------- #
    def list_methods(self) -> list[dict]:
        return [{"id": k, "label": v["label"], "network": v["network"], "last4": v["last4"]}
                for k, v in _CARDS.items()]

    def label(self, method_id: str) -> str:
        return _CARDS.get(method_id, {"label": method_id})["label"]

    # --------------------------- Visa/MC rails ------------------------------ #
    def authorize(self, amount_cents: int, currency: str, method: str, metadata: dict) -> dict:
        """Ask the issuer to place a HOLD. Returns a 3-D Secure challenge when a step-up is
        required; declines immediately for a bad card. No funds are captured here."""
        card = _CARDS.get(method)
        if card is None:
            raise ValueError(f"Unknown card {method}")
        auth_id = "auth_" + uuid.uuid4().hex[:16]
        base = {
            "auth_id": auth_id, "amount": amount_cents, "currency": currency, "method": method,
            "network": card["network"], "last4": card["last4"], "metadata": metadata,
            "created": time.time(), "fail": card["fail"], "captured": False,
        }
        if card["fail"] == "card_declined":  # issuer refuses the hold outright
            base["status"] = "declined"
            base["decline_reason"] = "card_declined"
            self.auths[auth_id] = base
            return _public(base)

        step_up = amount_cents >= STEP_UP_THRESHOLD_CENTS  # bank-partnered: every payment steps up
        if step_up:
            otp = f"{secrets.randbelow(1_000_000):06d}"
            base.update(status="requires_3ds", step_up=True, otp=otp,
                        otp_expires_at=time.time() + OTP_TTL_SECONDS, otp_attempts_left=OTP_MAX_ATTEMPTS,
                        twofa_method="sms_otp", otp_sent_to=_mask_phone(metadata))
        else:
            base.update(status="authorized", step_up=False)
        self.auths[auth_id] = base
        return _public(base)

    def verify_3ds(self, auth_id: str, otp: str) -> dict:
        """Cardholder enters the one-time passcode (2nd factor). Enforces expiry and a hard
        attempt cap; on exhaustion the hold is voided so nothing can be captured."""
        a = self.auths[auth_id]
        if a["status"] == "authorized":
            return _public(a)  # already passed
        if a["status"] != "requires_3ds":
            raise ValueError(f"Auth {auth_id} is {a['status']}, not awaiting 3-D Secure")
        if time.time() > a["otp_expires_at"]:
            a["status"] = "voided"
            a["decline_reason"] = "otp_expired"
            return _public(a)
        if str(otp).strip() != a["otp"]:
            a["otp_attempts_left"] -= 1
            if a["otp_attempts_left"] <= 0:
                a["status"] = "voided"
                a["decline_reason"] = "otp_attempts_exceeded"
            else:
                a["decline_reason"] = "otp_mismatch"
            return _public(a)
        a["status"] = "authorized"
        a.pop("decline_reason", None)
        return _public(a)

    def capture(self, auth_id: str, idempotency_key: str) -> dict:
        """Settle the held amount, the only step that moves money. Idempotent: a repeat with the
        same key returns the first capture instead of charging again (no replay / double charge)."""
        if idempotency_key in self.captures:
            return dict(self.captures[idempotency_key])
        a = self.auths[auth_id]
        if a["status"] != "authorized":
            raise ValueError(f"Cannot capture auth {auth_id}: status is {a['status']}")
        if a["fail"] == "insufficient_funds":  # hold placed, but settlement declines
            a["status"] = "capture_declined"
            return {"status": "declined", "decline_reason": "insufficient_funds", "auth_id": auth_id}
        a["status"] = "captured"
        a["captured"] = True
        cap = {"status": "captured", "capture_id": "cap_" + uuid.uuid4().hex[:16],
               "auth_id": auth_id, "amount": a["amount"], "currency": a["currency"]}
        self.captures[idempotency_key] = cap
        return dict(cap)

    def void(self, auth_id: str) -> dict:
        a = self.auths[auth_id]
        if not a.get("captured"):
            a["status"] = "voided"
        return _public(a)

    # ----------------- legacy PaymentProvider (old single-shot path) -------- #
    # Keeps CardNetwork a drop-in for the original checkout.execute (and its tests): a single
    # confirm with no step-up, mirroring MockPay semantics.
    def create_intent(self, amount_cents, currency, payment_method, metadata) -> dict:
        card = _CARDS.get(payment_method)
        if card is None:
            raise ValueError(f"Unknown card {payment_method}")
        intent = {"id": "pi_" + uuid.uuid4().hex[:16], "amount": amount_cents, "currency": currency,
                  "payment_method": payment_method, "metadata": metadata,
                  "status": "requires_confirmation", "created": time.time()}
        self.auths[intent["id"]] = intent | {"fail": card["fail"]}
        return dict(intent)

    def confirm(self, intent_id) -> dict:
        a = self.auths[intent_id]
        fail = a.get("fail")
        a["status"] = "failed" if fail else "succeeded"
        a["failure_reason"] = fail
        return {"id": intent_id, "status": a["status"], "failure_reason": fail,
                "amount": a.get("amount"), "currency": a.get("currency")}

    def cancel(self, intent_id) -> dict:
        self.auths[intent_id]["status"] = "canceled"
        return {"id": intent_id, "status": "canceled"}


def _public(a: dict) -> dict:
    """What callers see, hide the real OTP except the sandbox `demo_otp` convenience (a real bank
    would deliver it only to the phone; here we surface it so the demo/tests can read it)."""
    out = {k: a[k] for k in ("auth_id", "status", "amount", "currency", "network", "last4")
           if k in a}
    for k in ("step_up", "decline_reason", "otp_expires_at", "otp_attempts_left",
              "twofa_method", "otp_sent_to"):
        if k in a:
            out[k] = a[k]
    if a.get("status") == "requires_3ds":
        out["demo_otp"] = a["otp"]  # SANDBOX ONLY, stands in for the SMS to the cardholder
    return out


def _mask_phone(metadata: dict) -> str:
    seed = hashlib.sha256(str(metadata).encode()).hexdigest()
    return "+65 •••• " + seed[:4].translate(str.maketrans("abcdef", "012345"))

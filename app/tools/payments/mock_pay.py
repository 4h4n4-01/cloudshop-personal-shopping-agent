"""MockPay: an in-memory payment provider for demos, tests and user studies.
No real money moves. Test cards reproduce success and the two most common failures."""
import time
import uuid

from .base import PaymentProvider

_METHODS = {
    "pm_card_visa": {"label": "Visa •••• 4242 (always succeeds)", "fail": None},
    "pm_card_declined": {"label": "Visa •••• 0002 (always declined)", "fail": "card_declined"},
    "pm_card_insufficient": {"label": "Mastercard •••• 9995 (insufficient funds)", "fail": "insufficient_funds"},
}


class MockPay(PaymentProvider):
    name = "MockPay"

    def __init__(self) -> None:
        self.intents: dict[str, dict] = {}

    def list_methods(self) -> list[dict]:
        return [{"id": k, "label": v["label"]} for k, v in _METHODS.items()]

    def label(self, method_id: str) -> str:
        return _METHODS.get(method_id, {"label": method_id})["label"]

    def create_intent(self, amount_cents, currency, payment_method, metadata):
        if payment_method not in _METHODS:
            raise ValueError(f"Unknown payment method {payment_method}")
        intent = {
            "id": "pi_" + uuid.uuid4().hex[:16], "amount": amount_cents, "currency": currency,
            "payment_method": payment_method, "metadata": metadata,
            "status": "requires_confirmation", "created": time.time(),
        }
        self.intents[intent["id"]] = intent
        return dict(intent)

    def confirm(self, intent_id):
        intent = self.intents[intent_id]
        if intent["status"] != "requires_confirmation":
            raise ValueError(f"Intent {intent_id} is {intent['status']}")
        fail = _METHODS[intent["payment_method"]]["fail"]
        intent["status"] = "failed" if fail else "succeeded"
        intent["failure_reason"] = fail
        return dict(intent)

    def cancel(self, intent_id):
        intent = self.intents[intent_id]
        intent["status"] = "canceled"
        return dict(intent)

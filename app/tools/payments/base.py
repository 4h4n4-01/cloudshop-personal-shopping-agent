"""Payment plugin interface. Any provider (MockPay, Stripe test mode, PayPal sandbox...) plugs
in by implementing these four methods. The shape mirrors Stripe PaymentIntents so a real
adapter is a thin translation layer."""
from abc import ABC, abstractmethod


class PaymentProvider(ABC):
    name: str = "base"

    @abstractmethod
    def list_methods(self) -> list[dict]:
        """Saved payment methods: [{"id", "label"}]."""

    @abstractmethod
    def create_intent(self, amount_cents: int, currency: str, payment_method: str, metadata: dict) -> dict:
        """Create an uncaptured payment intent: {"id", "status": "requires_confirmation", ...}."""

    @abstractmethod
    def confirm(self, intent_id: str) -> dict:
        """Confirm (charge). Returns {"id", "status": "succeeded"|"failed", "failure_reason"}."""

    @abstractmethod
    def cancel(self, intent_id: str) -> dict:
        """Cancel an unconfirmed intent."""

from ...config import PAYMENT_PROVIDER
from .base import PaymentProvider
from .card_network import CardNetwork
from .mock_pay import MockPay


def get_provider() -> PaymentProvider:
    """CardNetwork models Visa/Mastercard rails (authorize -> 3-D Secure OTP/2FA -> capture) and
    drives the hardened human-in-the-loop checkout. MockPay is the simpler single-shot provider."""
    if PAYMENT_PROVIDER in ("cardnetwork", "card"):
        return CardNetwork()
    if PAYMENT_PROVIDER == "mock":
        return MockPay()
    raise ValueError(f"Unknown PAYMENT_PROVIDER '{PAYMENT_PROVIDER}'. Implement it in app/tools/payments/.")

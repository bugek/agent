from backend.orders.router import get_orders_router
from backend.payments.webhook import process_payment_webhook


def bootstrap() -> None:
    get_orders_router()
    process_payment_webhook({"type": "payment.updated", "payload": {"id": "evt_1"}})

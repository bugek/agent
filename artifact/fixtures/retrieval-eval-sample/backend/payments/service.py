from backend.common.events import EventEnvelope
from backend.payments.gateway import PaymentGateway


def handle_payment_event(event: EventEnvelope) -> dict:
    gateway = PaymentGateway()
    return gateway.sync_payment(event.payload["id"])

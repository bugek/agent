from backend.common.events import parse_event
from backend.payments.service import handle_payment_event


def process_payment_webhook(raw_event: dict) -> dict:
    event = parse_event(raw_event)
    return handle_payment_event(event)

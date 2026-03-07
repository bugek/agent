from backend.orders.repository import fetch_orders


def list_orders() -> list[dict]:
    return fetch_orders()

from backend.orders.service import list_orders


def get_orders_router() -> dict:
    return {"route": "/orders", "handler": list_orders}

class PaymentGateway:
    def sync_payment(self, payment_id: str) -> dict:
        return {"payment_id": payment_id, "status": "synced"}

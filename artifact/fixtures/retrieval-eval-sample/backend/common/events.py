class EventEnvelope:
    def __init__(self, event_type: str, payload: dict):
        self.event_type = event_type
        self.payload = payload


def parse_event(raw_event: dict) -> EventEnvelope:
    return EventEnvelope(raw_event["type"], raw_event["payload"])

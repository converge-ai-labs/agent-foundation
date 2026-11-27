"""Bounded-cardinality connector delivery measurements; IDs stay in structured logs."""

from a13n_service.infra.telemetry import meter

accepted = meter.create_counter("connector_events.accepted", description="Durably accepted events by initial state")
duplicates = meter.create_counter("connector_events.duplicates", description="Identical source event redeliveries")
rejected = meter.create_counter(
    "connector_events.rejected", description="Rejected signed callback deliveries by reason"
)
deliveries = meter.create_counter("connector_events.deliveries", description="Inbox handoff outcomes")
latency = meter.create_histogram(
    "connector_events.delivery_seconds", unit="s", description="Time from durable receipt to Inbox"
)

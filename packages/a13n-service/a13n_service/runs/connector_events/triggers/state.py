"""Eligibility for explicitly resuming a saved upstream operation."""

from datetime import datetime

from a13n_service.runs.connector_events.triggers.tables import TriggerRow

UNOWNED = {"subscription_owned_elsewhere", "subscription_creation_unconfirmed"}


def retryable(row: TriggerRow, current: datetime) -> bool:
    return (
        row.operation_id is not None
        and row.operation_kind != "create_unconfirmed"
        and row.failure not in UNOWNED
        and (
            row.failure is not None
            or (row.operation_attempts > 0 and row.operation_due_at is not None and row.operation_due_at <= current)
        )
    )

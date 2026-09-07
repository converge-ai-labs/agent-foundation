"""Deadlines for one periodic maintenance loop; no per-target timers."""

from datetime import datetime, timedelta

from a13n_service.temporal import assume_utc

from .domain import TemplateConfiguration
from .models import EnvironmentRecord
from .policy import RENEWAL_MARGIN


def next_maintenance(
    row: EnvironmentRecord, configuration: TemplateConfiguration, *, requires_keepalive: bool, now: datetime
) -> datetime | None:
    if row.operation_id is not None:
        return max(now, assume_utc(row.operation_expires_at)) if row.operation_expires_at else now
    if row.status in {"unprepared", "deleted"}:
        return None
    deadlines: list[datetime] = []
    if row.retention_condition == "idle":
        window = configuration.retention.idle
        since = assume_utc(row.condition_since)
        if window.delete_after is not None:
            deadlines.append(since + timedelta(seconds=window.delete_after))
        if window.stop_after is not None and row.status != "stopped":
            deadlines.append(since + timedelta(seconds=window.stop_after))
    if row.status == "running" and requires_keepalive:
        if row.expires_at:
            expiry = assume_utc(row.expires_at)
            margin = min(RENEWAL_MARGIN, max(timedelta(), expiry - now) / 5)
            deadlines.append(expiry - margin)
        else:
            deadlines.append(now)
    return max(now, min(deadlines)) if deadlines else None

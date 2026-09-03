"""Failure-isolated observation at the Environment entry validation boundary."""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

from .models import EnvironmentAvailability, EnvironmentDescriptor
from .providers import BoundEnvironmentProvider

logger = logging.getLogger("a13n_harness.environment.entry_observation")


@runtime_checkable
class EnvironmentEntryValidationObserver(Protocol):
    """Optional synchronous observer implemented by a bound Environment provider."""

    def environment_entry_validated(
        self,
        *,
        mount_id: str,
        descriptor: EnvironmentDescriptor,
        availability: EnvironmentAvailability,
    ) -> None: ...

    def environment_entry_rejected(self, *, mount_id: str, error: BaseException) -> None: ...


def notify_entry_validated(
    provider: BoundEnvironmentProvider,
    *,
    mount_id: str,
    descriptor: EnvironmentDescriptor,
    provider_type: str,
) -> None:
    if not isinstance(provider, EnvironmentEntryValidationObserver):
        return
    try:
        provider.environment_entry_validated(
            mount_id=mount_id,
            descriptor=descriptor,
            availability=provider.availability,
        )
    except Exception:
        logger.exception(
            "Environment entry validation observation failed",
            extra={
                "event": "environment_entry_validation_observation_failed",
                "mount_id": mount_id,
                "provider_type": provider_type,
            },
        )


def notify_entry_rejected(
    provider: BoundEnvironmentProvider,
    *,
    mount_id: str,
    error: BaseException,
) -> None:
    if not isinstance(provider, EnvironmentEntryValidationObserver):
        return
    try:
        provider.environment_entry_rejected(mount_id=mount_id, error=error)
    except Exception:
        logger.exception(
            "Environment entry rejection observation failed",
            extra={
                "event": "environment_entry_rejection_observation_failed",
                "mount_id": mount_id,
            },
        )


__all__ = ["EnvironmentEntryValidationObserver", "notify_entry_rejected", "notify_entry_validated"]

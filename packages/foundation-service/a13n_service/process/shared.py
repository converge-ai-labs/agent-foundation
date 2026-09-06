"""Shared resource composition for service processes and Runner children."""

from a13n_service.gateway.a2a_push import append_matching_a2a_push_outbox
from a13n_service.hooks.persistence import append_matching_webhook_outbox
from a13n_service.interactions.lifecycle import LifecycleWriter
from a13n_service.process.runtime import SharedRuntime
from a13n_service.settings import Settings
from a13n_service.storage import StorageResources


def build_shared_runtime(settings: Settings, storage: StorageResources) -> SharedRuntime:
    return SharedRuntime(
        storage=storage,
        lifecycle=LifecycleWriter(
            (append_matching_webhook_outbox, append_matching_a2a_push_outbox)
            if settings.a2a_enabled
            else (append_matching_webhook_outbox,)
        ),
        secret_protector=settings.secret_protector(),
    )

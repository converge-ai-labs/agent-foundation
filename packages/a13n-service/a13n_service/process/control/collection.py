"""Control contribution for domain-owned relational collection."""

from datetime import timedelta

from a13n_service.assets.retention import AssetRetention
from a13n_service.background import PeriodicTask
from a13n_service.hooks.retention import HookRetention
from a13n_service.iam.auth.cleanup import IdentityTokenCleanup
from a13n_service.iam.cleanup import OwnerCleanup
from a13n_service.object_retention.collector import ObjectCollector
from a13n_service.process.background import BackgroundTask
from a13n_service.process.runtime import SharedRuntime
from a13n_service.search.cleanup import SearchProviderOwnerCleanup
from a13n_service.secrets.cleanup import SecretOwnerCleanup
from a13n_service.settings import Settings
from a13n_service.skills.retention import SkillUploadRetention


def build_collection_tasks(settings: Settings, shared: SharedRuntime) -> tuple[BackgroundTask, ...]:
    sessions = shared.storage.sessions
    limit = settings.control_collection_batch_limit
    objects = ObjectCollector(
        sessions,
        shared.storage.objects,
        minimum_age=timedelta(hours=settings.object_orphan_minimum_age_hours),
        batch_limit=limit,
        item_timeout_seconds=settings.control_collection_timeout_seconds,
    )
    scans = (
        ("identity_token_cleanup", IdentityTokenCleanup(sessions, batch_limit=limit).scan),
        ("owner_deletion_cleanup", OwnerCleanup(sessions, batch_limit=limit).scan),
        (
            "asset_tombstone_retention",
            AssetRetention(
                sessions, minimum_age=timedelta(days=settings.asset_tombstone_minimum_retention_days), batch_limit=limit
            ).scan,
        ),
        ("secret_owner_cleanup", SecretOwnerCleanup(sessions, batch_limit=limit).scan),
        ("search_provider_owner_cleanup", SearchProviderOwnerCleanup(sessions, batch_limit=limit).scan),
        ("skill_upload_retention", SkillUploadRetention(sessions, batch_limit=limit).scan),
        (
            "hook_history_retention",
            HookRetention(
                sessions, minimum_age=timedelta(days=settings.hook_history_minimum_retention_days), batch_limit=limit
            ).scan,
        ),
        ("orphan_object_collection", objects.scan),
        ("object_collection_recovery", objects.recover),
    )
    return tuple(
        BackgroundTask(
            name,
            PeriodicTask(
                name,
                scan,
                interval_seconds=settings.control_collection_poll_interval_seconds,
                timeout_seconds=(settings.control_collection_timeout_seconds + 1) * limit,
            ).run,
        )
        for name, scan in scans
    )

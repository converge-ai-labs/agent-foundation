"""Surface-neutral configuration commands, queries, and subscriptions."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import AsyncGenerator, Mapping, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from uuid import uuid4

import yaml
from anyio import CancelScope, Lock, create_memory_object_stream, sleep, to_thread
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream

from a13n_ui.errors import ConfigurationError

from .catalog import CatalogRepository, ConfigurationDiagnostic
from .loader import CatalogCandidate, load_catalog_candidate, load_configuration_settings
from .models import (
    ConfigurationGeneration,
    ConfigurationSettings,
    LocalSkillDiscoverySettings,
    LocalSkillSourceDefinition,
    ResourceKind,
    ResourceRef,
    ResourceRevision,
    ResourceRevisionRef,
    SourceTransactionEntry,
    SourceTransactionManifest,
    canonical_digest,
    restart_settings_digest,
)
from .skills import (
    SkillChangePreview,
    SkillConflictPreview,
    SkillScanPreview,
    SkillService,
    SkillSourceStatus,
)
from .transactions import (
    commit_source_transaction,
    discard_source_transaction,
    finalize_source_transaction,
    prepare_source_transaction,
    rebase_source_transaction,
    rollback_source_transaction,
)


class ConfigurationService:
    """Serialize generation changes while returning detached immutable values."""

    def __init__(
        self,
        settings: ConfigurationSettings,
        repository: CatalogRepository,
        *,
        process_settings_path: Path | None = None,
    ) -> None:
        self._bootstrap_settings = settings
        self._settings = settings
        self._process_settings_path = process_settings_path
        self._repository = repository
        self._activated_restart_settings_digest = restart_settings_digest(settings)
        self._reload_lock = Lock()
        self._subscribers: set[MemoryObjectSendStream[ConfigurationGeneration]] = set()
        self.skills = SkillService(settings, repository)

    @property
    def settings(self) -> ConfigurationSettings:
        return self._settings

    async def initialize(self) -> ConfigurationGeneration | None:
        """Accept the initial candidate or retain the last known good generation."""

        try:
            generation = await self._reload(initial=True)
        except ConfigurationError as exc:
            await self._repository.record_rejection(exc)
            generation = await self._repository.current_generation()
            if generation is not None:
                accepted_settings = await self._repository.generation_settings(generation.generation_id)
                self._settings = accepted_settings
                self.skills.update_settings(accepted_settings)
        if generation is not None:
            await self._repository.verify_current()
        return generation

    async def reload(self) -> ConfigurationGeneration:
        try:
            return await self._reload()
        except ConfigurationError as exc:
            await self._repository.record_rejection(exc)
            raise

    async def _reload(self, *, initial: bool = False) -> ConfigurationGeneration:
        async with self._reload_lock:
            before = await self._repository.current_generation()
            desired = await load_configuration_settings(
                self._process_settings_path,
                self._bootstrap_settings,
            )
            candidate = await load_catalog_candidate(desired)
            self._validate_active_source_transactions(before, candidate)
            if initial:
                self._activated_restart_settings_digest = candidate.restart_settings_digest
            generation = await self._repository.accept(
                candidate,
                activated_restart_settings_digest=self._activated_restart_settings_digest,
            )
            await self._rebase_active_source_transactions(desired, candidate, generation.catalog_digest)
            self._settings = desired
            self.skills.update_settings(desired)
            if before is None or before.generation_id != generation.generation_id:
                await self._publish(generation)
            return generation

    @staticmethod
    def _validate_active_source_transactions(
        current: ConfigurationGeneration | None,
        candidate: CatalogCandidate,
    ) -> None:
        if not candidate.source_transactions:
            return
        if current is None:
            raise ConfigurationError(
                "Active source transactions require an accepted base generation.",
                code="source_transaction_stale",
            )
        if candidate.catalog_digest == current.catalog_digest:
            return
        if any(manifest.base_catalog_digest != current.catalog_digest for manifest in candidate.source_transactions):
            raise ConfigurationError(
                "An active source transaction is not based on the accepted generation.",
                code="source_transaction_stale",
            )

    @staticmethod
    async def _rebase_active_source_transactions(
        settings: ConfigurationSettings,
        candidate: CatalogCandidate,
        accepted_catalog_digest: str,
    ) -> None:
        roots = {root.root_id: root.path for root in settings.ordered_roots}
        for manifest in candidate.source_transactions:
            if manifest.base_catalog_digest == accepted_catalog_digest:
                continue
            root = roots.get(manifest.root_id)
            if root is None:
                raise ConfigurationError(
                    "An active source transaction selects an unavailable definition root.",
                    code="source_transaction_invalid",
                )
            await rebase_source_transaction(
                root,
                manifest,
                accepted_catalog_digest=accepted_catalog_digest,
            )

    async def apply_transaction(
        self,
        manifest: SourceTransactionManifest,
        replacements: Mapping[str, bytes],
    ) -> ConfigurationGeneration:
        async with self._reload_lock:
            try:
                return await self._apply_transaction_locked(manifest, replacements)
            except ConfigurationError as exc:
                await self._repository.record_rejection(exc)
                raise

    async def _apply_transaction_locked(
        self,
        manifest: SourceTransactionManifest,
        replacements: Mapping[str, bytes],
    ) -> ConfigurationGeneration:
        current = await self._repository.current_generation()
        if current is None:
            raise ConfigurationError(
                "Source edits require an accepted configuration generation.",
                code="configuration_generation_missing",
            )
        baseline = await load_catalog_candidate(self._settings)
        if baseline.catalog_digest != current.catalog_digest:
            raise ConfigurationError(
                "The source transaction base changed before it could be prepared.",
                code="source_transaction_stale",
            )
        prepared = await prepare_source_transaction(
            self._settings,
            manifest,
            replacements,
            current_catalog_digest=current.catalog_digest,
        )
        try:
            candidate = await load_catalog_candidate(
                self._settings,
                source_overlays={manifest.root_id: prepared.overlay},
            )
        except BaseException:
            await discard_source_transaction(prepared)
            raise
        try:
            confirmed_base = await load_catalog_candidate(self._settings)
            if confirmed_base.catalog_digest != current.catalog_digest:
                raise ConfigurationError(
                    "The source transaction base changed during validation.",
                    code="source_transaction_stale",
                )
        except BaseException:
            await discard_source_transaction(prepared)
            raise
        try:
            await commit_source_transaction(prepared)
        except BaseException:
            await discard_source_transaction(prepared)
            raise
        try:
            selected_candidate = await load_catalog_candidate(self._settings)
            if selected_candidate.catalog_digest != candidate.catalog_digest:
                raise ConfigurationError(
                    "The configuration sources changed while the source transaction was selected.",
                    code="source_transaction_stale",
                )
            self._validate_active_source_transactions(current, selected_candidate)
        except BaseException:
            with CancelScope(shield=True):
                await rollback_source_transaction(prepared)
            raise
        generation = await self._repository.accept(
            selected_candidate,
            activated_restart_settings_digest=self._activated_restart_settings_digest,
        )
        await self._rebase_active_source_transactions(
            self._settings,
            selected_candidate,
            generation.catalog_digest,
        )
        await finalize_source_transaction(
            prepared,
            accepted_catalog_digest=generation.catalog_digest,
        )
        if generation.generation_id != current.generation_id:
            await self._publish(generation)
        return generation

    async def reconcile_periodically(self) -> None:
        """Reconcile file-backed desired state without making watcher delivery authoritative."""

        last_rejection: tuple[str, str] | None = None
        while True:
            await sleep(self._settings.reconciliation_interval_seconds)
            try:
                await self._reload()
            except ConfigurationError as exc:
                rejection = (exc.code, str(exc))
                if rejection != last_rejection:
                    await self._repository.record_rejection(exc)
                last_rejection = rejection
            else:
                last_rejection = None

    async def current_generation(self) -> ConfigurationGeneration | None:
        return await self._repository.current_generation()

    async def retained_generations(self, *, limit: int = 100) -> tuple[ConfigurationGeneration, ...]:
        return await self._repository.retained_generations(limit=limit)

    async def resource(self, reference: ResourceRevisionRef) -> ResourceRevision:
        return await self._repository.resource(reference)

    async def diagnostics(self, *, limit: int = 100) -> tuple[ConfigurationDiagnostic, ...]:
        return await self._repository.diagnostics(limit=limit)

    async def skill_source_statuses(self) -> tuple[SkillSourceStatus, ...]:
        async with self._reload_lock:
            generation = await self._repository.current_generation()
            settings = self._settings
        if generation is None:
            return ()
        return await self.skills.source_statuses(generation, settings)

    async def upsert_skill_source(
        self,
        source: LocalSkillSourceDefinition,
        *,
        target_root_id: str,
    ) -> ConfigurationGeneration:
        """Create or edit one Skill source through the ordinary source transaction path."""

        async with self._reload_lock:
            try:
                current = await self._require_generation()
                reference = next(
                    (
                        item
                        for item in current.resources
                        if item.kind is ResourceKind.skill_source and item.resource_id == source.skill_source_id
                    ),
                    None,
                )
                if reference is None:
                    relative_path = f"skill-sources/{source.skill_source_id}.yaml"
                else:
                    revision = await self._repository.resource(reference)
                    if revision.source.source_id != target_root_id:
                        raise ConfigurationError(
                            "Editing a Skill source cannot move it between definition roots.",
                            code="skill_source_target_conflict",
                        )
                    relative_path = revision.source.relative_path
                content = yaml.safe_dump(
                    source.model_dump(mode="json"),
                    allow_unicode=True,
                    sort_keys=True,
                ).encode("utf-8")
                manifest = _single_source_transaction(
                    root_id=target_root_id,
                    base_catalog_digest=current.catalog_digest,
                    relative_path=relative_path,
                    content=content,
                )
                return await self._apply_transaction_locked(manifest, {relative_path: content})
            except ConfigurationError as exc:
                await self._repository.record_rejection(exc)
                raise

    async def delete_skill_source(self, skill_source_id: str) -> ConfigurationGeneration:
        """Delete one unselected writable Skill source revision."""

        async with self._reload_lock:
            try:
                current = await self._require_generation()
                if any(item.resource_id == skill_source_id for item in self._settings.skill_discovery.ordered_sources):
                    raise ConfigurationError(
                        "An enabled Skill source must be disabled before deletion.",
                        code="skill_source_selected",
                    )
                reference = next(
                    (
                        item
                        for item in current.resources
                        if item.kind is ResourceKind.skill_source and item.resource_id == skill_source_id
                    ),
                    None,
                )
                if reference is None:
                    raise ConfigurationError(
                        "The selected Skill source does not exist.",
                        code="skill_source_missing",
                    )
                revision = await self._repository.resource(reference)
                manifest = SourceTransactionManifest(
                    schema_version="1",
                    transaction_id=f"transaction-{uuid4().hex[:16]}",
                    root_id=revision.source.source_id,
                    base_catalog_digest=current.catalog_digest,
                    entries=(
                        SourceTransactionEntry(
                            relative_path=revision.source.relative_path,
                            operation="delete",
                        ),
                    ),
                )
                return await self._apply_transaction_locked(manifest, {})
            except ConfigurationError as exc:
                await self._repository.record_rejection(exc)
                raise

    async def reorder_skill_sources(
        self,
        skill_source_ids: Sequence[str],
    ) -> ConfigurationGeneration:
        """Persist the exact enabled Skill source order in the process settings document."""

        async with self._reload_lock:
            try:
                current = await self._require_generation()
                source_ids = tuple(skill_source_ids)
                if len(source_ids) != len(set(source_ids)):
                    raise ConfigurationError(
                        "Enabled Skill sources must be unique.",
                        code="skill_source_order_invalid",
                    )
                available = {item.resource_id for item in current.resources if item.kind is ResourceKind.skill_source}
                if any(source_id not in available for source_id in source_ids):
                    raise ConfigurationError(
                        "An enabled Skill source is absent from the accepted generation.",
                        code="skill_source_missing",
                    )
                if self._process_settings_path is None:
                    raise ConfigurationError(
                        "Skill source ordering requires a file-backed process settings document.",
                        code="process_settings_not_writable",
                    )
                observed = await load_configuration_settings(
                    self._process_settings_path,
                    self._bootstrap_settings,
                )
                if canonical_digest(observed) != current.process_settings_digest:
                    raise ConfigurationError(
                        "The process settings changed before the Skill source order could be written.",
                        code="process_settings_stale",
                    )
                baseline = await load_catalog_candidate(observed)
                if baseline.catalog_digest != current.catalog_digest:
                    raise ConfigurationError(
                        "The configuration sources changed before the Skill source order could be written.",
                        code="source_transaction_stale",
                    )
                desired = observed.model_copy(
                    update={
                        "skill_discovery": observed.skill_discovery.model_copy(
                            update={
                                "ordered_sources": tuple(
                                    ResourceRef(
                                        kind=ResourceKind.skill_source,
                                        resource_id=source_id,
                                    )
                                    for source_id in source_ids
                                )
                            }
                        )
                    }
                )
                candidate = await load_catalog_candidate(desired)
                confirmed = await load_configuration_settings(
                    self._process_settings_path,
                    self._bootstrap_settings,
                )
                if canonical_digest(confirmed) != current.process_settings_digest:
                    raise ConfigurationError(
                        "The process settings changed while the Skill source order was validated.",
                        code="process_settings_stale",
                    )
                confirmed_baseline = await load_catalog_candidate(confirmed)
                if confirmed_baseline.catalog_digest != current.catalog_digest:
                    raise ConfigurationError(
                        "The configuration sources changed while the Skill source order was validated.",
                        code="source_transaction_stale",
                    )
                try:
                    await to_thread.run_sync(
                        _write_process_settings,
                        self._process_settings_path,
                        desired,
                    )
                except OSError as exc:
                    raise ConfigurationError(
                        "The process settings document could not be written.",
                        code="process_settings_write_failed",
                    ) from exc
                generation = await self._repository.accept(
                    candidate,
                    activated_restart_settings_digest=self._activated_restart_settings_digest,
                )
                self._settings = desired
                self.skills.update_settings(desired)
                if generation.generation_id != current.generation_id:
                    await self._publish(generation)
                return generation
            except ConfigurationError as exc:
                await self._repository.record_rejection(exc)
                raise

    async def skill_conflicts(
        self,
        discovery: LocalSkillDiscoverySettings | None = None,
    ) -> SkillConflictPreview:
        async with self._reload_lock:
            generation = await self._repository.current_generation()
            settings = self._settings
        if generation is None:
            raise ConfigurationError(
                "Skill conflict inspection requires an accepted configuration generation.",
                code="skill_generation_missing",
            )
        return await self.skills.conflicts(
            discovery or settings.skill_discovery,
            generation=generation,
            settings=settings,
        )

    async def scan_skill_source(self, skill_source_id: str) -> SkillScanPreview:
        async with self._reload_lock:
            generation = await self._repository.current_generation()
            settings = self._settings
        discovery = settings.skill_discovery.model_copy(
            update={
                "ordered_sources": (
                    ResourceRef(
                        kind=ResourceKind.skill_source,
                        resource_id=skill_source_id,
                    ),
                ),
                "conflict": "error",
            }
        )
        return await self._scan_skills(
            discovery,
            generation=generation,
            settings=settings,
        )

    async def scan_skills(
        self,
        discovery: LocalSkillDiscoverySettings | None = None,
    ) -> SkillScanPreview:
        async with self._reload_lock:
            generation = await self._repository.current_generation()
            settings = self._settings
        return await self._scan_skills(
            discovery or settings.skill_discovery,
            generation=generation,
            settings=settings,
        )

    async def _scan_skills(
        self,
        discovery: LocalSkillDiscoverySettings,
        *,
        generation: ConfigurationGeneration | None,
        settings: ConfigurationSettings,
    ) -> SkillScanPreview:
        if generation is None:
            raise ConfigurationError(
                "Skill discovery requires an accepted configuration generation.",
                code="skill_generation_missing",
            )
        return await self.skills.scan(
            discovery,
            generation=generation,
            settings=settings,
        )

    async def discard_skill_scan(self, scan_id: str) -> None:
        await self.skills.discard_scan(scan_id)

    async def preview_skill_import(
        self,
        *,
        scan_id: str,
        skill_name: str,
        skill_id: str,
        display_name: str,
        target_root_id: str,
    ) -> SkillChangePreview:
        return await self.skills.preview_import(
            scan_id=scan_id,
            skill_name=skill_name,
            skill_id=skill_id,
            display_name=display_name,
            target_root_id=target_root_id,
        )

    async def preview_skill_refresh(
        self,
        *,
        scan_id: str,
        skill_name: str,
        skill_id: str,
    ) -> SkillChangePreview:
        return await self.skills.preview_refresh(
            scan_id=scan_id,
            skill_name=skill_name,
            skill_id=skill_id,
        )

    async def accept_skill_import(self, change_id: str) -> ConfigurationGeneration:
        return await self._accept_skill_change(change_id, expected_operation="import")

    async def accept_skill_refresh(self, change_id: str) -> ConfigurationGeneration:
        return await self._accept_skill_change(change_id, expected_operation="refresh")

    async def _accept_skill_change(
        self,
        change_id: str,
        *,
        expected_operation: Literal["import", "refresh"] | None = None,
    ) -> ConfigurationGeneration:
        async with self._reload_lock:
            try:
                manifest, replacements = await self.skills.accept_change(
                    change_id,
                    expected_operation=expected_operation,
                )
                return await self._apply_transaction_locked(manifest, replacements)
            except ConfigurationError as exc:
                await self._repository.record_rejection(exc)
                raise

    async def discard_skill_change(self, change_id: str) -> None:
        await self.skills.discard_change(change_id)

    @asynccontextmanager
    async def subscribe(
        self, *, capacity: int = 16
    ) -> AsyncGenerator[MemoryObjectReceiveStream[ConfigurationGeneration]]:
        if not 1 <= capacity <= 1024:
            raise ValueError("subscription capacity must be between 1 and 1024")
        send, receive = create_memory_object_stream[ConfigurationGeneration](capacity)
        current = await self._repository.current_generation()
        if current is not None:
            await send.send(current)
        self._subscribers.add(send)
        try:
            yield receive
        finally:
            self._subscribers.discard(send)
            await send.aclose()
            await receive.aclose()

    async def _require_generation(self) -> ConfigurationGeneration:
        generation = await self._repository.current_generation()
        if generation is None:
            raise ConfigurationError(
                "The operation requires an accepted configuration generation.",
                code="configuration_generation_missing",
            )
        return generation

    async def close(self) -> None:
        await self.skills.close()
        subscribers = tuple(self._subscribers)
        self._subscribers.clear()
        for subscriber in subscribers:
            await subscriber.aclose()

    async def _publish(self, generation: ConfigurationGeneration) -> None:
        stale: list[MemoryObjectSendStream[ConfigurationGeneration]] = []
        for subscriber in tuple(self._subscribers):
            try:
                subscriber.send_nowait(generation)
            except Exception:
                stale.append(subscriber)
        for subscriber in stale:
            self._subscribers.discard(subscriber)
            await subscriber.aclose()


def _single_source_transaction(
    *,
    root_id: str,
    base_catalog_digest: str,
    relative_path: str,
    content: bytes,
) -> SourceTransactionManifest:
    return SourceTransactionManifest(
        schema_version="1",
        transaction_id=f"transaction-{uuid4().hex[:16]}",
        root_id=root_id,
        base_catalog_digest=base_catalog_digest,
        entries=(
            SourceTransactionEntry(
                relative_path=relative_path,
                operation="replace",
                content_digest=hashlib.sha256(content).hexdigest(),
            ),
        ),
    )


def _write_process_settings(path: Path, settings: ConfigurationSettings) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}-{uuid4().hex}.tmp")
    if path.suffix.lower() == ".json":
        content = (
            json.dumps(
                settings.model_dump(mode="json"),
                ensure_ascii=False,
                allow_nan=False,
                indent=2,
                sort_keys=True,
            ).encode("utf-8")
            + b"\n"
        )
    else:
        content = yaml.safe_dump(
            settings.model_dump(mode="json"),
            allow_unicode=True,
            sort_keys=True,
        ).encode("utf-8")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            descriptor = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)


__all__ = ["ConfigurationService"]

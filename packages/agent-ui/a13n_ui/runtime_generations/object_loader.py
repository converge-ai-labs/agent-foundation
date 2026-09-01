"""Narrow read-only loader for exact immutable objects selected by the Host."""

from __future__ import annotations

import json
from importlib.metadata import version

from a13n_harness import HarnessState
from pydantic import JsonValue, TypeAdapter, ValidationError
from pydantic_ai.tools import DeferredToolRequests

from a13n_ui.composition import ResolvedAgentSnapshot, ResolvedEnvironmentSnapshot, ResolvedSkill
from a13n_ui.environments import SessionEnvironmentResource, StoredProviderState
from a13n_ui.errors import StoreIntegrityError
from a13n_ui.sessions import StoredSessionContinuation
from a13n_ui.settings import StorageSettings
from a13n_ui.storage import ImmutableObjectStore, ObjectKind, ObjectRef, StorageLayout


class RunnerObjectLoader:
    """Read named objects without database, listing, publication, or selection authority."""

    def __init__(self, settings: StorageSettings) -> None:
        layout = StorageLayout.from_root(settings.data_root)
        self._objects = ImmutableObjectStore(layout, settings, producer_release=version("a13n-ui"))

    async def agent_snapshot(self, reference: ObjectRef) -> ResolvedAgentSnapshot:
        payload = await self._payload(reference, ObjectKind.agent_snapshot)
        try:
            return ResolvedAgentSnapshot.model_validate_json(json.dumps(payload), strict=True)
        except ValidationError as exc:
            raise StoreIntegrityError("A selected Agent snapshot is invalid.", code="agent_snapshot_invalid") from exc

    async def environment_snapshot(self, reference: ObjectRef) -> ResolvedEnvironmentSnapshot:
        payload = await self._payload(reference, ObjectKind.environment_snapshot)
        try:
            return ResolvedEnvironmentSnapshot.model_validate_json(json.dumps(payload), strict=True)
        except ValidationError as exc:
            raise StoreIntegrityError(
                "A selected Environment snapshot is invalid.", code="environment_snapshot_invalid"
            ) from exc

    async def continuation(
        self,
        reference: ObjectRef,
    ) -> tuple[HarnessState, DeferredToolRequests | None]:
        payload = await self._payload(reference, ObjectKind.session_continuation)
        try:
            stored = StoredSessionContinuation.model_validate_json(json.dumps(payload), strict=True)
            state = HarnessState.model_validate(stored.harness_state)
            deferred = (
                TypeAdapter(DeferredToolRequests).validate_python(stored.deferred_requests)
                if stored.deferred_requests is not None
                else None
            )
        except ValidationError as exc:
            raise StoreIntegrityError("A selected continuation is invalid.", code="continuation_invalid") from exc
        if stored.harness_release != version("a13n-harness"):
            raise StoreIntegrityError(
                "A selected continuation requires another Harness release.",
                code="continuation_harness_mismatch",
            )
        return state, deferred

    async def skill_package(self, skill: ResolvedSkill) -> JsonValue:
        reference = ObjectRef(
            object_kind=ObjectKind.skill_package,
            object_schema_version="1",
            logical_digest=skill.package_object_digest,
        )
        return await self._payload(reference, ObjectKind.skill_package)

    async def provider_state(
        self,
        reference: ObjectRef,
        resource: SessionEnvironmentResource,
    ) -> StoredProviderState:
        payload = await self._payload(reference, ObjectKind.provider_state)
        try:
            stored = StoredProviderState.model_validate_json(json.dumps(payload), strict=True)
        except ValidationError as exc:
            raise StoreIntegrityError("A selected provider state is invalid.", code="provider_state_invalid") from exc
        if (
            stored.session_id != resource.session_id
            or stored.mount_name != resource.mount_name
            or stored.provider_key != resource.provider_key
            or stored.provider_spec_digest != resource.provider_spec_digest
        ):
            raise StoreIntegrityError(
                "A selected provider state does not match its resource.",
                code="provider_state_reference_mismatch",
            )
        return stored

    async def _payload(self, reference: ObjectRef, expected_kind: ObjectKind) -> JsonValue:
        if reference.object_kind is not expected_kind or reference.object_schema_version != "1":
            raise StoreIntegrityError("A selected object has the wrong kind.", code="object_reference_mismatch")
        envelope = await self._objects.read(reference)
        return envelope.payload


__all__ = ["RunnerObjectLoader"]

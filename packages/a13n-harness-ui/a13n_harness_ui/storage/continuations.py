"""Root checkpoint decoding, forward-only publication and owned-payload retention."""

from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import uuid4

from a13n_harness import HarnessState
from anyio import to_thread
from pydantic import Field, RootModel

from a13n_harness_ui.display_history import detach_display_history
from a13n_harness_ui.errors import ObjectIntegrityError

from .contracts import Continuation, ContinuationMetadata, ReferencedContinuation, StoredContinuation, StoredContract
from .display import DisplayArchive
from .objects import ImmutableObjectStore, ObjectKind, ObjectRef


class ContinuationPayload(
    RootModel[Annotated[StoredContinuation | ReferencedContinuation, Field(discriminator="schema_version")]]
):
    """Decode either released inline state or the referenced manifest."""


class ContinuationState(StoredContract):
    schema_version: Literal["1"] = "1"
    checkpoint_id: str
    harness_state: HarnessState


class ContinuationDisplay(StoredContract):
    schema_version: Literal["1"] = "1"
    checkpoint_id: str
    archive: DisplayArchive


def _metadata(value: ContinuationMetadata) -> dict[str, Any]:
    # Transfer typed metadata without serializing native state or display. Opaque
    # additions survive upgrades, but retired representation fields do not.
    reserved = {"schema_version", "checkpoint_id", "harness_state", "native_state", "display", "display_loaded"}
    return {
        **{key: item for key, item in (value.model_extra or {}).items() if key not in reserved},
        **{name: getattr(value, name) for name in ContinuationMetadata.model_fields},
    }


async def read_continuation(
    objects: ImmutableObjectStore, reference: ObjectRef, *, include_display: bool = False
) -> Continuation:
    """Resolve an exact checkpoint; callers protect replaceable heads with the Thread lock."""
    stored = (await objects.read_model(reference, ContinuationPayload)).root
    if isinstance(stored, StoredContinuation):
        state, display = await to_thread.run_sync(detach_display_history, stored.harness_state)
        archive = DisplayArchive(tail=display) if display is not None and include_display else None
    else:
        native = await objects.read_model(stored.native_state, ContinuationState)
        _check_owner(stored.checkpoint_id, native.checkpoint_id)
        state = native.harness_state
        archive = None
        if include_display and stored.display is not None:
            display_payload = await objects.read_model(stored.display, ContinuationDisplay)
            _check_owner(stored.checkpoint_id, display_payload.checkpoint_id)
            archive = display_payload.archive
    return Continuation.model_validate(
        {**_metadata(stored), "harness_state": state, "display": archive, "display_loaded": include_display},
        extra="allow",
    )


def _check_owner(expected: str, actual: str) -> None:
    if actual != expected:
        raise ObjectIntegrityError(
            "Checkpoint payload belongs to another checkpoint.", code="object_payload_incompatible"
        )


async def publish_continuation(objects: ImmutableObjectStore, value: Continuation) -> ObjectRef:
    """Publish owned state and display before the manifest; selection remains the caller's CAS."""
    if not value.display_loaded:
        raise ValueError("Load display before publishing a continuation")
    state, legacy_display = await to_thread.run_sync(detach_display_history, value.harness_state)
    archive = value.display
    if archive is None and legacy_display is not None:
        archive = DisplayArchive(tail=legacy_display)
    checkpoint_id = uuid4().hex
    native_ref = (
        await objects.publish_model(
            object_kind=ObjectKind.continuation_state,
            value=ContinuationState(checkpoint_id=checkpoint_id, harness_state=state),
        )
    ).ref
    display_ref = None
    if archive is not None:
        display_ref = (
            await objects.publish_model(
                object_kind=ObjectKind.continuation_display,
                value=ContinuationDisplay(checkpoint_id=checkpoint_id, archive=archive),
            )
        ).ref
    manifest = ReferencedContinuation.model_validate(
        {**_metadata(value), "checkpoint_id": checkpoint_id, "native_state": native_ref, "display": display_ref},
        extra="allow",
    )
    return (await objects.publish_model(object_kind=ObjectKind.continuation, value=manifest)).ref


async def remove_continuation(objects: ImmutableObjectStore, reference: ObjectRef) -> None:
    """Release a superseded checkpoint under its Thread lock, never shared prefix segments."""
    stored = (await objects.read_model(reference, ContinuationPayload)).root
    await objects.remove(reference)
    if isinstance(stored, ReferencedContinuation):
        await objects.remove(stored.native_state)
        if stored.display is not None:
            await objects.remove(stored.display)

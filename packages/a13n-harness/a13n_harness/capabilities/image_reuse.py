"""Run-scoped reuse of Host-held native images in base64 tool parameters."""

from __future__ import annotations

import json
from copy import copy
from dataclasses import dataclass
from hashlib import sha256
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pydantic_ai import BinaryContent, FunctionToolset, ModelRetry, RunContext, ToolApproved, ToolReturn
from pydantic_ai.capabilities import CapabilityOrdering
from pydantic_ai.messages import ModelRequest, ToolCallPart, UserPromptPart
from pydantic_ai.models import ModelRequestContext
from pydantic_ai.tools import ToolDefinition

from a13n_harness._json import project_json
from a13n_harness.content import ContentItem, annotate_prompt, prompt_content
from a13n_harness.context import AgentContext
from a13n_harness.errors import DefinitionError
from a13n_harness.model_context import (
    AbstractModelContextCapability,
    ModelContextBlock,
    ModelContextCoordinatorCapability,
    ModelContextNext,
    ModelContextPlacement,
    ModelContextProjection,
    ModelContextProjectionRequest,
)
from a13n_harness.tools._deferred_dispatch import FORWARDED_TOOL_KEY

IMAGE_REUSE_CAPABILITY_ID = "a13n.image-reuse"
_PREFIX = "image-base64:"
_SOURCE = "a13n.image-source"


class ImageReuseLimits(BaseModel):
    """Bounds on original images retained by one logical Run."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    max_images: int = Field(default=64, ge=1, le=256)
    max_image_bytes: int = Field(default=10 * 1024 * 1024, ge=1)
    max_total_bytes: int = Field(default=32 * 1024 * 1024, ge=1)


class _Image(BaseModel):
    model_config = ConfigDict(ser_json_bytes="base64", val_json_bytes="base64")
    reference: str
    run_id: str
    source: dict[str, Any]
    content: BinaryContent

    def descriptor(self) -> dict[str, Any]:
        return {
            "base64_reference": self.reference,
            "source": self.source,
            "identifier": self.content.identifier,
            "media_type": self.content.media_type,
            "size_bytes": len(self.content.data),
            "sha256": sha256(self.content.data).hexdigest(),
        }


class _Images(BaseModel):
    thread_id: str
    run_id: str
    images: list[_Image] = Field(default_factory=list)
    omitted: bool = False


def _references(value: Any) -> set[str]:
    if isinstance(value, str):
        return {value} if value.startswith(_PREFIX) else set()
    if isinstance(value, dict):
        return set().union(*(_references(item) for item in value.values()))
    if isinstance(value, list | tuple):
        return set().union(*(_references(item) for item in value))
    return set()


@dataclass(init=False)
class ImageReuseCapability(AbstractModelContextCapability):
    """Bind exact issued reference strings before native target schema validation.

    Enable this capability on an Agent definition. Only byte-backed native images
    are admitted; URL/provider handles and JSON objects are not image sources.
    """

    id = IMAGE_REUSE_CAPABILITY_ID

    def __init__(self, limits: ImageReuseLimits | None = None) -> None:
        self.limits = limits or ImageReuseLimits()
        self._state: _Images | None = None
        self._context: AgentContext | None = None
        self._bindings: dict[str, dict[tuple[str | int, ...], _Image]] = {}
        self._resume_calls: dict[str, tuple[str, set[str]]] = {}

    def get_ordering(self) -> CapabilityOrdering:
        from a13n_harness.capabilities.context import CompactionCapability
        from a13n_harness.capabilities.input import InputCapability

        # Capture canonical bytes after admission annotation, before compaction
        # and the coordinator's request-only image filters.
        return CapabilityOrdering(
            position="outermost",
            wrapped_by=(InputCapability,),
            wraps=(CompactionCapability, ModelContextCoordinatorCapability),
        )

    @classmethod
    def get_serialization_name(cls) -> None:
        return None  # Hosts compose this code-first capability explicitly.

    async def for_run(self, ctx: RunContext[AgentContext]) -> ImageReuseCapability:
        existing = ctx.deps._run_capability(IMAGE_REUSE_CAPABILITY_ID)
        if existing is not None:
            if not isinstance(existing, ImageReuseCapability):
                raise DefinitionError("Incompatible image reuse owner.", code="capability_type_mismatch")
            return existing
        owner = ImageReuseCapability(self.limits)
        owner._context = ctx.deps
        state = await ctx.deps.state.read(IMAGE_REUSE_CAPABILITY_ID, _Images, version="1")
        scope_id = ctx.deps.context_recovery_scope_id or ctx.deps.run_id
        if state is None or state.thread_id != ctx.deps.thread_id:
            state = _Images(thread_id=ctx.deps.thread_id, run_id=scope_id)
        if state.run_id != scope_id:
            accepted = ctx.deps._deferred_input
            pending = accepted.pending if accepted is not None else None
            if pending is not None and not pending.recovery:
                for item in pending.requests.approvals:
                    target = item.target_call
                    decision = pending.results.approvals[target.tool_call_id]
                    if decision is True or isinstance(decision, ToolApproved):
                        owner._resume_calls[target.tool_call_id] = (
                            target.tool_name,
                            _references(target.args_as_dict()),
                        )
            retained = set().union(*(references for _, references in owner._resume_calls.values()))
            state = _Images(
                thread_id=ctx.deps.thread_id,
                run_id=scope_id,
                images=[image for image in state.images if image.reference in retained],
            )
        if (
            len(state.images) > owner.limits.max_images
            or any(len(image.content.data) > owner.limits.max_image_bytes for image in state.images)
            or sum(len(image.content.data) for image in state.images) > owner.limits.max_total_bytes
        ):
            raise DefinitionError(
                "Restored image state exceeds the current retention limits.", code="image_reuse_limits"
            )
        owner._state = state
        ctx.deps._record_run_capability(IMAGE_REUSE_CAPABILITY_ID, owner)
        owner._checkpoint()
        return owner

    def _checkpoint(self) -> None:
        assert self._context is not None and self._state is not None
        state = self._state
        self._context.state._defer(IMAGE_REUSE_CAPABILITY_ID, lambda: state, version="1")

    def _capture(self, content: BinaryContent, source: dict[str, Any]) -> _Image | None:
        assert self._state is not None
        existing = next(
            (image for image in self._state.images if image.source == source and image.run_id == self._state.run_id),
            None,
        )
        if existing is not None:
            return existing if existing.content.data == content.data else None
        if (
            len(self._state.images) >= self.limits.max_images
            or len(content.data) > self.limits.max_image_bytes
            or sum(len(image.content.data) for image in self._state.images) + len(content.data)
            > self.limits.max_total_bytes
        ):
            self._state.omitted = True
            self._checkpoint()
            return None
        image = _Image(
            reference=f"{_PREFIX}{uuid4().hex}",
            run_id=self._state.run_id,
            source=source,
            content=copy(content),
        )
        self._state.images.append(image)
        self._checkpoint()
        return image

    async def before_model_request(
        self, ctx: RunContext[AgentContext], request_context: ModelRequestContext
    ) -> ModelRequestContext:
        if ctx.deps._compacting:
            return request_context
        self._bindings.clear()
        # Native approval dispatch precedes the next model decision. Unconsumed
        # exceptions must never authorize a later model-generated call ID.
        self._resume_calls.clear()
        # Admit current/newest canonical prompts before older history. Retained
        # same-Run and approved-pending images stay in place; never evict issued refs.
        for message_index in reversed(range(len(ctx.messages))):
            message = ctx.messages[message_index]
            if not isinstance(message, ModelRequest):
                continue
            for part_index in reversed(range(len(message.parts))):
                part = message.parts[part_index]
                if not isinstance(part, UserPromptPart):
                    continue
                items = prompt_content(message, part_index)
                changed = False
                for index, item in enumerate(items):
                    value = item.value
                    if not isinstance(value, BinaryContent) or not value.is_image:
                        continue
                    # Tool-result images are lifted by Pydantic AI into user content.
                    marker = (value.vendor_metadata or {}).get(_SOURCE)
                    if isinstance(marker, dict) and isinstance(marker.get("source"), dict):
                        self._capture(value, marker["source"])
                        continue
                    extra = item.metadata.model_extra or {}
                    source = extra.get(_SOURCE)
                    if not isinstance(source, dict):
                        source = {
                            "kind": "input",
                            "id": f"image-part-{uuid4().hex}",
                            "input_timestamp": part.timestamp.isoformat(),
                            "part_index": part_index,
                            "content_path": [index],
                        }
                        items[index] = ContentItem(value, item.metadata.model_copy(update={_SOURCE: source}))
                        changed = True
                    self._capture(value, source)
                if changed:
                    annotated = annotate_prompt(message, part_index, items)
                    message.metadata = annotated.metadata
                    # Annotation preserves all native values and only adds provenance.
                    if (
                        message_index < len(request_context.messages)
                        and request_context.messages[message_index] is message
                    ):
                        request_context.messages[message_index] = message
        return request_context

    def get_instructions(self) -> str:
        return (
            "Use list_images to find retained native input/tool images. To pass an image to a tool's base64 "
            "string parameter, copy its exact base64_reference as the whole parameter value. The Host encodes "
            "the original bytes before the actual target's schema and policy checks. Never transcribe base64, "
            "guess a reference from a short file identifier, or embed a reference in other text. References "
            "are Run-scoped; unavailable URL/provider-only images have no reference."
        )

    def get_toolset(self) -> FunctionToolset[AgentContext]:
        async def list_images() -> dict[str, Any]:
            """List byte-backed images reusable in this Run, with exact provenance and base64 references."""
            assert self._state is not None
            return {
                "images": [image.descriptor() for image in self._state.images if image.run_id == self._state.run_id],
                "omitted": self._state.omitted,
            }

        return FunctionToolset([list_images], id="a13n-image-reuse-tools")

    async def wrap_model_context(
        self, ctx: RunContext[AgentContext], request: ModelContextProjectionRequest, handler: ModelContextNext
    ) -> ModelContextProjection:
        projection = await handler(request)
        assert self._state is not None
        images = [image.descriptor() for image in self._state.images if image.run_id == self._state.run_id]
        if not images and not self._state.omitted:
            return projection
        block = ModelContextBlock(
            source_id=IMAGE_REUSE_CAPABILITY_ID,
            placement=ModelContextPlacement.REQUEST_EPILOGUE,
            content="<image-references>\n"
            + json.dumps({"images": images, "omitted": self._state.omitted})
            + "\n</image-references>",
        )
        return ModelContextProjection(
            blocks=(*projection.blocks, *((block,) if not request.history.matches_latest(block) else ()))
        )

    async def before_tool_validate(
        self, ctx: RunContext[AgentContext], *, call: ToolCallPart, tool_def: ToolDefinition, args: str | dict[str, Any]
    ) -> str | dict[str, Any]:
        # Resolve at the native target, never in forwarding/control envelopes.
        metadata = tool_def.metadata or {}
        if FORWARDED_TOOL_KEY in metadata or metadata.get("a13n.codeact.runner") is True or tool_def.kind == "external":
            return args
        if isinstance(args, str):
            try:
                raw = json.loads(args)
            except ValueError:
                return args  # Native validation owns malformed JSON.
        else:
            raw = args
        references = _references(raw)
        if not references:
            return args
        assert self._state is not None
        resume_name, resume_references = self._resume_calls.get(call.tool_call_id, ("", set()))
        allowed = {
            image.reference: image
            for image in self._state.images
            if image.run_id == self._state.run_id
            or (resume_name == call.tool_name and image.reference in resume_references)
        }
        if references - allowed.keys():
            raise ModelRetry(
                "Image reference is unavailable in this Run. Use list_images; do not guess short file IDs."
            )
        bound = {reference: allowed[reference] for reference in references}
        self._resume_calls.pop(call.tool_call_id, None)
        paths: dict[tuple[str | int, ...], _Image] = {}
        self._bindings[call.tool_call_id] = paths

        def resolve(value: Any, path: tuple[str | int, ...]) -> Any:
            if isinstance(value, str) and value in bound:
                image = bound[value]
                paths[path] = image
                return image.content.base64
            if isinstance(value, dict):
                return {key: resolve(item, (*path, key)) for key, item in value.items()}
            if isinstance(value, list):
                return [resolve(item, (*path, index)) for index, item in enumerate(value)]
            return value

        return resolve(raw, ())

    async def on_tool_validate_error(
        self,
        ctx: RunContext[AgentContext],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: str | dict[str, Any],
        error: ValidationError | ModelRetry,
    ) -> dict[str, Any]:
        if call.tool_call_id in self._bindings:
            # Neither native ValidationError.input nor a custom validator's message
            # may echo Host-generated base64 into retries or instrumentation.
            raise ModelRetry(
                "Image-bound arguments failed the target tool's validation. Check its schema and parameters."
            ) from None
        raise error

    async def after_tool_execute(
        self,
        ctx: RunContext[AgentContext],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: dict[str, Any],
        result: Any,
    ) -> Any:
        metadata = tool_def.metadata or {}
        if FORWARDED_TOOL_KEY in metadata or metadata.get("a13n.codeact.runner") is True:
            return result

        def capture(value: Any, path: list[str | int]) -> Any:
            if isinstance(value, BinaryContent) and value.is_image:
                marker = (value.vendor_metadata or {}).get(_SOURCE)
                source = {"kind": "tool", "tool_call_id": call.tool_call_id, "content_path": path}
                if self._state is not None and any(
                    isinstance(marker, dict)
                    and image.reference == marker.get("reference")
                    and image.source == source
                    and image.content.data == value.data
                    for image in self._state.images
                ):
                    return value
                image = self._capture(value, source)
                if image is not None:
                    updated = copy(value)
                    updated.vendor_metadata = {
                        **(value.vendor_metadata or {}),
                        _SOURCE: {"reference": image.reference, "source": image.source},
                    }
                    return updated
            elif isinstance(value, list | tuple):
                return [capture(item, [*path, index]) for index, item in enumerate(value)]
            return value

        if isinstance(result, ToolReturn):
            updated = copy(result)
            updated.return_value = capture(result.return_value, ["return_value"])
            updated.content = capture(result.content, ["content"])
            return updated
        return capture(result, [])


def image_argument_projection(ctx: RunContext[AgentContext], arguments: Any) -> Any:
    """Payload-free policy/review identity, bound to the effective base64 value's digest."""
    owner = ctx.deps._run_capability(IMAGE_REUSE_CAPABILITY_ID)
    if not isinstance(owner, ImageReuseCapability):
        return arguments
    bound = owner._bindings.get(ctx.tool_call_id or "", {})
    if not bound:
        return arguments
    payloads: dict[str, dict[str, _Image]] = {}
    for image in bound.values():
        payloads.setdefault(image.content.base64, {})[image.reference] = image

    def project(value: Any, path: tuple[str | int, ...]) -> Any:
        if isinstance(value, str) and value in payloads:
            candidates = payloads[value]
            image = bound.get(path)
            if image is not None and image.reference in candidates:
                return {"image_binding": image.descriptor(), "encoding": "base64"}
            # Native serializers can rename fields (for example Pydantic aliases).
            # Preserve every possible source instead of guessing from equal bytes.
            if len(candidates) == 1:
                return {"image_binding": next(iter(candidates.values())).descriptor(), "encoding": "base64"}
            return {"image_bindings": [image.descriptor() for image in candidates.values()], "encoding": "base64"}
        if isinstance(value, dict):
            return {key: project(item, (*path, key)) for key, item in value.items()}
        if isinstance(value, list):
            return [project(item, (*path, index)) for index, item in enumerate(value)]
        return value

    return project(project_json(arguments), ())


def image_result_projection(ctx: RunContext[AgentContext], result: Any) -> Any:
    """Expose the same image descriptors to CodeAct before its finite-JSON boundary."""
    owner = ctx.deps._run_capability(IMAGE_REUSE_CAPABILITY_ID)
    if not isinstance(owner, ImageReuseCapability) or owner._state is None:
        return result
    images = {image.reference: image for image in owner._state.images}
    supplemental: list[BinaryContent] = []

    def project(value: Any) -> Any:
        if isinstance(value, BinaryContent) and value.is_image:
            marker = (value.vendor_metadata or {}).get(_SOURCE)
            reference = marker.get("reference") if isinstance(marker, dict) else None
            image = images.get(reference) if isinstance(reference, str) else None
            supplemental.append(value)
            if image is None:
                return {"image_unavailable": "Image reuse retention limit exceeded."}
            return image.descriptor()
        if isinstance(value, list | tuple):
            return [project(item) for item in value]
        return value

    if isinstance(result, ToolReturn):
        value = project(result.return_value)
        content = result.content
        content_items = [content] if isinstance(content, str) else list(content or ())
        descriptors = [project(item) for item in content_items if isinstance(item, BinaryContent) and item.is_image]
        if not supplemental and not descriptors:
            return result
        return ToolReturn(
            return_value={"return_value": value, "images": descriptors} if descriptors else value,
            content=[*content_items, *(item for item in supplemental if item not in content_items)],
            metadata=result.metadata,
        )
    value = project(result)
    return ToolReturn(return_value=value, content=supplemental) if supplemental else value


__all__ = ["ImageReuseCapability", "ImageReuseLimits"]

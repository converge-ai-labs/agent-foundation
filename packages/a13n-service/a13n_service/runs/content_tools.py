"""Versioned content handles for execution tools.

Handles are opaque navigation state, never capabilities. Every resolution repeats
its source's original workspace, resource and analysis-origin authorization.
"""

import base64
import json
from typing import Annotated, Literal

from a13n_stream_protocol.display import Item
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from a13n_service.infra import reads
from a13n_service.infra.crypto import Envelope, SecretLocation
from a13n_service.infra.errors import ServiceError, invalid
from a13n_service.platform_tools import ToolContext as PlatformContext
from a13n_service.platform_tools import definition
from a13n_service.resources.agents import read_views as agent_reads
from a13n_service.runs import entries, item_reads, runs, traces
from a13n_service.runs.findings import analysis
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.schemas import context_response
from a13n_service.runs.tool_executions import views as execution_views

type ToolContext = PlatformContext[Runtime]

RunSection = Literal[
    "input",
    "output",
    "failure",
    "resume",
    "pending",
    "options",
    "environment_mounts",
    "memory_mounts",
    "usage_at_seal",
    "labels",
]
SpanSection = Literal["all", "input", "output", "attributes", "resource_attributes", "events", "links", "usage"]
Handle = Annotated[
    str,
    Field(
        min_length=1,
        max_length=2048,
        description="Opaque content or continuation reference returned by an execution tool. Pass unchanged.",
    ),
]


class _Source(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RunSource(_Source):
    kind: Literal["run"] = "run"
    run_id: str
    section: RunSection


class InboxSource(_Source):
    kind: Literal["inbox"] = "inbox"
    thread_id: str
    entry_id: str
    section: Literal["payload", "options", "failure"]


class ConfigurationSource(_Source):
    kind: Literal["configuration"] = "configuration"
    run_id: str
    section: Literal["original", "effective", "overrides"]
    part: agent_reads.ConfigSection = "all"


class ItemSource(_Source):
    kind: Literal["item"] = "item"
    run_id: str
    item_id: str
    ordinal: int = Field(ge=1)


class SpanSource(_Source):
    kind: Literal["span"] = "span"
    trace_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    span_id: str = Field(pattern=r"^[0-9a-f]{16}$")
    section: SpanSection = "all"


class ExecutionSource(_Source):
    kind: Literal["execution"] = "execution"
    thread_id: str
    execution_id: str


Source = Annotated[
    RunSource | InboxSource | ConfigurationSource | ItemSource | SpanSource | ExecutionSource,
    Field(discriminator="kind"),
]
SOURCE = TypeAdapter(Source)


class ContentView[T](BaseModel):
    summary: T
    content: dict[str, str]


class ContentRead(BaseModel):
    reference: str
    fragment: reads.TextRead
    continuation: str | None


def _location(ctx: ToolContext) -> SecretLocation:
    # Navigation issued by one Run cannot be repurposed to reach sources another
    # Run has not selected. This is integrity protection, not source authority.
    return SecretLocation(None, "content_tools", "reference_v1", f"{ctx.workspace_id}/{ctx.origin or ctx.actor.id}")


def encode_reference(ctx: ToolContext, source: Source, version: str | None, offset: int = 0) -> str:
    value = [source.model_dump(mode="json"), version, offset]
    protected = ctx.runtime.keys.protect(json.dumps(value, separators=(",", ":")).encode(), _location(ctx))
    return base64.urlsafe_b64encode(protected.model_dump_json().encode()).decode()


def value_reference(ctx: ToolContext, source: Source, value: object) -> str:
    version = reads.digest(value if isinstance(value, str) else reads.json_text(value))
    return encode_reference(ctx, source, version)


def unpack(ctx: ToolContext, ref: str) -> tuple[Source, str | None, int]:
    try:
        if len(ref) > 2048:
            raise ValueError("length")
        protected = Envelope.model_validate_json(base64.b64decode(ref, altchars=b"-_", validate=True))
        values = json.loads(ctx.runtime.keys.reveal(protected, _location(ctx)))
        if not isinstance(values, list) or len(values) != 3:
            raise ValueError("shape")
        source = SOURCE.validate_python(values[0])
        version, offset = values[1:]
        if (
            not (
                (isinstance(version, str) and len(version) == 64)
                or (version is None and isinstance(source, ItemSource) and offset == 0)
            )
            or not isinstance(offset, int)
            or isinstance(offset, bool)
            or offset < 0
        ):
            raise ValueError("position")
        return source, version, offset
    except (ValidationError, ValueError, ServiceError):
        raise invalid("reference", "Invalid execution content reference for this Run and workspace") from None


async def item_value(ctx: ToolContext, run_id: str, item: Item) -> dict[str, object]:
    """Resolve retained values for an already authorized display item before projecting it.

    Callers navigate the logical item; they never need to assemble storage IDs,
    ordinal tuples or partial JSON values. Stored truncation remains explicit.
    """
    value = item.model_dump(mode="python", exclude={"content_refs"})
    fields = dict(item.content)
    retained = {}
    truncated = []
    for field, ref in item.content_refs.items():
        if ref.id not in retained:
            retained[ref.id] = await runs.content(ctx.runtime, ctx.actor, ctx.workspace_id, run_id, ref.id)
        saved = retained[ref.id]
        fields[field] = saved.value
        if ref.truncated or saved.truncated:
            truncated.append(field)
    value["content"] = fields
    value["source_truncated_fields"] = truncated
    return value


async def _read(
    ctx: ToolContext, source: Source, *, version: str | None, offset: int, limit: int, query: str | None
) -> reads.TextRead:
    runtime, actor, workspace_id = ctx.runtime, ctx.actor, ctx.workspace_id
    if isinstance(source, ItemSource):
        item = await item_reads.get_item(runtime, actor, workspace_id, source.run_id, source.item_id, source.ordinal)
        value = await item_value(ctx, source.run_id, item)
        return reads.read_value(
            value,
            source=f"runs/{source.run_id}/items/{source.item_id}",
            offset=offset,
            limit=limit,
            version=version,
            query=query,
            source_truncated=bool(value["source_truncated_fields"]) or item.content.get("truncated") is True,
        )
    if isinstance(source, ExecutionSource):
        return await execution_views.result(
            runtime,
            actor,
            workspace_id,
            source.thread_id,
            source.execution_id,
            offset=offset,
            limit=limit,
            version=version,
            query=query,
        )
    truncated = False
    if isinstance(source, RunSource):
        run = context_response(await runs.get(runtime.storage, actor, workspace_id, source.run_id))
        value = getattr(run, source.section)
    elif isinstance(source, InboxSource):
        entry = context_response(
            await entries.get_entry(runtime.storage, actor, workspace_id, source.thread_id, source.entry_id)
        )
        value = getattr(entry, source.section)
    elif isinstance(source, ConfigurationSource):
        configuration = await runs.agent(runtime.storage, actor, workspace_id, source.run_id)
        if source.section == "overrides":
            if source.part != "all":
                raise invalid("reference", "Overrides have no configuration section")
            run = await runs.get(runtime.storage, actor, workspace_id, source.run_id)
            value = run.options.overrides
        else:
            value = agent_reads.config_section(
                configuration.original_config if source.section == "original" else configuration.config,
                source.part,
            )
    else:
        await analysis.read_scope(runtime, actor, workspace_id, ctx.origin, source.trace_id)
        span = await traces.get_trace_span(
            runtime.storage, runtime.traces, actor, workspace_id, source.trace_id, source.span_id
        )
        await analysis.record_read(runtime, actor, workspace_id, ctx.origin, source.trace_id)
        value = span if source.section == "all" else getattr(span, source.section)
    return reads.read_value(
        value,
        source=reads.json_text(source),
        offset=offset,
        limit=limit,
        version=version,
        query=query,
        source_truncated=truncated,
    )


async def read_content(ctx: ToolContext, *, reference: Handle, limit: reads.TextLimit = 4000) -> ContentRead:
    """Read bounded content from a parent result or continue it without reconstructing source paths or offsets.

    Item discovery references bind their content version on the first read. Returned references and continuations
    retain that version; content changes then fail explicitly. Current source access is checked on every call.
    """
    source, version, offset = unpack(ctx, reference)
    result = await _read(ctx, source, version=version, offset=offset, limit=limit, query=None)
    return ContentRead(
        reference=encode_reference(ctx, source, result.version),
        fragment=result,
        continuation=encode_reference(ctx, source, result.version, result.next_offset)
        if result.next_offset is not None
        else None,
    )


async def search_content(
    ctx: ToolContext,
    *,
    reference: Handle,
    query: Annotated[str, Field(min_length=1, max_length=256)],
    limit: reads.TextLimit = 4000,
) -> ContentRead:
    """Find the next literal match in bounded content, starting at the reference's position.

    Item discovery references bind their version on this first read. Reuse the returned reference or continuation
    to keep that version; changed content fails explicitly. Current source access is checked on every call.
    """
    source, version, offset = unpack(ctx, reference)
    result = await _read(ctx, source, version=version, offset=offset, limit=limit, query=query)
    return ContentRead(
        reference=encode_reference(ctx, source, result.version),
        fragment=result,
        continuation=encode_reference(ctx, source, result.version, result.next_offset)
        if result.next_offset is not None
        else None,
    )


TOOLS = (
    definition(read_content, group="execution", effect="read"),
    definition(search_content, group="execution", effect="read"),
)

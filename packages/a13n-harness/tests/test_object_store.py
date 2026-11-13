from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Annotated
from uuid import uuid4

import pytest
from a13n_harness import AgentSpec, HarnessBuilder, RunBindings
from a13n_harness.capabilities import ObjectRefToolset, ObjectStoreCapability
from a13n_harness.objects import SUPPORTS_OBJECT_REFS, ObjectInfo, object_not_found
from a13n_harness.tools import ToolPermissions, ToolPermissionsCapability
from pydantic import Field
from pydantic_ai.capabilities import Capability
from pydantic_ai.messages import BinaryContent, ModelRequest, ToolReturnPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from pydantic_ai.tools import Tool
from pydantic_ai.toolsets import FunctionToolset

pytestmark = pytest.mark.anyio
PAYLOAD = b"\x00original bytes, not model generated\xff"


@dataclass
class Store:
    entries: dict[str, tuple[ObjectInfo, bytes]] = field(default_factory=dict)
    reads: int = 0

    async def put(self, data, *, media_type, name=None):
        info = ObjectInfo(
            object_id=f"obj_{uuid4().hex}",
            media_type=media_type,
            size=len(data),
            digest=sha256(data).hexdigest(),
            name=name,
        )
        self.entries[info.object_id] = info, data
        return info

    async def info(self, object_id):
        if object_id not in self.entries:
            raise object_not_found()
        return self.entries[object_id][0]

    async def read(self, object_id):
        await self.info(object_id)
        self.reads += 1
        return self.entries[object_id][1]

    async def store(self, object_id):
        info = (await self.info(object_id)).model_copy(update={"retention": "stored"})
        self.entries[object_id] = info, self.entries[object_id][1]
        return info

    async def list(self, *, offset=0, limit=50):
        return [item[0] for item in list(self.entries.values())[offset : offset + limit]]

    async def delete(self, object_id):
        await self.info(object_id)
        del self.entries[object_id]


def model_call(name, arguments, inspect=None):
    async def stream(messages, info):
        if inspect:
            inspect(messages, info)
        if any(
            isinstance(part, ToolReturnPart)
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
        ):
            yield "done"
        else:
            yield {0: DeltaToolCall(name=name, json_args=json.dumps(arguments), tool_call_id="call-object")}

    return FunctionModel(stream_function=stream)


async def test_opted_in_strict_schema_and_original_validator_receive_exact_bytes():
    store = Store()
    item = await store.put(PAYLOAD, media_type="image/png")
    seen, validated, schemas = [], [], []

    def validate(ctx, data_base64):
        validated.append(data_base64)

    def send(data_base64: Annotated[str, Field(pattern=r"^[A-Za-z0-9+/]+=*$")]) -> str:
        seen.append(base64.b64decode(data_base64))
        return "sent"

    def inspect(messages, info):
        schemas.extend(tool.parameters_json_schema for tool in info.function_tools if tool.name == "send")

    executable = HarnessBuilder().build(
        AgentSpec(),
        model=model_call("send", {"data_base64": item.ref}, inspect),
        output_type=str,
        capabilities=(
            ObjectStoreCapability(),
            Capability(
                toolsets=[
                    ObjectRefToolset(FunctionToolset([Tool(send, args_validator=validate, strict=True)], id="target"))
                ]
            ),
        ),
    )
    result = await executable.run("send it", bindings=RunBindings.embedded(object_store=store))
    assert result.status == "completed", result
    assert seen == [PAYLOAD]
    assert validated == [item.ref]
    assert store.reads == 1
    assert "a13n-object" in json.dumps(schemas)
    calls = [part for message in result.all_messages() for part in message.parts if part.part_kind == "tool-call"]
    assert calls[0].args_as_dict() == {"data_base64": item.ref}
    assert base64.b64encode(PAYLOAD).decode() not in json.dumps([part.args for part in calls])


@pytest.mark.parametrize(
    "enabled,opted,expected",
    [
        (False, True, "object_refs_disabled"),
        (True, False, "object_refs_not_supported"),
        (True, True, "object_ref_not_found"),
    ],
)
async def test_missing_and_disabled_refs_never_reach_consumer(enabled, opted, expected):
    store, seen = Store(), []

    def send(data_base64: str):
        seen.append(data_base64)

    executable = HarnessBuilder().build(
        AgentSpec(),
        model=model_call("send", {"data_base64": "a13n-object://obj_missing"}),
        output_type=str,
        capabilities=(
            *([ObjectStoreCapability()] if enabled else []),
            Capability(tools=[Tool(send, metadata={SUPPORTS_OBJECT_REFS: opted})]),
        ),
    )
    result = await executable.run("send", bindings=RunBindings.embedded(object_store=store if enabled else None))
    assert result.status == "completed", result
    assert not seen
    assert expected in str(result.all_messages())


async def test_permission_denial_precedes_store_read():
    store = Store()
    item = await store.put(PAYLOAD, media_type="image/png")

    def send(data_base64: str):
        pytest.fail("denied tool executed")

    executable = HarnessBuilder().build(
        AgentSpec(),
        model=model_call("send", {"data_base64": item.ref}),
        output_type=str,
        capabilities=(
            ObjectStoreCapability(),
            Capability(tools=[Tool(send, metadata={SUPPORTS_OBJECT_REFS: True})]),
            ToolPermissionsCapability(ToolPermissions(default="deny")),
        ),
    )
    result = await executable.run("send", bindings=RunBindings.embedded(object_store=store))
    assert result.status == "completed"
    assert store.reads == 0


async def test_native_input_capture_preserves_vision_and_ref_promotion():
    store, observed = Store(), []

    async def stream(messages, info):
        observed.extend(
            part.content
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if part.part_kind == "user-prompt"
        )
        yield "seen"

    executable = HarnessBuilder().build(
        AgentSpec(),
        model=FunctionModel(stream_function=stream),
        output_type=str,
        capabilities=(ObjectStoreCapability(),),
    )
    from io import BytesIO

    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (8, 8), "red").save(buffer, format="PNG")
    image = buffer.getvalue()
    result = await executable.run(
        [BinaryContent(data=image, media_type="image/png")], bindings=RunBindings.embedded(object_store=store)
    )
    assert result.status == "completed", result
    assert len(store.entries) == 1
    assert any(isinstance(value, BinaryContent) for content in observed for value in content), observed
    assert "a13n-object://" in str(observed)
    info = (await store.list())[0]
    assert (await store.store(info.object_id)).object_id == info.object_id


@pytest.mark.parametrize("dispatch", ["direct", "proxy", "codeact"])
async def test_nested_reference_target_dispatch_expands_once_and_keeps_source(dispatch):
    from a13n_harness.capabilities import (
        CodeActCapability,
        ProxyToolSearchConfig,
        ToolSearchCapability,
        ToolSearchGroup,
    )
    from a13n_harness.toolsets import CodeActPolicyToolset, CodeActToolPolicy
    from pydantic import BaseModel

    class Payload(BaseModel):
        values: list[Annotated[str, Field(pattern=r"^[A-Za-z0-9+/]+=*$")]]

    seen = []

    def send(payload: Payload, literal: str):
        seen.append((payload.values, literal))
        return "sent"

    store = Store()
    item = await store.put(PAYLOAD, media_type="image/png")
    args = {"payload": {"values": [item.ref, item.ref]}, "literal": "prefix " + item.ref}
    send.__annotations__["payload"] = Payload
    source = ObjectRefToolset(FunctionToolset([send], id="nested"))
    capabilities = [ObjectStoreCapability()]
    if dispatch == "proxy":
        capabilities.append(
            ToolSearchCapability(
                groups={"media": ToolSearchGroup(source, "Media")}, config=ProxyToolSearchConfig(call_name="invoke")
            )
        )
        name, arguments = "invoke", {"group": "media", "tool": "send", "arguments": args}
    elif dispatch == "codeact":
        capabilities.extend(
            [Capability(toolsets=[CodeActPolicyToolset(source, CodeActToolPolicy(default=True))]), CodeActCapability()]
        )
        name, arguments = "run_code", {"code": f"await send(payload={args['payload']!r}, literal={args['literal']!r})"}
    else:
        capabilities.append(Capability(toolsets=[source]))
        name, arguments = "send", args
    executable = HarnessBuilder().build(
        AgentSpec(), output_type=str, model=model_call(name, arguments), capabilities=capabilities
    )
    result = await executable.run("send", bindings=RunBindings.embedded(object_store=store))
    assert result.output_or_raise() == "done"
    encoded = base64.b64encode(PAYLOAD).decode()
    assert seen == [([encoded, encoded], "prefix " + item.ref)], result.all_messages()
    assert store.reads == 1
    calls = [part for message in result.all_messages() for part in message.parts if part.part_kind == "tool-call"]
    assert calls[0].args_as_dict() == arguments
    assert encoded not in str(calls)


@pytest.mark.parametrize("reject", [False, True])
async def test_consumer_owns_expanded_payload_validation(reject):
    from pydantic_ai import ToolFailed

    store = Store()
    item = await store.put(PAYLOAD, media_type="image/png")

    validated, seen = [], []

    def validator(ctx, data_base64):
        assert store.reads == 0
        validated.append(data_base64)

    def send(data_base64: Annotated[str, Field(max_length=5)]):
        assert base64.b64decode(data_base64) == PAYLOAD
        if reject:
            raise ToolFailed("Consumer rejected payload")
        seen.append(data_base64)
        return "sent"

    tool = Tool(send, metadata={SUPPORTS_OBJECT_REFS: True}, args_validator=validator)
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=model_call("send", {"data_base64": item.ref}),
        capabilities=[ObjectStoreCapability(), Capability(tools=[tool])],
    )
    result = await executable.run("send", bindings=RunBindings.embedded(object_store=store))
    assert result.output_or_raise() == "done"
    assert validated == [item.ref]
    assert seen == ([] if reject else [base64.b64encode(PAYLOAD).decode()])
    assert store.reads == 1
    assert ("Consumer rejected payload" in str(result.all_messages())) == reject
    assert base64.b64encode(PAYLOAD).decode() not in str(result.all_messages())


@pytest.mark.parametrize("expired", [False, True])
@pytest.mark.parametrize("proxy", [False, True])
async def test_approval_persists_refs_and_resolves_only_on_resumed_target(proxy, expired):
    from a13n_harness import DeferredToolResume

    from .test_forwarded_deferred import _build

    store, seen = Store(), []
    item = await store.put(PAYLOAD, media_type="image/png")

    def send(data_base64: str):
        seen.append(base64.b64decode(data_base64))
        return "sent"

    args = {"data_base64": item.ref}
    calls = [("invoke", {"group": "tools", "tool": "send", "arguments": args})] if proxy else [("send", args)]

    def build():
        return _build(
            ObjectRefToolset(FunctionToolset([Tool(send, requires_approval=True)], id="approval")),
            proxy=proxy,
            calls=calls,
            extra=(ObjectStoreCapability(),),
        )

    first = await build().run("send", bindings=RunBindings.embedded(object_store=store))
    assert first.status == "suspended"
    assert store.reads == 0 and seen == []
    requests = first.deferred
    assert requests is not None
    approval = requests.approvals[0]
    assert approval.target_call.args_as_dict() == args
    if expired:
        await store.delete(item.object_id)
    resumed = await build().run(
        previous_state=first.state,
        bindings=RunBindings.embedded(object_store=store),
        deferred_resume=DeferredToolResume(
            requests, requests.build_results(approvals={approval.target_call.tool_call_id: True})
        ),
    )
    assert resumed.output_or_raise() == "done"
    assert seen == ([] if expired else [PAYLOAD])
    assert store.reads == (0 if expired else 1)
    if expired:
        assert "object_ref_not_found" in str(resumed.all_messages())


async def test_tool_media_capture_keeps_native_content_and_exact_original_bytes():
    from pydantic_ai import ToolReturn

    store, observed = Store(), []
    media = BinaryContent(data=PAYLOAD, media_type="audio/wav")

    def producer():
        return ToolReturn(return_value={"created": True}, content=[media])

    async def stream(messages, info):
        observed[:] = messages
        if not any(isinstance(part, ToolReturnPart) for message in messages for part in message.parts):
            yield {0: DeltaToolCall(name="producer", json_args="{}", tool_call_id="produce")}
        else:
            yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        model=FunctionModel(stream_function=stream),
        output_type=str,
        capabilities=[ObjectStoreCapability(), Capability(tools=[producer])],
    )
    result = await executable.run("make", bindings=RunBindings.embedded(object_store=store))
    assert result.output_or_raise() == "done"
    info = (await store.list())[0]
    assert await store.read(info.object_id) == PAYLOAD
    assert info.media_type == "audio/wav" and info.ref in str(observed)
    contents = [part.content for message in observed for part in message.parts if isinstance(part, ToolReturnPart)]
    assert any(isinstance(content, list) and {"created": True} in content for content in contents)
    assert any(
        isinstance(value, BinaryContent) and value.data == PAYLOAD
        for content in contents
        if isinstance(content, list)
        for value in content
    )


async def test_opt_in_preserves_native_json_mode_without_refs():
    from datetime import datetime

    seen = []

    def schedule(when: Annotated[datetime, Field(strict=True)]):
        seen.append(when)
        return "scheduled"

    schedule.__annotations__["when"] = Annotated[datetime, Field(strict=True)]
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=model_call("schedule", {"when": "2026-11-13T12:00:00"}),
        capabilities=[ObjectStoreCapability(), Capability(toolsets=[ObjectRefToolset(FunctionToolset([schedule]))])],
    )
    result = await executable.run("schedule", bindings=RunBindings.embedded(object_store=Store()))
    assert result.output_or_raise() == "done"
    assert seen == [datetime(2026, 11, 13, 12)]


async def test_ref_validation_preserves_defaults_coercion_and_managed_resolver_contract():
    from a13n_harness.tools import HarnessTool, HarnessToolMetadata, ToolOutputPolicy
    from pydantic import AfterValidator

    store, seen, resolved, validated = Store(), [], [], []
    item = await store.put(PAYLOAD, media_type="image/png")

    def validate_payload(value):
        assert store.reads == 0
        validated.append(value)
        return value

    async def resources(arguments, *, context):
        assert store.reads == 0
        resolved.append(dict(arguments))
        return ()

    def send(data_base64, destination="default-recipient", copies=1):
        seen.append((base64.b64decode(data_base64), destination, copies))
        return "sent"

    send.__annotations__.update(
        data_base64=Annotated[str, Field(pattern=r"^[A-Za-z0-9+/]+=*$"), AfterValidator(validate_payload)],
        destination=str,
        copies=int,
    )
    tool = HarnessTool(
        send,
        harness_metadata=HarnessToolMetadata(
            tool_id="test.media-send",
            effects=frozenset({"read"}),
            credential_audiences=(),
            idempotency="none",
            output_policy=ToolOutputPolicy(max_inline_bytes=1024, max_output_bytes=4096),
            resource_resolver=resources,
        ),
        metadata={SUPPORTS_OBJECT_REFS: True},
    )
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=model_call("send", {"data_base64": item.ref, "copies": "2"}),
        capabilities=[ObjectStoreCapability(), Capability(tools=[tool])],
    )
    result = await executable.run("send", bindings=RunBindings.embedded(object_store=store))
    assert result.output_or_raise() == "done"
    assert seen == [(PAYLOAD, "default-recipient", 2)], result.all_messages()
    assert resolved == [{"data_base64": item.ref, "destination": "default-recipient", "copies": 2}]
    assert validated == [item.ref]
    assert store.reads == 1


@pytest.mark.parametrize("mixed", [False, True])
async def test_capture_keeps_plain_media_list_flat_for_native_provider_rendering(mixed):
    from io import BytesIO

    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (8, 8), "red").save(buffer, format="PNG")
    image = BinaryContent(data=buffer.getvalue(), media_type="image/png")
    store = Store()

    def producer():
        return ["Screenshot", image] if mixed else [image]

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=model_call("producer", {}),
        capabilities=[ObjectStoreCapability(), Capability(tools=[producer])],
    )
    result = await executable.run("show", bindings=RunBindings.embedded(object_store=store))
    assert result.output_or_raise() == "done"
    part = next(part for message in result.all_messages() for part in message.parts if isinstance(part, ToolReturnPart))
    assert len(part.files) == 1
    text, native = part.model_response_str_and_user_content()
    assert any(isinstance(value, BinaryContent) and value.data == image.data for value in native)
    assert "Object Store" in text


async def test_capture_reimports_current_media_after_cached_object_was_deleted():
    from a13n_harness.capabilities.object_store import ObjectStoreCapability

    store = Store()
    capability = ObjectStoreCapability()
    content = BinaryContent(data=PAYLOAD, media_type="image/png")
    first = json.loads((await capability._capture(store, content))[0])
    await store.delete(first["object_id"])
    second = json.loads((await capability._capture(store, content))[0])
    assert first["ref"] != second["ref"]
    assert await store.read(second["object_id"]) == PAYLOAD


@pytest.mark.parametrize("single", [False, True])
async def test_reference_validation_preserves_nested_models_and_json_strict_fields(single):
    from datetime import datetime

    from pydantic import BaseModel, ConfigDict

    class Payload(BaseModel):
        model_config = ConfigDict(extra="forbid")
        payload: dict[str, Annotated[str, Field(pattern=r"^[A-Za-z0-9+/]+=*$")]]
        # Native single-model argument wrapping validates its model in Python
        # mode even for JSON input; preserve that upstream behavior.
        when: Annotated[datetime, Field(strict=not single)]
        count: int = 3

    seen = []

    def send(payload, label="sent"):
        seen.append((payload, label))
        return "sent"

    def send_single(payload):
        seen.append((payload, "sent"))
        return "sent"

    target = send_single if single else send
    target.__annotations__["payload"] = Payload
    store = Store()
    item = await store.put(PAYLOAD, media_type="application/octet-stream")
    payload = {"payload": {"data": item.ref}, "when": "2026-11-13T12:00:00"}
    arguments = payload if single else {"payload": payload}
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=model_call(target.__name__, arguments),
        capabilities=[ObjectStoreCapability(), Capability(toolsets=[ObjectRefToolset(FunctionToolset([target]))])],
    )
    result = await executable.run("send", bindings=RunBindings.embedded(object_store=store))
    assert result.output_or_raise() == "done"
    assert len(seen) == 1, str(
        [
            part.content
            for message in result.all_messages()
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
    )
    value, label = seen[0]
    assert isinstance(value, Payload)
    assert value.payload == {"data": base64.b64encode(PAYLOAD).decode()}
    assert value.when == datetime(2026, 11, 13, 12)
    assert value.count == 3 and label == "sent"


@pytest.mark.parametrize("selection", ["file", "directory", "denied"])
async def test_file_import_uses_authorized_environment_route(tmp_path, selection):
    from a13n_harness.environment import EnvironmentAction, EnvironmentMount, EnvironmentPermissionSet
    from a13n_harness.environment.advanced import create_environment_runtime
    from a13n_harness.providers.environment.direct_local.configuration import DirectLocalEnvironmentConfiguration

    from .environment_helpers import DirectLocalSource

    (tmp_path / "image.png").write_bytes(PAYLOAD)
    source = DirectLocalSource(
        DirectLocalEnvironmentConfiguration(root={"path": str(tmp_path)}), environment_id="files"
    )
    permissions = EnvironmentPermissionSet(
        operations=frozenset()
        if selection == "denied"
        else frozenset(
            {
                EnvironmentAction.FILE_STAT,
                EnvironmentAction.FILE_READ_BYTES,
            }
        )
    )
    runtime = create_environment_runtime(
        mounts={"media": EnvironmentMount(source=source, permission_ceiling=permissions)}, default_mount="media"
    )
    store = Store()
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        capabilities=[ObjectStoreCapability()],
        model=model_call("asset_ref", {"path": "." if selection == "directory" else "image.png"}),
    )
    result = await executable.run("import", bindings=RunBindings.embedded(environment=runtime, object_store=store))
    assert result.output_or_raise() == "done", result
    if selection == "file":
        item = (await store.list())[0]
        assert await store.read(item.object_id) == PAYLOAD
        assert item.name == "image.png" and item.media_type == "image/png"
    else:
        assert not await store.list()
        returns = [
            part.content
            for message in result.all_messages()
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        assert ("invalid_argument" if selection == "directory" else "environment_denied") in str(returns), returns


async def test_host_capture_size_limit_keeps_native_media():
    from a13n_harness.objects import ObjectStoreError

    class LimitedStore(Store):
        async def put(self, data, *, media_type, name=None):
            raise ObjectStoreError("object_too_large", "Host byte limit exceeded.")

    media = BinaryContent(data=PAYLOAD, media_type="audio/wav")

    def produce():
        return [media]

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=model_call("produce", {}),
        capabilities=[ObjectStoreCapability(), Capability(tools=[produce])],
    )
    result = await executable.run("produce", bindings=RunBindings.embedded(object_store=LimitedStore()))
    assert result.output_or_raise() == "done"
    part = next(part for message in result.all_messages() for part in message.parts if isinstance(part, ToolReturnPart))
    assert part.files == [media]
    assert "object_too_large" in str(part.content)


@pytest.mark.parametrize("outcome", ["approved", "expired", "denied"])
async def test_custom_validator_approval_precedes_object_reads(outcome):
    from a13n_harness import DeferredToolResume
    from pydantic_ai.exceptions import ApprovalRequired

    store, seen, validated = Store(), [], []
    item = await store.put(PAYLOAD, media_type="image/png")

    async def validate(ctx, data_base64):
        validated.append((ctx.tool_call_approved, data_base64))
        if not ctx.tool_call_approved:
            assert store.reads == 0
            raise ApprovalRequired(metadata={"reason": "Confirm sending media"})

    def send(data_base64: str):
        seen.append(base64.b64decode(data_base64))
        return "sent"

    def build():
        return HarnessBuilder().build(
            AgentSpec(),
            output_type=str,
            model=model_call("send", {"data_base64": item.ref}),
            capabilities=[
                ObjectStoreCapability(),
                Capability(tools=[Tool(send, args_validator=validate, metadata={SUPPORTS_OBJECT_REFS: True})]),
                ToolPermissionsCapability(ToolPermissions(default="allow")),
            ],
        )

    first = await build().run("send", bindings=RunBindings.embedded(object_store=store))
    assert first.status == "suspended", first
    assert store.reads == 0 and not seen
    assert validated == [(False, item.ref)]
    requests = first.deferred
    assert requests is not None
    approval = requests.approvals[0]
    assert approval.target_call.args_as_dict() == {"data_base64": item.ref}
    if outcome == "expired":
        await store.delete(item.object_id)
    resumed = await build().run(
        previous_state=first.state,
        bindings=RunBindings.embedded(object_store=store),
        deferred_resume=DeferredToolResume(
            requests, requests.build_results(approvals={approval.target_call.tool_call_id: outcome != "denied"})
        ),
    )
    assert resumed.output_or_raise() == "done"
    assert seen == ([PAYLOAD] if outcome == "approved" else [])
    assert store.reads == (1 if outcome == "approved" else 0)
    if outcome == "approved":
        assert validated == [(False, item.ref), (True, item.ref)]
    elif outcome == "expired":
        assert "object_ref_not_found" in str(resumed.all_messages())


@pytest.mark.parametrize("single", [False, True])
@pytest.mark.parametrize("invalid", [None, "count", "payload"])
async def test_reference_callbacks_run_once_before_reads_and_preserve_typed_models(single, invalid):
    from pydantic import AfterValidator, BaseModel, BeforeValidator, WrapValidator, model_validator
    from pydantic_ai import ToolFailed

    validated, seen = [], []
    expected = b"wrong payload" if invalid == "payload" else PAYLOAD

    def check_container(values, info):
        assert store.reads == 0
        assert values == [item.ref]
        validated.append("container")
        return values

    def wrap_container(value, handler, info):
        return check_container(handler(value), info)

    class Payload(BaseModel):
        data: Annotated[
            list[str], BeforeValidator(check_container), WrapValidator(wrap_container), AfterValidator(check_container)
        ]
        count: Annotated[int, Field(ge=1)] = 2

        @model_validator(mode="before")
        @classmethod
        def check_before(cls, value, info):
            check_container(value["data"], info)
            return value

        @model_validator(mode="wrap")
        @classmethod
        def check_wrap(cls, value, handler, info):
            result = handler(value)
            check_container(result.data, info)
            return result

        @model_validator(mode="after")
        def check_after(self, info):
            check_container(self.data, info)
            return self

    def send(payload, label=1):
        assert isinstance(payload, Payload)
        if any(base64.b64decode(value, validate=True) != expected for value in payload.data):
            raise ToolFailed("Consumer rejected payload")
        seen.append((payload.data, payload.count, label))
        return "sent"

    def send_single(payload):
        return send(payload)

    target = send_single if single else send
    target.__annotations__["payload"] = Payload
    store = Store()
    item = await store.put(PAYLOAD, media_type="image/png")
    payload = {"data": [item.ref], "count": 0 if invalid == "count" else "3"}
    arguments = payload if single else {"payload": payload}
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=model_call(target.__name__, arguments),
        capabilities=[ObjectStoreCapability(), Capability(toolsets=[ObjectRefToolset(FunctionToolset([target]))])],
    )
    result = await executable.run("send", bindings=RunBindings.embedded(object_store=store))
    assert result.output_or_raise() == "done", result
    if invalid:
        assert not seen
        assert store.reads == (0 if invalid == "count" else 1)
        if invalid == "payload":
            assert "Consumer rejected payload" in str(result.all_messages())
    else:
        assert seen == [([base64.b64encode(PAYLOAD).decode()], 3, 1)]
        assert len(validated) == 6
        assert store.reads == 1


async def test_custom_validator_denial_precedes_object_reads():
    from pydantic_ai import ToolFailed

    store = Store()
    item = await store.put(PAYLOAD, media_type="image/png")

    def deny(ctx, data_base64):
        assert data_base64 == item.ref
        raise ToolFailed("Business action denied")

    def send(data_base64: str):
        pytest.fail("denied consumer executed")

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=model_call("send", {"data_base64": item.ref}),
        capabilities=[
            ObjectStoreCapability(),
            Capability(tools=[Tool(send, args_validator=deny, metadata={SUPPORTS_OBJECT_REFS: True})]),
        ],
    )
    result = await executable.run("send", bindings=RunBindings.embedded(object_store=store))
    assert result.output_or_raise() == "done"
    assert store.reads == 0
    assert "Business action denied" in str(result.all_messages())


@pytest.mark.parametrize("enabled", [False, True])
async def test_reference_prefix_in_ordinary_text_is_not_a_reference(enabled):
    store, seen = Store(), []
    text = "a13n-object://obj_example is the screenshot, not an exact reference"

    def send(description: str):
        seen.append(description)
        return "sent"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=model_call("send", {"description": text}),
        capabilities=[
            *([ObjectStoreCapability()] if enabled else []),
            Capability(toolsets=[ObjectRefToolset(FunctionToolset([send]))]),
        ],
    )
    result = await executable.run("send", bindings=RunBindings.embedded(object_store=store if enabled else None))
    assert result.output_or_raise() == "done"
    assert seen == [text]
    assert store.reads == 0


async def test_reference_expansion_preserves_prefix_prose_beside_exact_reference():
    store, seen = Store(), []
    item = await store.put(PAYLOAD, media_type="image/png")
    text = f"{item.ref} is the screenshot"

    def send(data: str, description: str):
        seen.append((base64.b64decode(data), description))
        return "sent"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=model_call("send", {"data": item.ref, "description": text}),
        capabilities=[ObjectStoreCapability(), Capability(toolsets=[ObjectRefToolset(FunctionToolset([send]))])],
    )
    result = await executable.run("send", bindings=RunBindings.embedded(object_store=store))
    assert result.output_or_raise() == "done"
    assert seen == [(PAYLOAD, text)]
    assert store.reads == 1


@pytest.mark.parametrize("dispatch", ["direct", "proxy", "codeact"])
@pytest.mark.parametrize("single", [False, True])
@pytest.mark.parametrize("supplied", [False, True])
async def test_reference_dispatch_validates_once_without_double_transform(dispatch, single, supplied):
    from a13n_harness.capabilities import (
        CodeActCapability,
        ProxyToolSearchConfig,
        ToolSearchCapability,
        ToolSearchGroup,
    )
    from a13n_harness.toolsets import CodeActPolicyToolset, CodeActToolPolicy
    from pydantic import AfterValidator, BaseModel

    def double(value):
        return value * 2

    class Payload(BaseModel):
        data: str
        copies: Annotated[int, AfterValidator(double)] = Field(default=1, validate_default=True)

    store, seen, validated = Store(), [], []
    item = await store.put(PAYLOAD, media_type="image/png")

    def send(data: str, copies=1):
        seen.append((base64.b64decode(data), copies))
        return "sent"

    send.__annotations__["copies"] = Annotated[int, AfterValidator(double), Field(validate_default=True)]

    def send_single(payload):
        return send(payload.data, payload.copies)

    send_single.__annotations__["payload"] = Payload
    target = send_single if single else send

    def validate(ctx, **args):
        validated.append(args["payload"].copies if single else args["copies"])

    payload = {"data": item.ref, **({"copies": 1} if supplied else {})}
    # Single-model native tools advertise the unwrapped model as their schema.
    arguments = dict(payload)
    source = ObjectRefToolset(FunctionToolset([Tool(target, args_validator=validate)], id="transform"))
    capabilities = [ObjectStoreCapability()]
    if dispatch == "proxy":
        capabilities.append(
            ToolSearchCapability(
                groups={"media": ToolSearchGroup(source, "Media")}, config=ProxyToolSearchConfig(call_name="invoke")
            )
        )
        name, arguments = "invoke", {"group": "media", "tool": target.__name__, "arguments": arguments}
    elif dispatch == "codeact":
        capabilities.extend(
            [Capability(toolsets=[CodeActPolicyToolset(source, CodeActToolPolicy(default=True))]), CodeActCapability()]
        )
        keyword_args = (
            f"payload={payload!r}" if single else ", ".join(f"{key}={value!r}" for key, value in payload.items())
        )
        name, arguments = "run_code", {"code": f"await {target.__name__}({keyword_args})"}
    else:
        capabilities.append(Capability(toolsets=[source]))
        name = target.__name__
    executable = HarnessBuilder().build(
        AgentSpec(), output_type=str, model=model_call(name, arguments), capabilities=capabilities
    )
    result = await executable.run("send", bindings=RunBindings.embedded(object_store=store))
    assert result.output_or_raise() == "done"
    assert seen == [(PAYLOAD, 2)], result.all_messages()
    assert validated == [2]
    assert store.reads == 1


@pytest.mark.parametrize("single", [False, True])
async def test_reference_dispatch_preserves_default_factory_results(single):
    from pydantic import AfterValidator, BaseModel

    generated, checked, seen = [], [], []

    def next_token(data):
        generated.append(len(generated) + 1)
        assert "data" in data
        return generated[-1]

    class Payload(BaseModel):
        data: str
        token: Annotated[int, AfterValidator(lambda value: value * 2)] = Field(
            default_factory=next_token, validate_default=True
        )

    def send(payloads, label: str = "upload"):
        seen.extend((payload.data, payload.token) for payload in payloads)
        return "sent"

    send.__annotations__["payloads"] = list[Payload]

    class Batch(BaseModel):
        payloads: list[Payload]

    def send_single(batch):
        return send(batch.payloads)

    send_single.__annotations__["batch"] = Batch
    target = send_single if single else send

    def validate(ctx, **args):
        payloads = args["batch"].payloads if single else args["payloads"]
        checked.append([payload.token for payload in payloads])

    store = Store()
    item = await store.put(PAYLOAD, media_type="image/png")
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=model_call(target.__name__, {"payloads": [{"data": item.ref}, {"data": item.ref}]}),
        capabilities=[
            ObjectStoreCapability(),
            Capability(toolsets=[ObjectRefToolset(FunctionToolset([Tool(target, args_validator=validate)]))]),
        ],
    )
    result = await executable.run("send", bindings=RunBindings.embedded(object_store=store))
    assert result.output_or_raise() == "done"
    assert generated == [1, 2]
    assert checked == [[2, 4]]
    assert seen == [(base64.b64encode(PAYLOAD).decode(), 2), (base64.b64encode(PAYLOAD).decode(), 4)]
    assert store.reads == 1


async def test_reference_dispatch_preserves_before_validation_rewrite():
    from pydantic import AfterValidator
    from pydantic_ai.capabilities import AbstractCapability

    class Rewrite(AbstractCapability):
        async def before_tool_validate(self, ctx, *, call, tool_def, args):
            if tool_def.name == "send":
                return {**json.loads(args), "copies": 3}
            return args

    store, seen = Store(), []
    item = await store.put(PAYLOAD, media_type="image/png")

    def send(data: str, copies):
        seen.append((base64.b64decode(data), copies))
        return "sent"

    send.__annotations__["copies"] = Annotated[int, AfterValidator(lambda value: value * 2)]

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=model_call("send", {"data": item.ref, "copies": 1}),
        capabilities=[
            ObjectStoreCapability(),
            Capability(toolsets=[ObjectRefToolset(FunctionToolset([send]))]),
            Rewrite(),
        ],
    )
    result = await executable.run("send", bindings=RunBindings.embedded(object_store=store))
    assert result.output_or_raise() == "done"
    assert seen == [(PAYLOAD, 6)]
    assert store.reads == 1


async def test_reference_dispatch_preserves_after_validation_rewrite():
    from pydantic import AfterValidator
    from pydantic_ai.capabilities import AbstractCapability

    class Rewrite(AbstractCapability):
        async def after_tool_validate(self, ctx, *, call, tool_def, args):
            if tool_def.name == "send":
                return {**args, "copies": 3}
            return args

    store, seen = Store(), []
    item = await store.put(PAYLOAD, media_type="image/png")

    def send(data: str, copies):
        seen.append((base64.b64decode(data), copies))
        return "sent"

    send.__annotations__["copies"] = Annotated[int, AfterValidator(lambda value: value * 2)]

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=model_call("send", {"data": item.ref, "copies": 1}),
        capabilities=[
            ObjectStoreCapability(),
            Capability(toolsets=[ObjectRefToolset(FunctionToolset([send]))]),
            Rewrite(),
        ],
    )
    result = await executable.run("send", bindings=RunBindings.embedded(object_store=store))
    assert result.output_or_raise() == "done"
    assert seen == [(PAYLOAD, 3)]
    assert store.reads == 1


async def test_reference_dispatch_reads_only_references_remaining_after_hooks():
    from pydantic_ai.capabilities import AbstractCapability

    store, seen, rewritten = Store(), [], []
    removed = await store.put(b"removed", media_type="image/png")
    retained = await store.put(PAYLOAD, media_type="image/png")
    await store.delete(removed.object_id)

    class Rewrite(AbstractCapability):
        async def after_tool_validate(self, ctx, *, call, tool_def, args):
            if tool_def.name == "send":
                assert store.reads == 0
                rewritten.append(args)
                return {**args, "first": "removed by hook"}
            return args

    def send(first: str, second: str):
        seen.append((first, base64.b64decode(second)))
        return "sent"

    arguments = {"first": removed.ref, "second": retained.ref}
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=model_call("send", arguments),
        capabilities=[
            ObjectStoreCapability(),
            Capability(toolsets=[ObjectRefToolset(FunctionToolset([send]))]),
            Rewrite(),
        ],
    )
    result = await executable.run("send", bindings=RunBindings.embedded(object_store=store))
    assert result.output_or_raise() == "done"
    assert seen == [("removed by hook", PAYLOAD)]
    assert rewritten == [arguments]
    assert store.reads == 1
    calls = [part for message in result.all_messages() for part in message.parts if part.part_kind == "tool-call"]
    assert calls[0].args_as_dict() == arguments


async def test_reference_transport_does_not_bypass_native_field_validator_failure():
    from pydantic import AfterValidator

    store, validated = Store(), []
    item = await store.put(PAYLOAD, media_type="image/png")

    def reject(value):
        assert store.reads == 0
        validated.append(value)
        raise ValueError("Native validator rejected reference")

    def send(data):
        pytest.fail("rejected reference reached consumer")

    send.__annotations__["data"] = Annotated[str, AfterValidator(reject)]
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=model_call("send", {"data": item.ref}),
        capabilities=[ObjectStoreCapability(), Capability(toolsets=[ObjectRefToolset(FunctionToolset([send]))])],
    )
    result = await executable.run("send", bindings=RunBindings.embedded(object_store=store))
    assert result.output_or_raise() == "done"
    assert validated == [item.ref]
    assert store.reads == 0
    assert "Native validator rejected reference" in str(result.all_messages())


async def test_reference_dispatch_preserves_named_tuple_types_without_revalidation():
    from typing import NamedTuple

    from pydantic import AfterValidator

    transformed, seen = [], []

    def double(value):
        transformed.append(value)
        return value * 2

    class Upload(NamedTuple):
        data: str
        copies: int

    Upload.__annotations__["copies"] = Annotated[int, AfterValidator(double)]

    def send(upload, label: str = "sent"):
        assert isinstance(upload, Upload)
        seen.append((base64.b64decode(upload.data), upload.copies, label))
        return "sent"

    send.__annotations__["upload"] = Upload
    store = Store()
    item = await store.put(PAYLOAD, media_type="image/png")
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=model_call("send", {"upload": [item.ref, "2"]}),
        capabilities=[ObjectStoreCapability(), Capability(toolsets=[ObjectRefToolset(FunctionToolset([send]))])],
    )
    result = await executable.run("send", bindings=RunBindings.embedded(object_store=store))
    assert result.output_or_raise() == "done"
    assert seen == [(PAYLOAD, 4, "sent")]
    assert transformed == [2]
    assert store.reads == 1

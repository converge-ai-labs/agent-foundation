from __future__ import annotations

from dataclasses import dataclass
from types import ModuleType
from typing import Any

from google.protobuf import descriptor_pb2

EIP_PACKAGE = "a13n.agent_envd.eip.v1"
EIP_PREFIX = f".{EIP_PACKAGE}."


@dataclass(frozen=True)
class SchemaIndex:
    files: tuple[descriptor_pb2.FileDescriptorProto, ...]
    messages: dict[str, descriptor_pb2.DescriptorProto]
    enums: dict[str, descriptor_pb2.EnumDescriptorProto]
    map_entries: dict[str, descriptor_pb2.DescriptorProto]
    methods: tuple[descriptor_pb2.MethodDescriptorProto, ...]


@dataclass(frozen=True)
class DataFrameProfile:
    magic: bytes
    profile_version: int
    eip_major: int
    header_bytes: int
    magic_bytes: int
    version_bytes: int
    kind_bytes: int
    status_bytes: int
    handle_length_bytes: int
    reserved_bytes: int
    stream_offset_bytes: int
    payload_length_bytes: int
    kinds: dict[str, int]
    reset_statuses: dict[str, int]

    @property
    def maximum_handle_bytes(self) -> int:
        return 2 ** (8 * self.handle_length_bytes) - 1

    @property
    def maximum_payload_bytes(self) -> int:
        return 2 ** (8 * self.payload_length_bytes) - 1


def build_index(descriptor_set: descriptor_pb2.FileDescriptorSet) -> SchemaIndex:
    files = tuple(file for file in descriptor_set.file if file.package == EIP_PACKAGE)
    messages: dict[str, descriptor_pb2.DescriptorProto] = {}
    enums: dict[str, descriptor_pb2.EnumDescriptorProto] = {}
    map_entries: dict[str, descriptor_pb2.DescriptorProto] = {}
    methods: list[descriptor_pb2.MethodDescriptorProto] = []

    def add_message(message: descriptor_pb2.DescriptorProto, prefix: str) -> None:
        full_name = f"{prefix}.{message.name}"
        if message.options.map_entry:
            map_entries[full_name] = message
        else:
            messages[full_name] = message
        for nested in message.nested_type:
            add_message(nested, full_name)
        for enum in message.enum_type:
            enums[f"{full_name}.{enum.name}"] = enum

    for file in files:
        prefix = f".{file.package}"
        for message in file.message_type:
            add_message(message, prefix)
        for enum in file.enum_type:
            enums[f"{prefix}.{enum.name}"] = enum
        for service in file.service:
            methods.extend(service.method)

    return SchemaIndex(
        files=files,
        messages=messages,
        enums=enums,
        map_entries=map_entries,
        methods=tuple(methods),
    )


class OptionReader:
    def __init__(self, module: ModuleType) -> None:
        self._module = module

    def file(self, file: descriptor_pb2.FileDescriptorProto) -> Any | None:
        if not file.options.HasExtension(self._module.eip_data_frame_profile):
            return None
        return file.options.Extensions[self._module.eip_data_frame_profile]

    def method(self, method: descriptor_pb2.MethodDescriptorProto) -> Any:
        if not method.options.HasExtension(self._module.eip_method):
            raise ValueError(f"method {method.name} has no eip_method option")
        return method.options.Extensions[self._module.eip_method]

    def message(self, message: descriptor_pb2.DescriptorProto) -> Any | None:
        if not message.options.HasExtension(self._module.eip_message):
            return None
        return message.options.Extensions[self._module.eip_message]

    def enum_value(self, value: descriptor_pb2.EnumValueDescriptorProto) -> Any | None:
        if not value.options.HasExtension(self._module.eip_enum_value):
            return None
        return value.options.Extensions[self._module.eip_enum_value]

    def field(self, field: descriptor_pb2.FieldDescriptorProto) -> Any | None:
        if not field.options.HasExtension(self._module.eip_field):
            return None
        return field.options.Extensions[self._module.eip_field]

    def enum_name(self, enum_type: str, value: int) -> str:
        return getattr(self._module, enum_type).Name(value)


def data_frame_profile(index: SchemaIndex, options: OptionReader) -> DataFrameProfile:
    candidates = [option for file in index.files if (option := options.file(file)) is not None]
    if len(candidates) != 1:
        raise ValueError(f"expected exactly one EIP data-frame profile, found {len(candidates)}")
    option = candidates[0]

    def enum_values(name: str, prefix: str) -> dict[str, int]:
        enum = index.enums.get(f"{EIP_PREFIX}{name}")
        if enum is None:
            raise ValueError(f"descriptor is missing {name}")
        return {value.name.removeprefix(prefix).lower(): value.number for value in enum.value if value.number != 0}

    profile = DataFrameProfile(
        magic=option.magic.encode("ascii"),
        profile_version=option.profile_version,
        eip_major=option.eip_major,
        header_bytes=option.header_bytes,
        magic_bytes=option.magic_bytes,
        version_bytes=option.version_bytes,
        kind_bytes=option.kind_bytes,
        status_bytes=option.status_bytes,
        handle_length_bytes=option.handle_length_bytes,
        reserved_bytes=option.reserved_bytes,
        stream_offset_bytes=option.stream_offset_bytes,
        payload_length_bytes=option.payload_length_bytes,
        kinds=enum_values("EIPDataFrameKind", "EIP_DATA_FRAME_KIND_"),
        reset_statuses=enum_values("EIPDataResetStatus", "EIP_DATA_RESET_STATUS_"),
    )
    widths = (
        profile.magic_bytes,
        profile.version_bytes,
        profile.kind_bytes,
        profile.status_bytes,
        profile.handle_length_bytes,
        profile.reserved_bytes,
        profile.stream_offset_bytes,
        profile.payload_length_bytes,
    )
    if profile.magic != b"EIPD" or len(profile.magic) != profile.magic_bytes:
        raise ValueError("EIP data-frame magic must be four-byte ASCII EIPD")
    if profile.profile_version != 1 or profile.eip_major != 1:
        raise ValueError("EIP major 1 must select data-frame profile 1")
    if widths != (4, 1, 1, 2, 2, 2, 8, 4) or sum(widths) != profile.header_bytes:
        raise ValueError("EIP data-frame profile has an incompatible header layout")
    if profile.kinds != {
        "attach": 1,
        "attached": 2,
        "chunk": 3,
        "end": 4,
        "end_ack": 5,
        "reset": 6,
    }:
        raise ValueError("EIP data-frame kind mapping is incompatible with profile 1")
    if profile.reset_statuses != {
        "protocol": 1,
        "denied": 2,
        "expired": 3,
        "source": 4,
        "limit": 5,
        "cancelled": 6,
        "internal": 7,
    }:
        raise ValueError("EIP data-frame reset mapping is incompatible with profile 1")
    return profile


def short_name(full_name: str) -> str:
    return full_name.rsplit(".", 1)[-1]


def real_oneofs(message: descriptor_pb2.DescriptorProto) -> dict[int, tuple[descriptor_pb2.FieldDescriptorProto, ...]]:
    grouped: dict[int, list[descriptor_pb2.FieldDescriptorProto]] = {}
    for field in message.field:
        if field.HasField("oneof_index") and not field.proto3_optional:
            grouped.setdefault(field.oneof_index, []).append(field)
    return {index: tuple(fields) for index, fields in grouped.items()}


def topological_messages(index: SchemaIndex) -> tuple[tuple[str, descriptor_pb2.DescriptorProto], ...]:
    remaining = dict(index.messages)
    ordered: list[tuple[str, descriptor_pb2.DescriptorProto]] = []
    emitted: set[str] = set()
    while remaining:
        progress = False
        for full_name in sorted(tuple(remaining)):
            message = remaining[full_name]
            dependencies = {
                field.type_name
                for field in message.field
                if field.type == descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE
                and field.type_name in index.messages
                and field.type_name != full_name
            }
            if dependencies <= emitted:
                ordered.append((full_name, message))
                emitted.add(full_name)
                del remaining[full_name]
                progress = True
        if not progress:
            # Forward annotations support recursive schemas; keep deterministic order.
            for full_name in sorted(remaining):
                ordered.append((full_name, remaining[full_name]))
            break
    return tuple(ordered)

"""Business-output validation and native Pydantic output adaptation."""

from __future__ import annotations

import inspect
import typing
from collections.abc import Awaitable, Callable, Collection, Coroutine, Sequence
from dataclasses import fields as dataclass_fields
from dataclasses import is_dataclass
from functools import reduce
from operator import or_
from typing import Any, cast, get_args, get_origin, get_type_hints

from pydantic import BaseModel, ConfigDict, PydanticSchemaGenerationError, TypeAdapter
from pydantic_ai.output import NativeOutput, OutputSpec, PromptedOutput, TextOutput, ToolOutput
from pydantic_ai.tools import DeferredToolRequests
from typing_extensions import is_typeddict


def _uses_tool_based_output(value: Any) -> bool:
    """Return whether the effective business output can create Pydantic output tools."""
    if isinstance(value, ToolOutput):
        return True
    if isinstance(value, TextOutput | NativeOutput | PromptedOutput):
        return False
    if isinstance(value, tuple | list):
        return any(_uses_tool_based_output(item) for item in value)
    return value is not str


def _prepare_pydantic_output_spec(value: Any) -> Any:
    """Give Pydantic semantic return annotations for supported sync-awaitable output functions."""
    if isinstance(value, TextOutput):
        output_function = _prepare_pydantic_output_callable(value.output_function)
        return value if output_function is value.output_function else TextOutput(output_function)
    if isinstance(value, ToolOutput):
        output = _prepare_pydantic_output_spec(value.output)
        if output is value.output:
            return value
        return ToolOutput(
            output,
            name=value.name,
            description=value.description,
            max_retries=value.max_retries,
            strict=value.strict,
            sequential=value.sequential,
        )
    if isinstance(value, NativeOutput):
        outputs = _prepare_pydantic_output_spec(value.outputs)
        if outputs is value.outputs:
            return value
        return NativeOutput(
            outputs,
            name=value.name,
            description=value.description,
            strict=value.strict,
            template=value.template,
        )
    if isinstance(value, PromptedOutput):
        outputs = _prepare_pydantic_output_spec(value.outputs)
        if outputs is value.outputs:
            return value
        return PromptedOutput(
            outputs,
            name=value.name,
            description=value.description,
            template=value.template,
        )
    if isinstance(value, tuple):
        prepared = tuple(_prepare_pydantic_output_spec(item) for item in value)
        return (
            value if all(current is original for current, original in zip(prepared, value, strict=True)) else prepared
        )
    if isinstance(value, list):
        prepared = [_prepare_pydantic_output_spec(item) for item in value]
        return (
            value if all(current is original for current, original in zip(prepared, value, strict=True)) else prepared
        )
    if inspect.isfunction(value) or inspect.ismethod(value):
        return _prepare_pydantic_output_callable(value)
    return value


def _prepare_pydantic_output_callable(function: Callable[..., Any]) -> Callable[..., Any]:
    type_hints = get_type_hints(function, include_extras=True)
    return_type = type_hints.get("return", Any)
    semantic_return_type = _semantic_output_return_type(function, return_type)
    if semantic_return_type == return_type:
        return function

    def output_facade(*args: Any, **kwargs: Any) -> Any:
        return function(*args, **kwargs)

    output_facade.__name__ = function.__name__
    output_facade.__qualname__ = function.__qualname__
    output_facade.__module__ = function.__module__
    output_facade.__doc__ = function.__doc__
    output_facade.__annotations__ = {**type_hints, "return": semantic_return_type}
    signature = inspect.signature(function).replace(return_annotation=semantic_return_type)
    cast(Any, output_facade).__signature__ = signature
    return output_facade


def _semantic_output_return_type(function: Callable[..., Any], return_type: Any) -> Any:
    if inspect.iscoroutinefunction(function):
        return return_type
    candidate = return_type
    while get_origin(candidate) is typing.Annotated:
        arguments = get_args(candidate)
        candidate = arguments[0] if arguments else Any
    origin = get_origin(candidate)
    if origin is Awaitable:
        arguments = get_args(candidate)
        return arguments[0] if arguments else Any
    if origin is Coroutine:
        arguments = get_args(candidate)
        return arguments[2] if len(arguments) == 3 else Any
    return return_type


def _business_output_contains_deferred_value(value: Any) -> bool:
    """Fail closed if reserved suspension control leaks into a completed container value."""
    seen: set[int] = set()

    def contains(item: Any) -> bool:
        if isinstance(item, DeferredToolRequests):
            return True
        if isinstance(item, dict):
            nested = list(item.values())
        elif isinstance(item, BaseModel):
            nested = [getattr(item, field_name) for field_name in type(item).model_fields]
            if item.model_extra is not None:
                nested.extend(item.model_extra.values())
        elif not isinstance(item, type) and is_dataclass(item):
            nested = [getattr(item, field.name) for field in dataclass_fields(cast(Any, item))]
        elif isinstance(item, list | tuple | set | frozenset):
            nested = list(item)
        else:
            return False
        item_id = id(item)
        if item_id in seen:
            return False
        seen.add(item_id)
        return any(contains(nested_value) for nested_value in nested)

    return contains(value)


def _output_spec_contains_deferred_requests(output_spec: OutputSpec[Any]) -> bool:
    """Return whether a business output spec directly or transitively reserves deferred control output."""
    seen: set[int] = set()

    def contains(value: Any) -> bool:
        value_id = id(value)
        if value_id in seen:
            return False
        seen.add(value_id)

        if isinstance(value, type) and issubclass(value, DeferredToolRequests):
            return True
        if isinstance(value, type) and issubclass(value, BaseModel):
            return any(contains(model_field.annotation) for model_field in value.model_fields.values())
        if isinstance(value, type) and is_dataclass(value):
            annotations = get_type_hints(value, include_extras=True)
            return any(contains(annotations.get(item.name, item.type)) for item in dataclass_fields(value))
        if isinstance(value, type) and is_typeddict(value):
            return any(contains(annotation) for annotation in get_type_hints(value, include_extras=True).values())
        if isinstance(value, typing.TypeAliasType):
            return contains(value.__value__)
        if get_origin(value) is typing.Annotated:
            arguments = get_args(value)
            return bool(arguments) and contains(arguments[0])
        if isinstance(value, NativeOutput | PromptedOutput):
            return contains(value.outputs)
        if isinstance(value, ToolOutput):
            return contains(value.output)
        if isinstance(value, TextOutput):
            return contains_callable(value.output_function)
        if isinstance(value, Sequence):
            return any(contains(item) for item in value)
        origin = get_origin(value)
        if origin in (typing.Union, type(str | int), typing.Required, typing.NotRequired):
            return any(contains(item) for item in get_args(value))
        if isinstance(origin, type):
            structured_origin = issubclass(origin, BaseModel) or is_dataclass(origin) or is_typeddict(origin)
            if structured_origin or issubclass(origin, Collection):
                return any(item is not Ellipsis and contains(item) for item in get_args(value))
        if inspect.isfunction(value) or inspect.ismethod(value):
            return contains_callable(value)
        return False

    def contains_callable(function: Callable[..., Any]) -> bool:
        return_type = get_type_hints(function, include_extras=True).get("return", Any)
        return contains(_semantic_output_return_type(function, return_type))

    return contains(output_spec)


def _build_output_adapter(output_spec: OutputSpec[Any]) -> TypeAdapter[Any]:
    """Build a validator for the semantic value returned by an output specification."""
    output_types: list[Any] = []

    def collect(value: Any) -> None:
        if isinstance(value, NativeOutput | PromptedOutput):
            collect(value.outputs)
        elif isinstance(value, ToolOutput):
            collect(value.output)
        elif isinstance(value, TextOutput):
            collect_callable(value.output_function)
        elif isinstance(value, Sequence):
            for item in value:
                collect(item)
        elif get_origin(value) in (typing.Union, type(str | int)):
            for item in get_args(value):
                collect(item)
        elif inspect.isfunction(value) or inspect.ismethod(value):
            collect_callable(value)
        elif value is None:
            output_types.append(type(None))
        else:
            output_types.append(value)

    def collect_callable(function: Callable[..., Any]) -> None:
        return_type = get_type_hints(function, include_extras=True).get("return", Any)
        collect(_semantic_output_return_type(function, return_type))

    collect(output_spec)
    if not output_types:
        raise ValueError("Output specifications must contain at least one semantic output type.")
    validation_type = reduce(or_, output_types)
    try:
        return TypeAdapter(validation_type)
    except PydanticSchemaGenerationError:
        return TypeAdapter(validation_type, config=ConfigDict(arbitrary_types_allowed=True))

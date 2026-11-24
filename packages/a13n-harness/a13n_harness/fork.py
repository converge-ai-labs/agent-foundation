"""Run-local plugin settlement and text-only replay of a settled model prefix."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

from pydantic import JsonValue
from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability, CapabilityOrdering
from pydantic_ai.direct import model_request
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    NativeToolCallPart,
    TextPart,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models import Model, ModelRequestContext, ModelRequestParameters, ModelResolutionContext
from pydantic_ai.settings import ModelSettings, merge_model_settings

from a13n_harness.errors import PluginError
from a13n_harness.metering import MeteredModel, ModelUsageBinding
from a13n_harness.models.self_healing import SelfHealingModel
from a13n_harness.usage import RunUsageLedger, UsageCapability, UsageDeltaReporter, UsageReporter

if TYPE_CHECKING:
    from a13n_harness.builder import AgentDefinition
    from a13n_harness.context import AgentContext
    from a13n_harness.models.binding import RunModelResolver
    from a13n_harness.plugins import AbstractHarnessPlugin
    from a13n_harness.pricing import AbstractModelCostCapability
    from a13n_harness.result import HarnessRunResult


_PREFIX_CAPTURE_ID = "a13n.post_run_prefix"
_NON_REPLAYABLE_SETTINGS = frozenset(
    ("extra_body", "openai_conversation_id", "openai_background", "anthropic_container", "google_cached_content")
)


def _can_replay_settings(settings: ModelSettings | None) -> bool:
    return not settings or not any(settings.get(key) for key in _NON_REPLAYABLE_SETTINGS)


@dataclass(frozen=True, slots=True)
class _ReplayPrefix:
    model: Model
    model_id: str | None
    messages: tuple[ModelMessage, ...]
    parameters: ModelRequestParameters
    settings: ModelSettings | None


class _PrefixCapture(AbstractCapability["AgentContext"]):
    id = _PREFIX_CAPTURE_ID

    def __init__(self) -> None:
        self.run_id: str | None = None
        self.prefix: _ReplayPrefix | None = None

    def get_ordering(self) -> CapabilityOrdering:
        return CapabilityOrdering(position="innermost", wraps=(UsageCapability,))

    def begin(self, run_id: str) -> None:
        self.run_id = run_id
        self.prefix = None

    async def after_model_request(
        self, ctx: RunContext[AgentContext], *, request_context: ModelRequestContext, response: ModelResponse
    ) -> ModelResponse:
        if ctx.run_id != self.run_id:
            return response
        self.prefix = None
        if not _can_replay_settings(request_context.model_settings) or any(
            isinstance(part, ToolCallPart) for part in response.parts
        ):
            return response
        params = request_context.model_request_parameters
        # Drop Harness wrappers that carry foreground usage or retry state.
        model = request_context.model
        while isinstance(model, (MeteredModel, SelfHealingModel)):
            model = model.wrapped
        self.prefix = _ReplayPrefix(
            model=model,
            model_id=request_context.model_id,
            messages=(*request_context.messages, response),
            parameters=replace(
                params,
                function_tools=[replace(tool, metadata=None) for tool in params.function_tools],
                native_tools=[],
                output_tools=[],
                output_object=None,
                prompted_output_template=None,
                output_mode="text",
                allow_text_output=True,
                allow_image_output=False,
            ),
            settings=request_context.model_settings,
        )
        return response


@dataclass(frozen=True, slots=True)
class ForkedAgent:
    """Read-only text generation over a settled prefix, with no tool dispatch."""

    definition: AgentDefinition[Any]
    _prefix: _ReplayPrefix | None
    _resolution: ModelResolutionContext[AgentContext]
    _resolver: RunModelResolver
    _cost: AbstractModelCostCapability
    _resolver_override: RunModelResolver | None = None
    _reporter: UsageReporter | UsageDeltaReporter | None = None

    @property
    def can_replay_prefix(self) -> bool:
        return self._prefix is not None

    @property
    def settled_model_id(self) -> str | None:
        prefix = self._prefix
        return (prefix.model_id or prefix.model.model_name) if prefix is not None else None

    async def complete_text(self, prompt: str, *, source: str = "plugin_post_run") -> str | None:
        """Append a private prompt and return raw text from a single model request."""
        prefix = self._prefix
        if prefix is None:
            return None
        model_id = prefix.model_id or prefix.model.model_name
        model = prefix.model
        if self._resolver_override is not None or prefix.model_id is not None:
            model = await (self._resolver_override or self._resolver)(self._resolution, model_id)
        settings = merge_model_settings(model.settings, prefix.settings)
        if not _can_replay_settings(settings):
            return None
        if "openai_native_tools" in (settings or {}):
            settings = cast(ModelSettings, {**(settings or {}), "openai_native_tools": ()})
        messages = model.prepare_messages(
            [*prefix.messages, ModelRequest(parts=[UserPromptPart(prompt)])], prefix.parameters
        )
        request = ModelRequestContext(
            model=model,
            model_id=model_id,
            messages=messages,
            model_settings=settings,
            model_request_parameters=prefix.parameters,
        )
        deps = self._resolution.deps
        ledger = RunUsageLedger(
            run_id=f"run-{uuid4().hex}", thread_id=deps.thread_id, instance=deps.instance, reporter=self._reporter
        )
        binding = ModelUsageBinding(ledger, self._cost, source=source)
        async with model:
            response = await model_request(
                MeteredModel(request, binding, None),
                messages,
                model_settings=settings,
                model_request_parameters=prefix.parameters,
                instrument=False,
            )
        if any(isinstance(part, (ToolCallPart, NativeToolCallPart)) for part in response.parts):
            return None
        text = "".join(part.content for part in response.parts if isinstance(part, TextPart))
        return text if text and not any(marker in text for marker in ("\x00", "\ufffc")) else None


class PostRunSession:
    """One process-local opportunity to run plugin hooks after Host settlement."""

    def __init__(
        self,
        *,
        agent: ForkedAgent,
        plugins: tuple[AbstractHarnessPlugin, ...],
        result: HarnessRunResult[Any],
    ) -> None:
        self._agent = agent
        self._plugins = plugins
        self._result = result
        self._started = False

    async def run(
        self,
        *,
        model_resolver: RunModelResolver | None = None,
        usage_reporter: UsageReporter | UsageDeltaReporter | None = None,
        metadata: Mapping[str, JsonValue] | None = None,
    ) -> None:
        """Run the same bound plugins inside-out, then raise any ordinary failures together."""
        from a13n_harness.plugins import PluginPostRunContext

        if self._started:
            raise PluginError("Post-run hooks may only run once.", code="post_run_reused")
        self._started = True
        deps = self._agent._resolution.deps
        context = PluginPostRunContext(
            thread_id=deps.thread_id,
            run_id=deps.run_id,
            instance=deps.instance,
            metadata=MappingProxyType({**deps.metadata, **(metadata or {})}),
            result=self._result,
            shared=deps.plugins.shared,
            agent=replace(self._agent, _resolver_override=model_resolver, _reporter=usage_reporter),
        )
        errors: list[Exception] = []
        for plugin in reversed(self._plugins):
            try:
                await plugin.post_run(context)
            except Exception as error:
                errors.append(error)
        if errors:
            raise ExceptionGroup("Plugin post_run failed.", errors)

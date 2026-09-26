"""Code-first Agent definitions and construction."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field, replace
from dataclasses import fields as dataclass_fields
from typing import Any, Literal, Self, cast, overload
from uuid import uuid4

from pydantic import JsonValue, TypeAdapter
from pydantic_ai import Agent
from pydantic_ai.agent.abstract import AbstractAgent
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.capabilities import (
    AbstractCapability,
    CombinedCapability,
    ResolveModelId,
    WrapperCapability,
)
from pydantic_ai.models import Model, ModelResolutionContext
from pydantic_ai.models.instrumented import InstrumentedModel
from pydantic_ai.output import OutputSpec
from pydantic_ai.tools import DeferredToolRequests
from pydantic_ai.usage import UsageLimits

from a13n_harness._capability_contract import _validate_built_capability_tree, _validate_capability_source
from a13n_harness._output_contract import (
    _build_output_adapter,
    _output_spec_contains_deferred_requests,
    _prepare_pydantic_output_spec,
    _uses_tool_based_output,
)
from a13n_harness.capabilities.context import (
    HandoffCapability,
)
from a13n_harness.capabilities.lifecycle import (
    LifecycleEventCapability,
)
from a13n_harness.capabilities.steering import (
    SteeringCapability,
)
from a13n_harness.capabilities.tool_proxy import (
    ToolProxyPlan,
)
from a13n_harness.capability_types import (
    CapabilityTypeCatalog,
    first_party_declarative_capability_types,
)
from a13n_harness.context import (
    AgentContext,
    BuiltSubagent,
    RunBindings,
    SubagentCollection,
)
from a13n_harness.errors import (
    DefinitionError,
    HarnessError,
    ModelResolutionError,
    PluginError,
)
from a13n_harness.execution import ExecutableAgent
from a13n_harness.filters.cold_start import ColdStartFilterCapability, ColdStartFilterConfiguration
from a13n_harness.filters.integrity import (
    MessageIntegrityFilterCapability,
)
from a13n_harness.identity import AgentIdentityRef
from a13n_harness.model_context import (
    ModelContextCoordinatorCapability,
)
from a13n_harness.models.binding import resolve_run_model
from a13n_harness.models.inference import GatewayModelProviderFactory, infer_model
from a13n_harness.models.profile import project_context_window
from a13n_harness.models.request_headers import (
    ModelRequestHeadersCapability,
    ModelRequestPatchConfiguration,
)
from a13n_harness.models.structured_output import (
    StructuredOutputAutoToolChoiceCapability,
)
from a13n_harness.observation import (
    HarnessInstrumentation,
    _compile_observation,
)
from a13n_harness.output_schema import structured_output_type
from a13n_harness.plugin_configuration import HarnessBuildContext, HarnessPluginConfiguration
from a13n_harness.plugin_factories import (
    HarnessPluginFactoryCatalog,
    HarnessPluginFactoryContext,
    build_harness_plugin_factory_catalog,
)
from a13n_harness.plugins import (
    AbstractHarnessPlugin,
    bind_agent_plugins,
)
from a13n_harness.pricing import (
    AbstractModelCostCapability,
    CatalogModelCostCapability,
    PricingCatalog,
    get_current_pricing_catalog,
)
from a13n_harness.recovery import (
    ModelRecoveryPolicy,
)
from a13n_harness.spec import AgentSpec as HarnessAgentSpec
from a13n_harness.tools.invocation import (
    ToolExecutionBoundaryCapability,
)
from a13n_harness.tools.permissions import ToolPermissionsCapability
from a13n_harness.tools.surface import (
    ToolSurfaceCapability,
)
from a13n_harness.usage import UsageCapability

_EMPTY_CAPABILITY_TYPE_CATALOG = CapabilityTypeCatalog()


class _UnsetOutputType:
    __slots__ = ()


_UNSET_OUTPUT_TYPE = _UnsetOutputType()


@dataclass(frozen=True, slots=True)
class DelegationContextPolicy:
    """Portable ceilings on context and working-state sharing for one child edge."""

    include_task: bool = True
    history: Literal["none", "summary", "selected"] = "none"
    task_state: Literal["shared", "isolated"] = "shared"

    def __post_init__(self) -> None:
        if not isinstance(self.include_task, bool):
            raise DefinitionError("Subagent include_task policy must be a boolean.", code="subagent_context_invalid")
        if self.history not in {"none", "summary", "selected"}:
            raise DefinitionError("Unsupported subagent history policy.", code="subagent_context_invalid")
        if self.task_state not in {"shared", "isolated"}:
            raise DefinitionError("Unsupported subagent task-state policy.", code="subagent_context_invalid")


@dataclass(frozen=True, slots=True)
class SubagentIdentityPolicy:
    """Portable child Identity derivation for one authored edge."""

    inherit_agent_id: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.inherit_agent_id, bool):
            raise DefinitionError(
                "Subagent inherit_agent_id policy must be a boolean.",
                code="subagent_identity_invalid",
            )


def derive_child_identity(
    parent: AgentIdentityRef,
    child_agent_id: str,
    policy: SubagentIdentityPolicy | None = None,
) -> AgentIdentityRef:
    """Derive a fresh child Identity from one trusted parent Identity."""
    if not isinstance(parent, AgentIdentityRef):
        raise TypeError("parent must be an AgentIdentityRef")
    if not isinstance(child_agent_id, str) or not child_agent_id.strip():
        raise DefinitionError("child_agent_id must be a non-blank string.", code="subagent_identity_invalid")
    selected_policy = policy or SubagentIdentityPolicy()
    if not isinstance(selected_policy, SubagentIdentityPolicy):
        raise DefinitionError("policy must be SubagentIdentityPolicy.", code="subagent_identity_invalid")
    claims = dict(parent.claims)
    if not selected_policy.inherit_agent_id:
        claims["agent_id"] = child_agent_id
    return AgentIdentityRef(issuer=parent.issuer, subject=parent.subject, **claims)


@dataclass(frozen=True, slots=True)
class SubagentDefinition:
    """One named process-local child definition and authored edge ceilings."""

    name: str
    description: str
    agent: AgentDefinition[Any]
    context: DelegationContextPolicy = field(default_factory=DelegationContextPolicy)
    identity: SubagentIdentityPolicy = field(default_factory=SubagentIdentityPolicy)
    usage_limits: UsageLimits | None = None
    run_bindings_factory: Callable[[RunBindings], RunBindings] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise DefinitionError("Subagent name must be a non-blank string.", code="subagent_name_invalid")
        if not isinstance(self.description, str) or not self.description.strip():
            raise DefinitionError(
                "Subagent description must be a non-blank string.",
                code="subagent_description_invalid",
            )
        if not isinstance(self.agent, AgentDefinition):
            raise DefinitionError("Subagent agent must be an AgentDefinition.", code="subagent_definition_invalid")
        if not isinstance(self.context, DelegationContextPolicy):
            raise DefinitionError("Subagent context must be DelegationContextPolicy.", code="subagent_context_invalid")
        if not isinstance(self.identity, SubagentIdentityPolicy):
            raise DefinitionError("Subagent identity must be SubagentIdentityPolicy.", code="subagent_identity_invalid")
        if self.usage_limits is not None and not isinstance(self.usage_limits, UsageLimits):
            raise DefinitionError("Subagent usage_limits must be UsageLimits or None.", code="subagent_limits_invalid")
        if self.run_bindings_factory is not None and not callable(self.run_bindings_factory):
            raise DefinitionError("Child run bindings factory must be callable.", code="subagent_binding_invalid")
        object.__setattr__(self, "usage_limits", deepcopy(self.usage_limits))


@dataclass(frozen=True, slots=True)
class AgentDefinition[OutputT]:
    """Immutable code-first inputs for one process-local executable Agent."""

    agent: AgentSpec
    output_type: OutputSpec[OutputT] | None
    definition_id: str = field(default_factory=lambda: str(uuid4()))
    model: Model | None = None
    capabilities: tuple[AbstractCapability[AgentContext], ...] = ()
    plugins: tuple[AbstractHarnessPlugin, ...] = ()
    tool_proxy: ToolProxyPlan | None = None
    subagents: tuple[SubagentDefinition, ...] = ()
    model_recovery: ModelRecoveryPolicy = field(default_factory=ModelRecoveryPolicy)

    def __post_init__(self) -> None:
        if not self.definition_id.strip():
            raise DefinitionError("definition_id must not be blank.", code="definition_id_invalid")
        if self.model is not None and not isinstance(self.model, Model):
            raise DefinitionError("model must be a native Model or None.", code="model_invalid")
        if isinstance(self.model, InstrumentedModel) or isinstance(self.agent.model, InstrumentedModel):
            raise DefinitionError(
                "InstrumentedModel is reserved to Harness-managed instrumentation.",
                code="instrumentation_owner_conflict",
            )
        if self.model is not None and self.agent.model is not None:
            raise DefinitionError(
                "AgentSpec.model and AgentDefinition.model are mutually exclusive.",
                code="model_selection_conflict",
            )
        has_schema = self.agent.output_schema is not None
        if self.output_type is not None and has_schema:
            raise DefinitionError(
                "AgentDefinition.output_type and AgentSpec.output_schema are mutually exclusive.",
                code="output_contract_conflict",
            )
        if self.output_type is None and not has_schema:
            raise DefinitionError(
                "AgentDefinition requires an explicit output_type or AgentSpec.output_schema.",
                code="output_contract_missing",
            )
        if self.output_type is not None and _output_spec_contains_deferred_requests(self.output_type):
            raise DefinitionError(
                "DeferredToolRequests is reserved for Harness suspension and cannot be a business output.",
                code="output_contract_reserved",
            )
        object.__setattr__(self, "agent", self.agent.model_copy(deep=True))
        object.__setattr__(self, "capabilities", tuple(self.capabilities))
        object.__setattr__(self, "plugins", tuple(self.plugins))
        if self.tool_proxy is not None and not isinstance(self.tool_proxy, ToolProxyPlan):
            raise TypeError("tool_proxy must be ToolProxyPlan or None")
        subagents = tuple(self.subagents)
        if not all(isinstance(child, SubagentDefinition) for child in subagents):
            raise DefinitionError(
                "subagents must contain only SubagentDefinition values.",
                code="subagent_definition_invalid",
            )
        names = [child.name for child in subagents]
        if len(set(names)) != len(names):
            raise DefinitionError("Subagent names must be unique within one parent.", code="subagent_name_duplicate")
        object.__setattr__(self, "subagents", subagents)

    def with_updates(
        self,
        updates: Mapping[str, object] | None = None,
        /,
        **overrides: object,
    ) -> Self:
        """Return a fully validated definition with selected top-level fields replaced."""

        requested: dict[str, object] = {}
        if updates is not None:
            if not isinstance(updates, Mapping) or not all(isinstance(key, str) for key in updates):
                raise TypeError("updates must be a mapping with string keys")
            requested.update(updates)
        for key, value in overrides.items():
            if key in requested:
                raise ValueError(f"AgentDefinition update field {key!r} was supplied more than once")
            requested[key] = value

        fields = {item.name for item in dataclass_fields(type(self)) if item.init}
        for key in requested:
            if key not in fields:
                raise ValueError(f"AgentDefinition has no updateable field {key!r}")
        return replace(self, **requested)


def _model_cost_capabilities(
    capabilities: Sequence[AbstractCapability[AgentContext]],
) -> tuple[AbstractModelCostCapability, ...]:
    leaves: list[AbstractCapability[AgentContext]] = []
    for capability in capabilities:
        if not isinstance(capability, AbstractCapability):
            continue
        capability.apply(leaves.append)
    return tuple(capability for capability in leaves if isinstance(capability, AbstractModelCostCapability))


@dataclass
class _DefaultColdStartCapability(AbstractCapability[AgentContext]):
    """Select the default only after native declarative capabilities are resolved."""

    configuration: ColdStartFilterConfiguration

    def for_agent(self, agent: AbstractAgent[AgentContext, Any]) -> AbstractCapability[AgentContext]:
        leaves: list[AbstractCapability[AgentContext]] = []
        agent.root_capability.apply(leaves.append)
        for capability in leaves:
            while isinstance(capability, WrapperCapability):
                capability = capability.wrapped
            if isinstance(capability, ColdStartFilterCapability):
                return CombinedCapability([])
        return ColdStartFilterCapability(self.configuration)


def _cold_start_capabilities(agent: AgentSpec) -> tuple[AbstractCapability[AgentContext], ...]:
    configuration = agent.cold_start_filter if isinstance(agent, HarnessAgentSpec) else ColdStartFilterConfiguration()
    return (_DefaultColdStartCapability(configuration),) if configuration is not None else ()


def _normalize_system_prompt(agent: AgentSpec) -> tuple[str, ...]:
    if not isinstance(agent, HarnessAgentSpec) or agent.system_prompt is None:
        return ()
    if isinstance(agent.system_prompt, str):
        return (agent.system_prompt,)
    return tuple(agent.system_prompt)


def _resolve_model_characteristics_capabilities(
    agent: AgentSpec,
    capabilities: tuple[AbstractCapability[AgentContext], ...],
) -> tuple[AbstractCapability[AgentContext], ...]:
    model_characteristics = agent.model_characteristics if isinstance(agent, HarnessAgentSpec) else None
    resolved: list[AbstractCapability[AgentContext]] = []
    for capability in capabilities:
        if isinstance(capability, HandoffCapability) and model_characteristics is not None:
            configuration = capability.configuration
            threshold_fields = {"include_summary_reminder", "summary_reminder_tokens"}
            if not threshold_fields.intersection(configuration.model_fields_set):
                proactive_threshold = model_characteristics.proactive_context_management_threshold
                if proactive_threshold is None:
                    configuration = configuration.model_copy(
                        update={"include_summary_reminder": False},
                        deep=True,
                    )
                    resolved.append(HandoffCapability(configuration))
                    continue
                if model_characteristics.context_window_tokens is not None:
                    reminder_tokens = model_characteristics.summary_reminder_tokens
                    assert reminder_tokens is not None
                    configuration = configuration.model_copy(
                        update={"summary_reminder_tokens": reminder_tokens},
                        deep=True,
                    )
                    resolved.append(HandoffCapability(configuration))
                    continue
        resolved.append(capability)
    return tuple(resolved)


class HarnessBuilder:
    """Build executable Agents through one authoritative Agent.from_spec path."""

    def __init__(
        self,
        *,
        capability_type_catalog: CapabilityTypeCatalog | None = None,
        build_context: HarnessBuildContext | None = None,
        configured_plugins_enabled: bool | None = None,
        gateway_provider_factory: GatewayModelProviderFactory | None = None,
        instrumentation: HarnessInstrumentation | Literal["environment"] | None = "environment",
        session_affinity_header: str | None = None,
        openai_prompt_cache_key_enabled: bool | None = None,
    ) -> None:
        if capability_type_catalog is not None and not isinstance(capability_type_catalog, CapabilityTypeCatalog):
            raise DefinitionError(
                "capability_type_catalog must be a CapabilityTypeCatalog or None.",
                code="capability_type_catalog_invalid",
            )
        if build_context is not None and not isinstance(build_context, HarnessBuildContext):
            raise PluginError(
                "build_context must be a HarnessBuildContext or None.",
                code="plugin_configuration_invalid",
            )
        if configured_plugins_enabled is not None and not isinstance(configured_plugins_enabled, bool):
            raise PluginError(
                "configured_plugins_enabled must be a boolean or None.",
                code="plugin_configuration_enablement_invalid",
            )
        if gateway_provider_factory is not None and not callable(gateway_provider_factory):
            raise DefinitionError(
                "gateway_provider_factory must be callable or None.",
                code="gateway_provider_factory_invalid",
            )
        self._capability_type_catalog = capability_type_catalog or _EMPTY_CAPABILITY_TYPE_CATALOG
        self._gateway_provider_factory = gateway_provider_factory
        resolved_instrumentation = (
            HarnessInstrumentation.from_environment() if instrumentation == "environment" else instrumentation
        )
        self._observation = _compile_observation(resolved_instrumentation)
        self._model_request_patch_configuration = ModelRequestPatchConfiguration.from_environment(
            session_affinity_header=session_affinity_header,
            openai_prompt_cache_key_enabled=openai_prompt_cache_key_enabled,
        )
        if build_context is None:
            resolved_build_context = HarnessBuildContext.from_environment(enabled=configured_plugins_enabled)
        else:
            effective_application = (
                build_context.configured_plugins_enabled
                if configured_plugins_enabled is None
                else configured_plugins_enabled
            )
            if effective_application and not isinstance(
                build_context.plugin_configuration,
                HarnessPluginConfiguration,
            ):
                raise PluginError(
                    "Enabling configured plugins in an explicit build context requires a configuration document.",
                    code="plugin_configuration_invalid",
                )
            resolved_build_context = HarnessBuildContext(
                configured_plugins_enabled=effective_application,
                plugin_configuration=build_context.plugin_configuration if effective_application else None,
                extensions=build_context.extensions,
            )
        self._build_context = resolved_build_context
        self._plugin_factory_catalog: HarnessPluginFactoryCatalog | None = None
        if self._build_context.configured_plugins_enabled:
            configuration = self._build_context.plugin_configuration
            if not isinstance(configuration, HarnessPluginConfiguration):
                raise PluginError(
                    "Enabled configured plugins require a configuration document.",
                    code="plugin_configuration_invalid",
                )
            selected_keys = tuple(dict.fromkeys(entry.plugin_key for entry in configuration.enabled_plugins))
            self._plugin_factory_catalog = build_harness_plugin_factory_catalog(
                plugin_keys=selected_keys,
            )

    @overload
    def build[BuildOutputT](
        self,
        definition: AgentDefinition[BuildOutputT],
        /,
        *,
        pricing_catalog: PricingCatalog | None = None,
    ) -> ExecutableAgent[BuildOutputT]: ...

    @overload
    def build[BuildOutputT](
        self,
        spec: AgentSpec,
        /,
        *,
        output_type: OutputSpec[BuildOutputT],
        definition_id: str | None = None,
        model: Model | None = None,
        capabilities: Sequence[AbstractCapability[AgentContext]] = (),
        plugins: Sequence[AbstractHarnessPlugin] = (),
        tool_proxy: ToolProxyPlan | None = None,
        subagents: Sequence[SubagentDefinition] = (),
        model_recovery: ModelRecoveryPolicy | None = None,
        pricing_catalog: PricingCatalog | None = None,
    ) -> ExecutableAgent[BuildOutputT]: ...

    @overload
    def build(
        self,
        spec: AgentSpec,
        /,
        *,
        output_type: None,
        definition_id: str | None = None,
        model: Model | None = None,
        capabilities: Sequence[AbstractCapability[AgentContext]] = (),
        plugins: Sequence[AbstractHarnessPlugin] = (),
        tool_proxy: ToolProxyPlan | None = None,
        subagents: Sequence[SubagentDefinition] = (),
        model_recovery: ModelRecoveryPolicy | None = None,
        pricing_catalog: PricingCatalog | None = None,
    ) -> ExecutableAgent[dict[str, JsonValue]]: ...

    def build(
        self,
        definition_or_spec: AgentDefinition[Any] | AgentSpec,
        /,
        *,
        output_type: OutputSpec[Any] | _UnsetOutputType | None = _UNSET_OUTPUT_TYPE,
        definition_id: str | None = None,
        model: Model | None = None,
        capabilities: Sequence[AbstractCapability[AgentContext]] = (),
        plugins: Sequence[AbstractHarnessPlugin] = (),
        tool_proxy: ToolProxyPlan | None = None,
        subagents: Sequence[SubagentDefinition] = (),
        model_recovery: ModelRecoveryPolicy | None = None,
        pricing_catalog: PricingCatalog | None = None,
    ) -> ExecutableAgent[Any]:
        """Build with one current or explicitly pinned default pricing snapshot.

        An authored model-cost Capability takes precedence over ``pricing_catalog``.
        """
        if pricing_catalog is not None and not isinstance(pricing_catalog, PricingCatalog):
            raise TypeError("pricing_catalog must be a PricingCatalog")
        if isinstance(definition_or_spec, AgentDefinition):
            if (
                not isinstance(output_type, _UnsetOutputType)
                or definition_id is not None
                or model is not None
                or capabilities
                or plugins
                or tool_proxy is not None
                or subagents
                or model_recovery is not None
            ):
                raise TypeError("AgentDefinition build does not accept AgentSpec construction arguments")
            return self._build_definition(
                definition_or_spec,
                active_definition_ids=(),
                pricing_catalog=get_current_pricing_catalog() if pricing_catalog is None else pricing_catalog,
            )
        if not isinstance(definition_or_spec, AgentSpec):
            raise TypeError("build() requires an AgentDefinition or AgentSpec")
        if isinstance(output_type, _UnsetOutputType):
            raise TypeError("AgentSpec build requires the keyword-only output_type argument")
        definition = AgentDefinition(
            agent=definition_or_spec,
            output_type=output_type,
            definition_id=definition_id or str(uuid4()),
            model=model,
            capabilities=tuple(capabilities),
            plugins=tuple(plugins),
            tool_proxy=tool_proxy,
            subagents=tuple(subagents),
            model_recovery=model_recovery if model_recovery is not None else ModelRecoveryPolicy(),
        )
        return self._build_definition(
            definition,
            active_definition_ids=(),
            pricing_catalog=get_current_pricing_catalog() if pricing_catalog is None else pricing_catalog,
        )

    def _build_definition[BuildOutputT](
        self,
        definition: AgentDefinition[BuildOutputT],
        *,
        active_definition_ids: tuple[int, ...],
        pricing_catalog: PricingCatalog,
    ) -> ExecutableAgent[BuildOutputT]:
        definition_object_id = id(definition)
        if definition_object_id in active_definition_ids:
            raise DefinitionError("Subagent definitions must form a finite acyclic graph.", code="subagent_cycle")
        child_path = (*active_definition_ids, definition_object_id)
        built_children = tuple(
            BuiltSubagent(
                declaration=child,
                definition=child.agent,
                executable=self._build_definition(
                    child.agent, active_definition_ids=child_path, pricing_catalog=pricing_catalog
                ),
            )
            for child in definition.subagents
        )
        subagents = SubagentCollection({child.declaration.name: child for child in built_children})
        configured_plugins = self._create_configured_plugins()
        plugins, contributions = bind_agent_plugins((*definition.plugins, *configured_plugins))
        plugin_capabilities = tuple(capability for sources in contributions.values() for capability in sources)
        _validate_capability_source(plugin_capabilities, source="plugin")
        _validate_capability_source(definition.capabilities, source="definition")
        selected_capabilities = (*definition.capabilities, *plugin_capabilities)
        if definition.tool_proxy is not None:
            selected_capabilities = definition.tool_proxy._compose(selected_capabilities, contributions)
        authored_capabilities = _resolve_model_characteristics_capabilities(
            definition.agent,
            selected_capabilities,
        )
        model_characteristics = (
            definition.agent.model_characteristics if isinstance(definition.agent, HarnessAgentSpec) else None
        )
        profile_context_window = (
            model_characteristics.context_window_tokens if model_characteristics is not None else None
        )
        selected_model_costs = _model_cost_capabilities(definition.capabilities)
        if len(selected_model_costs) > 1:
            raise DefinitionError(
                "AgentDefinition capabilities must contain at most one model-cost Capability.",
                code="capability_scope_invalid",
            )
        default_model_costs: tuple[AbstractModelCostCapability, ...] = (
            () if selected_model_costs else (CatalogModelCostCapability(catalog=pricing_catalog),)
        )
        definition_reserved_ids = _validate_capability_source(authored_capabilities, source="definition")

        async def resolve_model(
            context: ModelResolutionContext[AgentContext],
            model_id: str,
        ) -> Model:
            resolved = await resolve_run_model(context, model_id)
            if resolved is not None:
                if isinstance(resolved, InstrumentedModel):
                    raise ModelResolutionError(
                        "Run model resolution returned an InstrumentedModel reserved to Harness instrumentation.",
                        code="instrumentation_owner_conflict",
                        details={"model_id": model_id},
                    )
                return project_context_window(resolved, profile_context_window)
            inferred = infer_model(
                model_id,
                gateway_provider_factory=self._gateway_provider_factory,
            )
            if isinstance(inferred, InstrumentedModel):
                raise ModelResolutionError(
                    "Model inference returned an InstrumentedModel reserved to Harness instrumentation.",
                    code="instrumentation_owner_conflict",
                    details={"model_id": model_id},
                )
            return project_context_window(inferred, profile_context_window)

        try:
            construction_spec, business_output, output_adapter = _resolve_business_output(definition)
            definition_model = (
                project_context_window(definition.model, profile_context_window)
                if definition.model is not None
                else None
            )
            structured_output_capabilities = (
                (StructuredOutputAutoToolChoiceCapability(),) if _uses_tool_based_output(business_output) else ()
            )
            capabilities = (
                *self._observation.pydantic_capabilities,
                ToolExecutionBoundaryCapability(),
                ToolSurfaceCapability(),
                MessageIntegrityFilterCapability(),
                LifecycleEventCapability(),
                SteeringCapability(),
                ModelContextCoordinatorCapability(),
                ResolveModelId(resolve_model),
                *_cold_start_capabilities(construction_spec),
                *authored_capabilities,
                *default_model_costs,
                ModelRequestHeadersCapability(self._model_request_patch_configuration),
                *structured_output_capabilities,
                UsageCapability(),
            )
            definition_reserved_ids = definition_reserved_ids | _first_party_spec_reserved_ids(construction_spec)
            complete_output = [business_output, DeferredToolRequests]
            system_prompt = _normalize_system_prompt(construction_spec)
            agent = Agent.from_spec(
                construction_spec,
                deps_type=AgentContext,
                system_prompt=system_prompt,
                custom_capability_types=(
                    *first_party_declarative_capability_types(),
                    *self._capability_type_catalog.custom_capability_types,
                ),
                model=definition_model,
                output_type=complete_output,
                capabilities=capabilities,
                defer_model_check=True,
            )
            agent.instrument = False
            _validate_built_capability_tree(
                agent.root_capability,
                definition_reserved_ids=definition_reserved_ids,
                expected_instrumentation=self._observation.pydantic_instrumentation,
                expected_structured_output_compatibility=bool(structured_output_capabilities),
            )
        except Exception as exc:
            if isinstance(exc, HarnessError):
                raise
            raise DefinitionError(
                "Pydantic AI Agent construction failed.",
                code="agent_build_failed",
                details={"definition_id": definition.definition_id},
            ) from exc
        return ExecutableAgent(
            definition=definition,
            agent=cast(Agent[AgentContext, BuildOutputT | DeferredToolRequests], agent),
            system_prompt=system_prompt,
            output_adapter=output_adapter,
            plugins=plugins,
            subagents=subagents,
            definition_reserved_capability_ids=definition_reserved_ids,
            model_inference=resolve_model,
            observation=self._observation,
        )

    def _create_configured_plugins(self) -> tuple[AbstractHarnessPlugin, ...]:
        catalog = self._plugin_factory_catalog
        if catalog is None:
            return ()
        configuration = self._build_context.plugin_configuration
        if not isinstance(configuration, HarnessPluginConfiguration):
            raise PluginError(
                "Enabled configured plugins require a configuration document.",
                code="plugin_configuration_invalid",
            )
        return tuple(
            catalog.create_plugin(
                HarnessPluginFactoryContext(
                    plugin_key=entry.plugin_key,
                    plugin_id=entry.plugin_id,
                    configuration=catalog.validate_configuration(
                        entry.plugin_key,
                        entry.configuration,
                    ),
                    extensions=self._build_context.extensions,
                )
            )
            for entry in configuration.enabled_plugins
        )


def _first_party_spec_reserved_ids(spec: AgentSpec) -> frozenset[str]:
    """Authorize reserved definition IDs selected by exact first-party wire names."""
    names = [capability.name for capability in spec.capabilities]
    selected: set[str] = set()
    for capability_type in (ToolPermissionsCapability,):
        name = capability_type.get_serialization_name()
        if name is None:
            raise AssertionError(f"{capability_type.__name__} must be serializable")
        count = names.count(name)
        if count > 1:
            raise DefinitionError(
                f"AgentSpec contains duplicate {name} declarations.",
                code="capability_id_duplicate",
                details={"capability_id": capability_type.id, "source": "definition"},
            )
        if count:
            assert capability_type.id is not None
            selected.add(capability_type.id)
    return frozenset(selected)


def _resolve_business_output[OutputT](
    definition: AgentDefinition[OutputT],
) -> tuple[AgentSpec, Any, TypeAdapter[Any]]:
    """Resolve and freeze the one business-output source used for Agent construction."""
    construction_spec = definition.agent.model_copy(deep=True)
    if definition.output_type is not None:
        business_output = _prepare_pydantic_output_spec(definition.output_type)
        return construction_spec, business_output, _build_output_adapter(definition.output_type)

    schema = construction_spec.output_schema
    assert schema is not None
    try:
        business_output = structured_output_type(schema)
        output_adapter = TypeAdapter(business_output)
    except Exception as exc:
        raise DefinitionError(
            "AgentSpec.output_schema is not a valid native structured object schema.",
            code="agent_build_failed",
            details={"definition_id": definition.definition_id},
        ) from exc
    construction_spec = construction_spec.model_copy(update={"output_schema": None})
    return construction_spec, business_output, output_adapter

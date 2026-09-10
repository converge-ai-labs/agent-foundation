from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.child_environment_policy import ChildEnvironmentPolicy
    from ..models.delegation_context_policy import DelegationContextPolicy
    from ..models.usage_limits_output import UsageLimitsOutput


T = TypeVar("T", bound="SubagentOverrideOutput")


@_attrs_define(repr=False)
class SubagentOverrideOutput:
    """
    Attributes:
        agent_id (None | str | Unset):
        context (DelegationContextPolicy | None | Unset):
        description (None | str | Unset):
        environment (ChildEnvironmentPolicy | None | Unset):
        usage_limits (None | Unset | UsageLimitsOutput):
        version (int | None | Unset):
    """

    agent_id: str | Unset | None = UNSET
    context: DelegationContextPolicy | Unset | None = UNSET
    description: str | Unset | None = UNSET
    environment: ChildEnvironmentPolicy | Unset | None = UNSET
    usage_limits: Unset | UsageLimitsOutput | None = UNSET
    version: int | Unset | None = UNSET

    def to_dict(self) -> dict[str, Any]:
        from ..models.child_environment_policy import ChildEnvironmentPolicy
        from ..models.delegation_context_policy import DelegationContextPolicy
        from ..models.usage_limits_output import UsageLimitsOutput

        agent_id: str | Unset | None
        if isinstance(self.agent_id, Unset):
            agent_id = UNSET
        else:
            agent_id = self.agent_id

        context: dict[str, Any] | Unset | None
        if isinstance(self.context, Unset):
            context = UNSET
        elif isinstance(self.context, DelegationContextPolicy):
            context = self.context.to_dict()
        else:
            context = self.context

        description: str | Unset | None
        if isinstance(self.description, Unset):
            description = UNSET
        else:
            description = self.description

        environment: dict[str, Any] | Unset | None
        if isinstance(self.environment, Unset):
            environment = UNSET
        elif isinstance(self.environment, ChildEnvironmentPolicy):
            environment = self.environment.to_dict()
        else:
            environment = self.environment

        usage_limits: dict[str, Any] | Unset | None
        if isinstance(self.usage_limits, Unset):
            usage_limits = UNSET
        elif isinstance(self.usage_limits, UsageLimitsOutput):
            usage_limits = self.usage_limits.to_dict()
        else:
            usage_limits = self.usage_limits

        version: int | Unset | None
        if isinstance(self.version, Unset):
            version = UNSET
        else:
            version = self.version

        field_dict: dict[str, Any] = {}

        field_dict.update({})
        if agent_id is not UNSET:
            field_dict["agent_id"] = agent_id
        if context is not UNSET:
            field_dict["context"] = context
        if description is not UNSET:
            field_dict["description"] = description
        if environment is not UNSET:
            field_dict["environment"] = environment
        if usage_limits is not UNSET:
            field_dict["usage_limits"] = usage_limits
        if version is not UNSET:
            field_dict["version"] = version

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.child_environment_policy import ChildEnvironmentPolicy
        from ..models.delegation_context_policy import DelegationContextPolicy
        from ..models.usage_limits_output import UsageLimitsOutput

        d = dict(src_dict)

        def _parse_agent_id(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        agent_id = _parse_agent_id(d.pop("agent_id", UNSET))

        def _parse_context(data: object) -> DelegationContextPolicy | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                context_type_0 = DelegationContextPolicy.from_dict(data)

                return context_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(DelegationContextPolicy | Unset | None, data)

        context = _parse_context(d.pop("context", UNSET))

        def _parse_description(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        description = _parse_description(d.pop("description", UNSET))

        def _parse_environment(data: object) -> ChildEnvironmentPolicy | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                environment_type_0 = ChildEnvironmentPolicy.from_dict(data)

                return environment_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(ChildEnvironmentPolicy | Unset | None, data)

        environment = _parse_environment(d.pop("environment", UNSET))

        def _parse_usage_limits(data: object) -> Unset | UsageLimitsOutput | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                usage_limits_type_0 = UsageLimitsOutput.from_dict(data)

                return usage_limits_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(Unset | UsageLimitsOutput | None, data)

        usage_limits = _parse_usage_limits(d.pop("usage_limits", UNSET))

        def _parse_version(data: object) -> int | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | Unset | None, data)

        version = _parse_version(d.pop("version", UNSET))

        subagent_override_output = cls(
            agent_id=agent_id,
            context=context,
            description=description,
            environment=environment,
            usage_limits=usage_limits,
            version=version,
        )

        return subagent_override_output

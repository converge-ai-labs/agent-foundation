from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.child_environment_policy import ChildEnvironmentPolicy
    from ..models.delegation_context_policy import DelegationContextPolicy
    from ..models.usage_limits_output import UsageLimitsOutput


T = TypeVar("T", bound="ResolvedSubagentEdge")


@_attrs_define(repr=False)
class ResolvedSubagentEdge:
    """
    Attributes:
        child_agent_id (str):
        child_agent_revision_id (str):
        context (DelegationContextPolicy):
        environment (ChildEnvironmentPolicy):
        name (str):
        description (None | str | Unset):
        usage_limits (None | Unset | UsageLimitsOutput):
    """

    child_agent_id: str
    child_agent_revision_id: str
    context: DelegationContextPolicy
    environment: ChildEnvironmentPolicy
    name: str
    description: str | Unset | None = UNSET
    usage_limits: Unset | UsageLimitsOutput | None = UNSET

    def to_dict(self) -> dict[str, Any]:
        from ..models.usage_limits_output import UsageLimitsOutput

        child_agent_id = self.child_agent_id

        child_agent_revision_id = self.child_agent_revision_id

        context = self.context.to_dict()

        environment = self.environment.to_dict()

        name = self.name

        description: str | Unset | None
        if isinstance(self.description, Unset):
            description = UNSET
        else:
            description = self.description

        usage_limits: dict[str, Any] | Unset | None
        if isinstance(self.usage_limits, Unset):
            usage_limits = UNSET
        elif isinstance(self.usage_limits, UsageLimitsOutput):
            usage_limits = self.usage_limits.to_dict()
        else:
            usage_limits = self.usage_limits

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "child_agent_id": child_agent_id,
                "child_agent_revision_id": child_agent_revision_id,
                "context": context,
                "environment": environment,
                "name": name,
            }
        )
        if description is not UNSET:
            field_dict["description"] = description
        if usage_limits is not UNSET:
            field_dict["usage_limits"] = usage_limits

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.child_environment_policy import ChildEnvironmentPolicy
        from ..models.delegation_context_policy import DelegationContextPolicy
        from ..models.usage_limits_output import UsageLimitsOutput

        d = dict(src_dict)
        child_agent_id = d.pop("child_agent_id")

        child_agent_revision_id = d.pop("child_agent_revision_id")

        context = DelegationContextPolicy.from_dict(d.pop("context"))

        environment = ChildEnvironmentPolicy.from_dict(d.pop("environment"))

        name = d.pop("name")

        def _parse_description(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        description = _parse_description(d.pop("description", UNSET))

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

        resolved_subagent_edge = cls(
            child_agent_id=child_agent_id,
            child_agent_revision_id=child_agent_revision_id,
            context=context,
            environment=environment,
            name=name,
            description=description,
            usage_limits=usage_limits,
        )

        return resolved_subagent_edge

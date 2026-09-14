from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.agent_input import AgentInput
    from ..models.agent_run_override_input import AgentRunOverrideInput
    from ..models.existing_environment_selection import ExistingEnvironmentSelection
    from ..models.fork_run_request_labels import ForkRunRequestLabels
    from ..models.fork_run_request_thread_labels import ForkRunRequestThreadLabels
    from ..models.inline_hook_subscription_input import InlineHookSubscriptionInput
    from ..models.new_environment_selection import NewEnvironmentSelection


T = TypeVar("T", bound="ForkRunRequest")


@_attrs_define(repr=False)
class ForkRunRequest:
    """
    Attributes:
        input_ (AgentInput): Submitted or retained versioned ordinary Agent input.
        agent_id (None | str | Unset):
        agent_revision_id (None | str | Unset):
        config_override (AgentRunOverrideInput | None | Unset):
        environment (ExistingEnvironmentSelection | NewEnvironmentSelection | None | Unset):
        expected_current_revision_id (None | str | Unset):
        hook_subscription (InlineHookSubscriptionInput | None | Unset):
        labels (ForkRunRequestLabels | Unset):
        thread_labels (ForkRunRequestThreadLabels | Unset):
    """

    input_: AgentInput
    agent_id: str | Unset | None = UNSET
    agent_revision_id: str | Unset | None = UNSET
    config_override: AgentRunOverrideInput | Unset | None = UNSET
    environment: ExistingEnvironmentSelection | NewEnvironmentSelection | Unset | None = UNSET
    expected_current_revision_id: str | Unset | None = UNSET
    hook_subscription: InlineHookSubscriptionInput | Unset | None = UNSET
    labels: ForkRunRequestLabels | Unset = UNSET
    thread_labels: ForkRunRequestThreadLabels | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        from ..models.agent_run_override_input import AgentRunOverrideInput
        from ..models.existing_environment_selection import ExistingEnvironmentSelection
        from ..models.inline_hook_subscription_input import InlineHookSubscriptionInput
        from ..models.new_environment_selection import NewEnvironmentSelection

        input_ = self.input_.to_dict()

        agent_id: str | Unset | None
        if isinstance(self.agent_id, Unset):
            agent_id = UNSET
        else:
            agent_id = self.agent_id

        agent_revision_id: str | Unset | None
        if isinstance(self.agent_revision_id, Unset):
            agent_revision_id = UNSET
        else:
            agent_revision_id = self.agent_revision_id

        config_override: dict[str, Any] | Unset | None
        if isinstance(self.config_override, Unset):
            config_override = UNSET
        elif isinstance(self.config_override, AgentRunOverrideInput):
            config_override = self.config_override.to_dict()
        else:
            config_override = self.config_override

        environment: dict[str, Any] | Unset | None
        if isinstance(self.environment, Unset):
            environment = UNSET
        elif isinstance(self.environment, ExistingEnvironmentSelection):
            environment = self.environment.to_dict()
        elif isinstance(self.environment, NewEnvironmentSelection):
            environment = self.environment.to_dict()
        else:
            environment = self.environment

        expected_current_revision_id: str | Unset | None
        if isinstance(self.expected_current_revision_id, Unset):
            expected_current_revision_id = UNSET
        else:
            expected_current_revision_id = self.expected_current_revision_id

        hook_subscription: dict[str, Any] | Unset | None
        if isinstance(self.hook_subscription, Unset):
            hook_subscription = UNSET
        elif isinstance(self.hook_subscription, InlineHookSubscriptionInput):
            hook_subscription = self.hook_subscription.to_dict()
        else:
            hook_subscription = self.hook_subscription

        labels: dict[str, Any] | Unset = UNSET
        if not isinstance(self.labels, Unset):
            labels = self.labels.to_dict()

        thread_labels: dict[str, Any] | Unset = UNSET
        if not isinstance(self.thread_labels, Unset):
            thread_labels = self.thread_labels.to_dict()

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "input": input_,
            }
        )
        if agent_id is not UNSET:
            field_dict["agent_id"] = agent_id
        if agent_revision_id is not UNSET:
            field_dict["agent_revision_id"] = agent_revision_id
        if config_override is not UNSET:
            field_dict["config_override"] = config_override
        if environment is not UNSET:
            field_dict["environment"] = environment
        if expected_current_revision_id is not UNSET:
            field_dict["expected_current_revision_id"] = expected_current_revision_id
        if hook_subscription is not UNSET:
            field_dict["hook_subscription"] = hook_subscription
        if labels is not UNSET:
            field_dict["labels"] = labels
        if thread_labels is not UNSET:
            field_dict["thread_labels"] = thread_labels

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.agent_input import AgentInput
        from ..models.agent_run_override_input import AgentRunOverrideInput
        from ..models.existing_environment_selection import ExistingEnvironmentSelection
        from ..models.fork_run_request_labels import ForkRunRequestLabels
        from ..models.fork_run_request_thread_labels import ForkRunRequestThreadLabels
        from ..models.inline_hook_subscription_input import InlineHookSubscriptionInput
        from ..models.new_environment_selection import NewEnvironmentSelection

        d = dict(src_dict)
        input_ = AgentInput.from_dict(d.pop("input"))

        def _parse_agent_id(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        agent_id = _parse_agent_id(d.pop("agent_id", UNSET))

        def _parse_agent_revision_id(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        agent_revision_id = _parse_agent_revision_id(d.pop("agent_revision_id", UNSET))

        def _parse_config_override(data: object) -> AgentRunOverrideInput | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                config_override_type_0 = AgentRunOverrideInput.from_dict(data)

                return config_override_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(AgentRunOverrideInput | Unset | None, data)

        config_override = _parse_config_override(d.pop("config_override", UNSET))

        def _parse_environment(data: object) -> ExistingEnvironmentSelection | NewEnvironmentSelection | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                componentsschemas_environment_selection_type_0 = ExistingEnvironmentSelection.from_dict(data)

                return componentsschemas_environment_selection_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                componentsschemas_environment_selection_type_1 = NewEnvironmentSelection.from_dict(data)

                return componentsschemas_environment_selection_type_1
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(ExistingEnvironmentSelection | NewEnvironmentSelection | Unset | None, data)

        environment = _parse_environment(d.pop("environment", UNSET))

        def _parse_expected_current_revision_id(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        expected_current_revision_id = _parse_expected_current_revision_id(d.pop("expected_current_revision_id", UNSET))

        def _parse_hook_subscription(data: object) -> InlineHookSubscriptionInput | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                hook_subscription_type_0 = InlineHookSubscriptionInput.from_dict(data)

                return hook_subscription_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(InlineHookSubscriptionInput | Unset | None, data)

        hook_subscription = _parse_hook_subscription(d.pop("hook_subscription", UNSET))

        _labels = d.pop("labels", UNSET)
        labels: ForkRunRequestLabels | Unset
        if isinstance(_labels, Unset):
            labels = UNSET
        else:
            labels = ForkRunRequestLabels.from_dict(_labels)

        _thread_labels = d.pop("thread_labels", UNSET)
        thread_labels: ForkRunRequestThreadLabels | Unset
        if isinstance(_thread_labels, Unset):
            thread_labels = UNSET
        else:
            thread_labels = ForkRunRequestThreadLabels.from_dict(_thread_labels)

        fork_run_request = cls(
            input_=input_,
            agent_id=agent_id,
            agent_revision_id=agent_revision_id,
            config_override=config_override,
            environment=environment,
            expected_current_revision_id=expected_current_revision_id,
            hook_subscription=hook_subscription,
            labels=labels,
            thread_labels=thread_labels,
        )

        return fork_run_request

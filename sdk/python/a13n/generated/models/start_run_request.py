from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.agent_input import AgentInput
    from ..models.agent_run_override_input import AgentRunOverrideInput
    from ..models.existing_environment_selection import ExistingEnvironmentSelection
    from ..models.inline_hook_subscription_input import InlineHookSubscriptionInput
    from ..models.new_environment_selection import NewEnvironmentSelection
    from ..models.start_run_request_labels import StartRunRequestLabels
    from ..models.start_run_request_session_labels import StartRunRequestSessionLabels
    from ..models.start_run_request_thread_labels import StartRunRequestThreadLabels


T = TypeVar("T", bound="StartRunRequest")


@_attrs_define(repr=False)
class StartRunRequest:
    """
    Attributes:
        agent_id (str):
        input_ (AgentInput): Submitted or retained versioned ordinary Agent input.
        agent_revision_id (None | str | Unset):
        config_override (AgentRunOverrideInput | None | Unset):
        environment (ExistingEnvironmentSelection | NewEnvironmentSelection | None | Unset):
        expected_current_revision_id (None | str | Unset):
        hook_subscription (InlineHookSubscriptionInput | None | Unset):
        labels (StartRunRequestLabels | Unset):
        session_id (None | str | Unset):
        session_labels (StartRunRequestSessionLabels | Unset):
        thread_labels (StartRunRequestThreadLabels | Unset):
    """

    agent_id: str
    input_: AgentInput
    agent_revision_id: str | Unset | None = UNSET
    config_override: AgentRunOverrideInput | Unset | None = UNSET
    environment: ExistingEnvironmentSelection | NewEnvironmentSelection | Unset | None = UNSET
    expected_current_revision_id: str | Unset | None = UNSET
    hook_subscription: InlineHookSubscriptionInput | Unset | None = UNSET
    labels: StartRunRequestLabels | Unset = UNSET
    session_id: str | Unset | None = UNSET
    session_labels: StartRunRequestSessionLabels | Unset = UNSET
    thread_labels: StartRunRequestThreadLabels | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        from ..models.agent_run_override_input import AgentRunOverrideInput
        from ..models.existing_environment_selection import ExistingEnvironmentSelection
        from ..models.inline_hook_subscription_input import InlineHookSubscriptionInput
        from ..models.new_environment_selection import NewEnvironmentSelection

        agent_id = self.agent_id

        input_ = self.input_.to_dict()

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

        session_id: str | Unset | None
        if isinstance(self.session_id, Unset):
            session_id = UNSET
        else:
            session_id = self.session_id

        session_labels: dict[str, Any] | Unset = UNSET
        if not isinstance(self.session_labels, Unset):
            session_labels = self.session_labels.to_dict()

        thread_labels: dict[str, Any] | Unset = UNSET
        if not isinstance(self.thread_labels, Unset):
            thread_labels = self.thread_labels.to_dict()

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "agent_id": agent_id,
                "input": input_,
            }
        )
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
        if session_id is not UNSET:
            field_dict["session_id"] = session_id
        if session_labels is not UNSET:
            field_dict["session_labels"] = session_labels
        if thread_labels is not UNSET:
            field_dict["thread_labels"] = thread_labels

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.agent_input import AgentInput
        from ..models.agent_run_override_input import AgentRunOverrideInput
        from ..models.existing_environment_selection import ExistingEnvironmentSelection
        from ..models.inline_hook_subscription_input import InlineHookSubscriptionInput
        from ..models.new_environment_selection import NewEnvironmentSelection
        from ..models.start_run_request_labels import StartRunRequestLabels
        from ..models.start_run_request_session_labels import StartRunRequestSessionLabels
        from ..models.start_run_request_thread_labels import StartRunRequestThreadLabels

        d = dict(src_dict)
        agent_id = d.pop("agent_id")

        input_ = AgentInput.from_dict(d.pop("input"))

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
        labels: StartRunRequestLabels | Unset
        if isinstance(_labels, Unset):
            labels = UNSET
        else:
            labels = StartRunRequestLabels.from_dict(_labels)

        def _parse_session_id(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        session_id = _parse_session_id(d.pop("session_id", UNSET))

        _session_labels = d.pop("session_labels", UNSET)
        session_labels: StartRunRequestSessionLabels | Unset
        if isinstance(_session_labels, Unset):
            session_labels = UNSET
        else:
            session_labels = StartRunRequestSessionLabels.from_dict(_session_labels)

        _thread_labels = d.pop("thread_labels", UNSET)
        thread_labels: StartRunRequestThreadLabels | Unset
        if isinstance(_thread_labels, Unset):
            thread_labels = UNSET
        else:
            thread_labels = StartRunRequestThreadLabels.from_dict(_thread_labels)

        start_run_request = cls(
            agent_id=agent_id,
            input_=input_,
            agent_revision_id=agent_revision_id,
            config_override=config_override,
            environment=environment,
            expected_current_revision_id=expected_current_revision_id,
            hook_subscription=hook_subscription,
            labels=labels,
            session_id=session_id,
            session_labels=session_labels,
            thread_labels=thread_labels,
        )

        return start_run_request

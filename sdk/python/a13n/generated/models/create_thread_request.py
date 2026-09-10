from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.existing_environment_selection import ExistingEnvironmentSelection
    from ..models.new_environment_selection import NewEnvironmentSelection


T = TypeVar("T", bound="CreateThreadRequest")


@_attrs_define(repr=False)
class CreateThreadRequest:
    """
    Attributes:
        agent_id (None | str | Unset):
        environment (ExistingEnvironmentSelection | NewEnvironmentSelection | None | Unset):
        session_id (None | str | Unset):
    """

    agent_id: str | Unset | None = UNSET
    environment: ExistingEnvironmentSelection | NewEnvironmentSelection | Unset | None = UNSET
    session_id: str | Unset | None = UNSET

    def to_dict(self) -> dict[str, Any]:
        from ..models.existing_environment_selection import ExistingEnvironmentSelection
        from ..models.new_environment_selection import NewEnvironmentSelection

        agent_id: str | Unset | None
        if isinstance(self.agent_id, Unset):
            agent_id = UNSET
        else:
            agent_id = self.agent_id

        environment: dict[str, Any] | Unset | None
        if isinstance(self.environment, Unset):
            environment = UNSET
        elif isinstance(self.environment, ExistingEnvironmentSelection):
            environment = self.environment.to_dict()
        elif isinstance(self.environment, NewEnvironmentSelection):
            environment = self.environment.to_dict()
        else:
            environment = self.environment

        session_id: str | Unset | None
        if isinstance(self.session_id, Unset):
            session_id = UNSET
        else:
            session_id = self.session_id

        field_dict: dict[str, Any] = {}

        field_dict.update({})
        if agent_id is not UNSET:
            field_dict["agent_id"] = agent_id
        if environment is not UNSET:
            field_dict["environment"] = environment
        if session_id is not UNSET:
            field_dict["session_id"] = session_id

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.existing_environment_selection import ExistingEnvironmentSelection
        from ..models.new_environment_selection import NewEnvironmentSelection

        d = dict(src_dict)

        def _parse_agent_id(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        agent_id = _parse_agent_id(d.pop("agent_id", UNSET))

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

        def _parse_session_id(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        session_id = _parse_session_id(d.pop("session_id", UNSET))

        create_thread_request = cls(
            agent_id=agent_id,
            environment=environment,
            session_id=session_id,
        )

        return create_thread_request

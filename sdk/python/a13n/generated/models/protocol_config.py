from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Literal, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.a2a_skill_projection import A2ASkillProjection
    from ..models.client_tool_policy import ClientToolPolicy
    from ..models.extended_agent_card_policy import ExtendedAgentCardPolicy
    from ..models.protocol_config_context_schema_type_0 import ProtocolConfigContextSchemaType0
    from ..models.protocol_config_input_data_schema_type_0 import ProtocolConfigInputDataSchemaType0
    from ..models.protocol_config_state_schema_type_0 import ProtocolConfigStateSchemaType0
    from ..models.protocol_limits import ProtocolLimits


T = TypeVar("T", bound="ProtocolConfig")


@_attrs_define(repr=False)
class ProtocolConfig:
    """
    Attributes:
        public_name (str):
        a2a_skills (list[A2ASkillProjection] | Unset):
        client_tools (list[ClientToolPolicy] | Unset):
        context_schema (None | ProtocolConfigContextSchemaType0 | Unset):
        event_visibility (list[str] | Unset):
        extended_agent_card (ExtendedAgentCardPolicy | None | Unset):
        input_data_schema (None | ProtocolConfigInputDataSchemaType0 | Unset):
        limits (ProtocolLimits | Unset):
        output_modes (list[str] | Unset):
        public_description (None | str | Unset):
        schema_version (Literal['1'] | Unset):
        state_schema (None | ProtocolConfigStateSchemaType0 | Unset):
    """

    public_name: str
    a2a_skills: list[A2ASkillProjection] | Unset = UNSET
    client_tools: list[ClientToolPolicy] | Unset = UNSET
    context_schema: ProtocolConfigContextSchemaType0 | Unset | None = UNSET
    event_visibility: list[str] | Unset = UNSET
    extended_agent_card: ExtendedAgentCardPolicy | Unset | None = UNSET
    input_data_schema: ProtocolConfigInputDataSchemaType0 | Unset | None = UNSET
    limits: ProtocolLimits | Unset = UNSET
    output_modes: list[str] | Unset = UNSET
    public_description: str | Unset | None = UNSET
    schema_version: Literal["1"] | Unset = UNSET
    state_schema: ProtocolConfigStateSchemaType0 | Unset | None = UNSET

    def to_dict(self) -> dict[str, Any]:
        from ..models.extended_agent_card_policy import ExtendedAgentCardPolicy
        from ..models.protocol_config_context_schema_type_0 import ProtocolConfigContextSchemaType0
        from ..models.protocol_config_input_data_schema_type_0 import ProtocolConfigInputDataSchemaType0
        from ..models.protocol_config_state_schema_type_0 import ProtocolConfigStateSchemaType0

        public_name = self.public_name

        a2a_skills: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.a2a_skills, Unset):
            a2a_skills = []
            for a2a_skills_item_data in self.a2a_skills:
                a2a_skills_item = a2a_skills_item_data.to_dict()
                a2a_skills.append(a2a_skills_item)

        client_tools: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.client_tools, Unset):
            client_tools = []
            for client_tools_item_data in self.client_tools:
                client_tools_item = client_tools_item_data.to_dict()
                client_tools.append(client_tools_item)

        context_schema: dict[str, Any] | Unset | None
        if isinstance(self.context_schema, Unset):
            context_schema = UNSET
        elif isinstance(self.context_schema, ProtocolConfigContextSchemaType0):
            context_schema = self.context_schema.to_dict()
        else:
            context_schema = self.context_schema

        event_visibility: list[str] | Unset = UNSET
        if not isinstance(self.event_visibility, Unset):
            event_visibility = self.event_visibility

        extended_agent_card: dict[str, Any] | Unset | None
        if isinstance(self.extended_agent_card, Unset):
            extended_agent_card = UNSET
        elif isinstance(self.extended_agent_card, ExtendedAgentCardPolicy):
            extended_agent_card = self.extended_agent_card.to_dict()
        else:
            extended_agent_card = self.extended_agent_card

        input_data_schema: dict[str, Any] | Unset | None
        if isinstance(self.input_data_schema, Unset):
            input_data_schema = UNSET
        elif isinstance(self.input_data_schema, ProtocolConfigInputDataSchemaType0):
            input_data_schema = self.input_data_schema.to_dict()
        else:
            input_data_schema = self.input_data_schema

        limits: dict[str, Any] | Unset = UNSET
        if not isinstance(self.limits, Unset):
            limits = self.limits.to_dict()

        output_modes: list[str] | Unset = UNSET
        if not isinstance(self.output_modes, Unset):
            output_modes = self.output_modes

        public_description: str | Unset | None
        if isinstance(self.public_description, Unset):
            public_description = UNSET
        else:
            public_description = self.public_description

        schema_version = self.schema_version

        state_schema: dict[str, Any] | Unset | None
        if isinstance(self.state_schema, Unset):
            state_schema = UNSET
        elif isinstance(self.state_schema, ProtocolConfigStateSchemaType0):
            state_schema = self.state_schema.to_dict()
        else:
            state_schema = self.state_schema

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "public_name": public_name,
            }
        )
        if a2a_skills is not UNSET:
            field_dict["a2a_skills"] = a2a_skills
        if client_tools is not UNSET:
            field_dict["client_tools"] = client_tools
        if context_schema is not UNSET:
            field_dict["context_schema"] = context_schema
        if event_visibility is not UNSET:
            field_dict["event_visibility"] = event_visibility
        if extended_agent_card is not UNSET:
            field_dict["extended_agent_card"] = extended_agent_card
        if input_data_schema is not UNSET:
            field_dict["input_data_schema"] = input_data_schema
        if limits is not UNSET:
            field_dict["limits"] = limits
        if output_modes is not UNSET:
            field_dict["output_modes"] = output_modes
        if public_description is not UNSET:
            field_dict["public_description"] = public_description
        if schema_version is not UNSET:
            field_dict["schema_version"] = schema_version
        if state_schema is not UNSET:
            field_dict["state_schema"] = state_schema

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.a2a_skill_projection import A2ASkillProjection
        from ..models.client_tool_policy import ClientToolPolicy
        from ..models.extended_agent_card_policy import ExtendedAgentCardPolicy
        from ..models.protocol_config_context_schema_type_0 import ProtocolConfigContextSchemaType0
        from ..models.protocol_config_input_data_schema_type_0 import (
            ProtocolConfigInputDataSchemaType0,
        )
        from ..models.protocol_config_state_schema_type_0 import ProtocolConfigStateSchemaType0
        from ..models.protocol_limits import ProtocolLimits

        d = dict(src_dict)
        public_name = d.pop("public_name")

        _a2a_skills = d.pop("a2a_skills", UNSET)
        a2a_skills: list[A2ASkillProjection] | Unset = UNSET
        if _a2a_skills is not UNSET:
            a2a_skills = []
            for a2a_skills_item_data in _a2a_skills:
                a2a_skills_item = A2ASkillProjection.from_dict(a2a_skills_item_data)

                a2a_skills.append(a2a_skills_item)

        _client_tools = d.pop("client_tools", UNSET)
        client_tools: list[ClientToolPolicy] | Unset = UNSET
        if _client_tools is not UNSET:
            client_tools = []
            for client_tools_item_data in _client_tools:
                client_tools_item = ClientToolPolicy.from_dict(client_tools_item_data)

                client_tools.append(client_tools_item)

        def _parse_context_schema(data: object) -> ProtocolConfigContextSchemaType0 | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                context_schema_type_0 = ProtocolConfigContextSchemaType0.from_dict(data)

                return context_schema_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(ProtocolConfigContextSchemaType0 | Unset | None, data)

        context_schema = _parse_context_schema(d.pop("context_schema", UNSET))

        event_visibility = cast(list[str], d.pop("event_visibility", UNSET))

        def _parse_extended_agent_card(data: object) -> ExtendedAgentCardPolicy | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                extended_agent_card_type_0 = ExtendedAgentCardPolicy.from_dict(data)

                return extended_agent_card_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(ExtendedAgentCardPolicy | Unset | None, data)

        extended_agent_card = _parse_extended_agent_card(d.pop("extended_agent_card", UNSET))

        def _parse_input_data_schema(data: object) -> ProtocolConfigInputDataSchemaType0 | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                input_data_schema_type_0 = ProtocolConfigInputDataSchemaType0.from_dict(data)

                return input_data_schema_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(ProtocolConfigInputDataSchemaType0 | Unset | None, data)

        input_data_schema = _parse_input_data_schema(d.pop("input_data_schema", UNSET))

        _limits = d.pop("limits", UNSET)
        limits: ProtocolLimits | Unset
        if isinstance(_limits, Unset):
            limits = UNSET
        else:
            limits = ProtocolLimits.from_dict(_limits)

        output_modes = cast(list[str], d.pop("output_modes", UNSET))

        def _parse_public_description(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        public_description = _parse_public_description(d.pop("public_description", UNSET))

        schema_version = cast(Literal["1"] | Unset, d.pop("schema_version", UNSET))
        if schema_version != "1" and not isinstance(schema_version, Unset):
            raise ValueError(f"schema_version must match const '1', got '{schema_version}'")

        def _parse_state_schema(data: object) -> ProtocolConfigStateSchemaType0 | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                state_schema_type_0 = ProtocolConfigStateSchemaType0.from_dict(data)

                return state_schema_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(ProtocolConfigStateSchemaType0 | Unset | None, data)

        state_schema = _parse_state_schema(d.pop("state_schema", UNSET))

        protocol_config = cls(
            public_name=public_name,
            a2a_skills=a2a_skills,
            client_tools=client_tools,
            context_schema=context_schema,
            event_visibility=event_visibility,
            extended_agent_card=extended_agent_card,
            input_data_schema=input_data_schema,
            limits=limits,
            output_modes=output_modes,
            public_description=public_description,
            schema_version=schema_version,
            state_schema=state_schema,
        )

        return protocol_config

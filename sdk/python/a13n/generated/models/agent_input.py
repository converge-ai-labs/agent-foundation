from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..models.agent_input_schema_version import AgentInputSchemaVersion
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.agent_secret_binding import AgentSecretBinding
    from ..models.binary_content import BinaryContent
    from ..models.text_content import TextContent


T = TypeVar("T", bound="AgentInput")


@_attrs_define(repr=False)
class AgentInput:
    """Submitted or retained versioned ordinary Agent input.

    Attributes:
        schema_version (AgentInputSchemaVersion):
        content (list[BinaryContent | TextContent] | Unset):
        secret_bindings (list[AgentSecretBinding] | Unset):
        structured_content (Any | None | Unset):
    """

    schema_version: AgentInputSchemaVersion
    content: list[BinaryContent | TextContent] | Unset = UNSET
    secret_bindings: list[AgentSecretBinding] | Unset = UNSET
    structured_content: Any | Unset | None = UNSET

    def to_dict(self) -> dict[str, Any]:
        from ..models.text_content import TextContent

        schema_version = self.schema_version.value

        content: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.content, Unset):
            content = []
            for content_item_data in self.content:
                content_item: dict[str, Any]
                if isinstance(content_item_data, TextContent):
                    content_item = content_item_data.to_dict()
                else:
                    content_item = content_item_data.to_dict()

                content.append(content_item)

        secret_bindings: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.secret_bindings, Unset):
            secret_bindings = []
            for secret_bindings_item_data in self.secret_bindings:
                secret_bindings_item = secret_bindings_item_data.to_dict()
                secret_bindings.append(secret_bindings_item)

        structured_content: Any | Unset | None
        if isinstance(self.structured_content, Unset):
            structured_content = UNSET
        else:
            structured_content = self.structured_content

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "schema_version": schema_version,
            }
        )
        if content is not UNSET:
            field_dict["content"] = content
        if secret_bindings is not UNSET:
            field_dict["secret_bindings"] = secret_bindings
        if structured_content is not UNSET:
            field_dict["structured_content"] = structured_content

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.agent_secret_binding import AgentSecretBinding
        from ..models.binary_content import BinaryContent
        from ..models.text_content import TextContent

        d = dict(src_dict)
        schema_version = AgentInputSchemaVersion(d.pop("schema_version"))

        _content = d.pop("content", UNSET)
        content: list[BinaryContent | TextContent] | Unset = UNSET
        if _content is not UNSET:
            content = []
            for content_item_data in _content:

                def _parse_content_item(data: object) -> BinaryContent | TextContent:
                    try:
                        if not isinstance(data, dict):
                            raise TypeError()
                        content_item_type_0 = TextContent.from_dict(data)

                        return content_item_type_0
                    except (TypeError, ValueError, AttributeError, KeyError):
                        pass
                    if not isinstance(data, dict):
                        raise TypeError()
                    content_item_type_1 = BinaryContent.from_dict(data)

                    return content_item_type_1

                content_item = _parse_content_item(content_item_data)

                content.append(content_item)

        _secret_bindings = d.pop("secret_bindings", UNSET)
        secret_bindings: list[AgentSecretBinding] | Unset = UNSET
        if _secret_bindings is not UNSET:
            secret_bindings = []
            for secret_bindings_item_data in _secret_bindings:
                secret_bindings_item = AgentSecretBinding.from_dict(secret_bindings_item_data)

                secret_bindings.append(secret_bindings_item)

        def _parse_structured_content(data: object) -> Any | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(Any | Unset | None, data)

        structured_content = _parse_structured_content(d.pop("structured_content", UNSET))

        agent_input = cls(
            schema_version=schema_version,
            content=content,
            secret_bindings=secret_bindings,
            structured_content=structured_content,
        )

        return agent_input

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define

from ..models.scope_audience import ScopeAudience
from ..types import UNSET, Unset

T = TypeVar("T", bound="Scope")


@_attrs_define(repr=False)
class Scope:
    """
    Attributes:
        account_id (str):
        audience (ScopeAudience):
        external_conversation_id (str):
        id (str):
        name (str):
        provider_id (str):
        version (int):
        enabled (bool | Unset):
        save_on_request (bool | Unset):
        timezone (str | Unset):
        use_memory (bool | Unset):
    """

    account_id: str
    audience: ScopeAudience
    external_conversation_id: str
    id: str
    name: str
    provider_id: str
    version: int
    enabled: bool | Unset = UNSET
    save_on_request: bool | Unset = UNSET
    timezone: str | Unset = UNSET
    use_memory: bool | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        account_id = self.account_id

        audience = self.audience.value

        external_conversation_id = self.external_conversation_id

        id = self.id

        name = self.name

        provider_id = self.provider_id

        version = self.version

        enabled = self.enabled

        save_on_request = self.save_on_request

        timezone = self.timezone

        use_memory = self.use_memory

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "account_id": account_id,
                "audience": audience,
                "external_conversation_id": external_conversation_id,
                "id": id,
                "name": name,
                "provider_id": provider_id,
                "version": version,
            }
        )
        if enabled is not UNSET:
            field_dict["enabled"] = enabled
        if save_on_request is not UNSET:
            field_dict["save_on_request"] = save_on_request
        if timezone is not UNSET:
            field_dict["timezone"] = timezone
        if use_memory is not UNSET:
            field_dict["use_memory"] = use_memory

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        account_id = d.pop("account_id")

        audience = ScopeAudience(d.pop("audience"))

        external_conversation_id = d.pop("external_conversation_id")

        id = d.pop("id")

        name = d.pop("name")

        provider_id = d.pop("provider_id")

        version = d.pop("version")

        enabled = d.pop("enabled", UNSET)

        save_on_request = d.pop("save_on_request", UNSET)

        timezone = d.pop("timezone", UNSET)

        use_memory = d.pop("use_memory", UNSET)

        scope = cls(
            account_id=account_id,
            audience=audience,
            external_conversation_id=external_conversation_id,
            id=id,
            name=name,
            provider_id=provider_id,
            version=version,
            enabled=enabled,
            save_on_request=save_on_request,
            timezone=timezone,
            use_memory=use_memory,
        )

        return scope

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..models.child_environment_policy_mode import ChildEnvironmentPolicyMode
from ..types import UNSET, Unset

T = TypeVar("T", bound="ChildEnvironmentPolicy")


@_attrs_define(repr=False)
class ChildEnvironmentPolicy:
    """
    Attributes:
        mode (ChildEnvironmentPolicyMode | Unset):
        template_revision_id (None | str | Unset):
    """

    mode: ChildEnvironmentPolicyMode | Unset = UNSET
    template_revision_id: str | Unset | None = UNSET

    def to_dict(self) -> dict[str, Any]:
        mode: str | Unset = UNSET
        if not isinstance(self.mode, Unset):
            mode = self.mode.value

        template_revision_id: str | Unset | None
        if isinstance(self.template_revision_id, Unset):
            template_revision_id = UNSET
        else:
            template_revision_id = self.template_revision_id

        field_dict: dict[str, Any] = {}

        field_dict.update({})
        if mode is not UNSET:
            field_dict["mode"] = mode
        if template_revision_id is not UNSET:
            field_dict["template_revision_id"] = template_revision_id

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        _mode = d.pop("mode", UNSET)
        mode: ChildEnvironmentPolicyMode | Unset
        if isinstance(_mode, Unset):
            mode = UNSET
        else:
            mode = ChildEnvironmentPolicyMode(_mode)

        def _parse_template_revision_id(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        template_revision_id = _parse_template_revision_id(d.pop("template_revision_id", UNSET))

        child_environment_policy = cls(
            mode=mode,
            template_revision_id=template_revision_id,
        )

        return child_environment_policy

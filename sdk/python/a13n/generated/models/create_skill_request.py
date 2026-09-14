from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.create_skill_request_labels import CreateSkillRequestLabels
    from ..models.git_hub_revision_source import GitHubRevisionSource
    from ..models.zip_upload_skill_source import ZipUploadSkillSource


T = TypeVar("T", bound="CreateSkillRequest")


@_attrs_define(repr=False)
class CreateSkillRequest:
    """
    Attributes:
        source (GitHubRevisionSource | ZipUploadSkillSource):
        labels (CreateSkillRequestLabels | Unset):
        name (None | str | Unset):
    """

    source: GitHubRevisionSource | ZipUploadSkillSource
    labels: CreateSkillRequestLabels | Unset = UNSET
    name: str | Unset | None = UNSET

    def to_dict(self) -> dict[str, Any]:
        from ..models.zip_upload_skill_source import ZipUploadSkillSource

        source: dict[str, Any]
        if isinstance(self.source, ZipUploadSkillSource):
            source = self.source.to_dict()
        else:
            source = self.source.to_dict()

        labels: dict[str, Any] | Unset = UNSET
        if not isinstance(self.labels, Unset):
            labels = self.labels.to_dict()

        name: str | Unset | None
        if isinstance(self.name, Unset):
            name = UNSET
        else:
            name = self.name

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "source": source,
            }
        )
        if labels is not UNSET:
            field_dict["labels"] = labels
        if name is not UNSET:
            field_dict["name"] = name

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.create_skill_request_labels import CreateSkillRequestLabels
        from ..models.git_hub_revision_source import GitHubRevisionSource
        from ..models.zip_upload_skill_source import ZipUploadSkillSource

        d = dict(src_dict)

        def _parse_source(data: object) -> GitHubRevisionSource | ZipUploadSkillSource:
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                source_type_0 = ZipUploadSkillSource.from_dict(data)

                return source_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            if not isinstance(data, dict):
                raise TypeError()
            source_type_1 = GitHubRevisionSource.from_dict(data)

            return source_type_1

        source = _parse_source(d.pop("source"))

        _labels = d.pop("labels", UNSET)
        labels: CreateSkillRequestLabels | Unset
        if isinstance(_labels, Unset):
            labels = UNSET
        else:
            labels = CreateSkillRequestLabels.from_dict(_labels)

        def _parse_name(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        name = _parse_name(d.pop("name", UNSET))

        create_skill_request = cls(
            source=source,
            labels=labels,
            name=name,
        )

        return create_skill_request

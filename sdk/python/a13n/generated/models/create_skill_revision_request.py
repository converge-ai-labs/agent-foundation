from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define

if TYPE_CHECKING:
    from ..models.git_hub_revision_source import GitHubRevisionSource
    from ..models.zip_upload_skill_source import ZipUploadSkillSource


T = TypeVar("T", bound="CreateSkillRevisionRequest")


@_attrs_define(repr=False)
class CreateSkillRevisionRequest:
    """
    Attributes:
        expected_version (int):
        source (GitHubRevisionSource | ZipUploadSkillSource):
    """

    expected_version: int
    source: GitHubRevisionSource | ZipUploadSkillSource

    def to_dict(self) -> dict[str, Any]:
        from ..models.zip_upload_skill_source import ZipUploadSkillSource

        expected_version = self.expected_version

        source: dict[str, Any]
        if isinstance(self.source, ZipUploadSkillSource):
            source = self.source.to_dict()
        else:
            source = self.source.to_dict()

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "expected_version": expected_version,
                "source": source,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.git_hub_revision_source import GitHubRevisionSource
        from ..models.zip_upload_skill_source import ZipUploadSkillSource

        d = dict(src_dict)
        expected_version = d.pop("expected_version")

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

        create_skill_revision_request = cls(
            expected_version=expected_version,
            source=source,
        )

        return create_skill_revision_request

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="GitHubSkillImportProvenance")


@_attrs_define(repr=False)
class GitHubSkillImportProvenance:
    """
    Attributes:
        repository_url (str):
        resolved_commit_sha (str):
        subdirectory (str):
        kind (Literal['github'] | Unset):
        requested_ref (None | str | Unset):
    """

    repository_url: str
    resolved_commit_sha: str
    subdirectory: str
    kind: Literal["github"] | Unset = UNSET
    requested_ref: str | Unset | None = UNSET

    def to_dict(self) -> dict[str, Any]:
        repository_url = self.repository_url

        resolved_commit_sha = self.resolved_commit_sha

        subdirectory = self.subdirectory

        kind = self.kind

        requested_ref: str | Unset | None
        if isinstance(self.requested_ref, Unset):
            requested_ref = UNSET
        else:
            requested_ref = self.requested_ref

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "repository_url": repository_url,
                "resolved_commit_sha": resolved_commit_sha,
                "subdirectory": subdirectory,
            }
        )
        if kind is not UNSET:
            field_dict["kind"] = kind
        if requested_ref is not UNSET:
            field_dict["requested_ref"] = requested_ref

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        repository_url = d.pop("repository_url")

        resolved_commit_sha = d.pop("resolved_commit_sha")

        subdirectory = d.pop("subdirectory")

        kind = cast(Literal["github"] | Unset, d.pop("kind", UNSET))
        if kind != "github" and not isinstance(kind, Unset):
            raise ValueError(f"kind must match const 'github', got '{kind}'")

        def _parse_requested_ref(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        requested_ref = _parse_requested_ref(d.pop("requested_ref", UNSET))

        git_hub_skill_import_provenance = cls(
            repository_url=repository_url,
            resolved_commit_sha=resolved_commit_sha,
            subdirectory=subdirectory,
            kind=kind,
            requested_ref=requested_ref,
        )

        return git_hub_skill_import_provenance

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="GitHubRevisionSource")


@_attrs_define(repr=False)
class GitHubRevisionSource:
    """
    Attributes:
        repository_url (str):
        credential_secret_id (None | str | Unset):
        expected_commit_sha (None | str | Unset):
        kind (Literal['github'] | Unset):
        ref (None | str | Unset):
        subdirectory (str | Unset):
    """

    repository_url: str
    credential_secret_id: str | Unset | None = UNSET
    expected_commit_sha: str | Unset | None = UNSET
    kind: Literal["github"] | Unset = UNSET
    ref: str | Unset | None = UNSET
    subdirectory: str | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        repository_url = self.repository_url

        credential_secret_id: str | Unset | None
        if isinstance(self.credential_secret_id, Unset):
            credential_secret_id = UNSET
        else:
            credential_secret_id = self.credential_secret_id

        expected_commit_sha: str | Unset | None
        if isinstance(self.expected_commit_sha, Unset):
            expected_commit_sha = UNSET
        else:
            expected_commit_sha = self.expected_commit_sha

        kind = self.kind

        ref: str | Unset | None
        if isinstance(self.ref, Unset):
            ref = UNSET
        else:
            ref = self.ref

        subdirectory = self.subdirectory

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "repository_url": repository_url,
            }
        )
        if credential_secret_id is not UNSET:
            field_dict["credential_secret_id"] = credential_secret_id
        if expected_commit_sha is not UNSET:
            field_dict["expected_commit_sha"] = expected_commit_sha
        if kind is not UNSET:
            field_dict["kind"] = kind
        if ref is not UNSET:
            field_dict["ref"] = ref
        if subdirectory is not UNSET:
            field_dict["subdirectory"] = subdirectory

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        repository_url = d.pop("repository_url")

        def _parse_credential_secret_id(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        credential_secret_id = _parse_credential_secret_id(d.pop("credential_secret_id", UNSET))

        def _parse_expected_commit_sha(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        expected_commit_sha = _parse_expected_commit_sha(d.pop("expected_commit_sha", UNSET))

        kind = cast(Literal["github"] | Unset, d.pop("kind", UNSET))
        if kind != "github" and not isinstance(kind, Unset):
            raise ValueError(f"kind must match const 'github', got '{kind}'")

        def _parse_ref(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        ref = _parse_ref(d.pop("ref", UNSET))

        subdirectory = d.pop("subdirectory", UNSET)

        git_hub_revision_source = cls(
            repository_url=repository_url,
            credential_secret_id=credential_secret_id,
            expected_commit_sha=expected_commit_sha,
            kind=kind,
            ref=ref,
            subdirectory=subdirectory,
        )

        return git_hub_revision_source

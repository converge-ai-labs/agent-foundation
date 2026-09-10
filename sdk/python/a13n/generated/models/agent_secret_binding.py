from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define

if TYPE_CHECKING:
    from ..models.invoking_user_secret_credential import InvokingUserSecretCredential
    from ..models.workspace_secret_credential import WorkspaceSecretCredential


T = TypeVar("T", bound="AgentSecretBinding")


@_attrs_define(repr=False)
class AgentSecretBinding:
    """Bind one declared requirement to a non-secret, owner-scoped lookup intent.

    Attributes:
        credential (InvokingUserSecretCredential | WorkspaceSecretCredential):
        key (str):
    """

    credential: InvokingUserSecretCredential | WorkspaceSecretCredential
    key: str

    def to_dict(self) -> dict[str, Any]:
        from ..models.workspace_secret_credential import WorkspaceSecretCredential

        credential: dict[str, Any]
        if isinstance(self.credential, WorkspaceSecretCredential):
            credential = self.credential.to_dict()
        else:
            credential = self.credential.to_dict()

        key = self.key

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "credential": credential,
                "key": key,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.invoking_user_secret_credential import InvokingUserSecretCredential
        from ..models.workspace_secret_credential import WorkspaceSecretCredential

        d = dict(src_dict)

        def _parse_credential(data: object) -> InvokingUserSecretCredential | WorkspaceSecretCredential:
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                credential_type_0 = WorkspaceSecretCredential.from_dict(data)

                return credential_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            if not isinstance(data, dict):
                raise TypeError()
            credential_type_1 = InvokingUserSecretCredential.from_dict(data)

            return credential_type_1

        credential = _parse_credential(d.pop("credential"))

        key = d.pop("key")

        agent_secret_binding = cls(
            credential=credential,
            key=key,
        )

        return agent_secret_binding

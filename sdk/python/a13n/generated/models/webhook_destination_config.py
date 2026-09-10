from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="WebhookDestinationConfig")


@_attrs_define(repr=False)
class WebhookDestinationConfig:
    """
    Attributes:
        endpoint_url (str):
        signing_secret_id (str):
        signature_profile (Literal['hmac_sha256_v1'] | Unset):
    """

    endpoint_url: str
    signing_secret_id: str
    signature_profile: Literal["hmac_sha256_v1"] | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        endpoint_url = self.endpoint_url

        signing_secret_id = self.signing_secret_id

        signature_profile = self.signature_profile

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "endpoint_url": endpoint_url,
                "signing_secret_id": signing_secret_id,
            }
        )
        if signature_profile is not UNSET:
            field_dict["signature_profile"] = signature_profile

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        endpoint_url = d.pop("endpoint_url")

        signing_secret_id = d.pop("signing_secret_id")

        signature_profile = cast(Literal["hmac_sha256_v1"] | Unset, d.pop("signature_profile", UNSET))
        if signature_profile != "hmac_sha256_v1" and not isinstance(signature_profile, Unset):
            raise ValueError(f"signature_profile must match const 'hmac_sha256_v1', got '{signature_profile}'")

        webhook_destination_config = cls(
            endpoint_url=endpoint_url,
            signing_secret_id=signing_secret_id,
            signature_profile=signature_profile,
        )

        return webhook_destination_config

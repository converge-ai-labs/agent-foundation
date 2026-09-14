from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="CompleteAuthorizationRequest")


@_attrs_define(repr=False)
class CompleteAuthorizationRequest:
    """
    Attributes:
        code (None | str | Unset):
        completion_verifier (None | str | Unset):
        error (None | str | Unset):
        iss (None | str | Unset):
        receipt (None | str | Unset):
        state (None | str | Unset):
    """

    code: str | Unset | None = UNSET
    completion_verifier: str | Unset | None = UNSET
    error: str | Unset | None = UNSET
    iss: str | Unset | None = UNSET
    receipt: str | Unset | None = UNSET
    state: str | Unset | None = UNSET

    def to_dict(self) -> dict[str, Any]:
        code: str | Unset | None
        if isinstance(self.code, Unset):
            code = UNSET
        else:
            code = self.code

        completion_verifier: str | Unset | None
        if isinstance(self.completion_verifier, Unset):
            completion_verifier = UNSET
        else:
            completion_verifier = self.completion_verifier

        error: str | Unset | None
        if isinstance(self.error, Unset):
            error = UNSET
        else:
            error = self.error

        iss: str | Unset | None
        if isinstance(self.iss, Unset):
            iss = UNSET
        else:
            iss = self.iss

        receipt: str | Unset | None
        if isinstance(self.receipt, Unset):
            receipt = UNSET
        else:
            receipt = self.receipt

        state: str | Unset | None
        if isinstance(self.state, Unset):
            state = UNSET
        else:
            state = self.state

        field_dict: dict[str, Any] = {}

        field_dict.update({})
        if code is not UNSET:
            field_dict["code"] = code
        if completion_verifier is not UNSET:
            field_dict["completion_verifier"] = completion_verifier
        if error is not UNSET:
            field_dict["error"] = error
        if iss is not UNSET:
            field_dict["iss"] = iss
        if receipt is not UNSET:
            field_dict["receipt"] = receipt
        if state is not UNSET:
            field_dict["state"] = state

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)

        def _parse_code(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        code = _parse_code(d.pop("code", UNSET))

        def _parse_completion_verifier(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        completion_verifier = _parse_completion_verifier(d.pop("completion_verifier", UNSET))

        def _parse_error(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        error = _parse_error(d.pop("error", UNSET))

        def _parse_iss(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        iss = _parse_iss(d.pop("iss", UNSET))

        def _parse_receipt(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        receipt = _parse_receipt(d.pop("receipt", UNSET))

        def _parse_state(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        state = _parse_state(d.pop("state", UNSET))

        complete_authorization_request = cls(
            code=code,
            completion_verifier=completion_verifier,
            error=error,
            iss=iss,
            receipt=receipt,
            state=state,
        )

        return complete_authorization_request

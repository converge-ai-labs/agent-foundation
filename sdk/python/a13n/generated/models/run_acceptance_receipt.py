from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="RunAcceptanceReceipt")


@_attrs_define(repr=False)
class RunAcceptanceReceipt:
    """
    Attributes:
        run_id (str):
        run_version (int):
        session_id (str):
        thread_id (str):
        thread_version (int):
        hook_subscription_id (None | str | Unset):
        schema_version (Literal['1'] | Unset):
        status (Literal['accepted'] | Unset):
    """

    run_id: str
    run_version: int
    session_id: str
    thread_id: str
    thread_version: int
    hook_subscription_id: str | Unset | None = UNSET
    schema_version: Literal["1"] | Unset = UNSET
    status: Literal["accepted"] | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        run_id = self.run_id

        run_version = self.run_version

        session_id = self.session_id

        thread_id = self.thread_id

        thread_version = self.thread_version

        hook_subscription_id: str | Unset | None
        if isinstance(self.hook_subscription_id, Unset):
            hook_subscription_id = UNSET
        else:
            hook_subscription_id = self.hook_subscription_id

        schema_version = self.schema_version

        status = self.status

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "run_id": run_id,
                "run_version": run_version,
                "session_id": session_id,
                "thread_id": thread_id,
                "thread_version": thread_version,
            }
        )
        if hook_subscription_id is not UNSET:
            field_dict["hook_subscription_id"] = hook_subscription_id
        if schema_version is not UNSET:
            field_dict["schema_version"] = schema_version
        if status is not UNSET:
            field_dict["status"] = status

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        run_id = d.pop("run_id")

        run_version = d.pop("run_version")

        session_id = d.pop("session_id")

        thread_id = d.pop("thread_id")

        thread_version = d.pop("thread_version")

        def _parse_hook_subscription_id(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        hook_subscription_id = _parse_hook_subscription_id(d.pop("hook_subscription_id", UNSET))

        schema_version = cast(Literal["1"] | Unset, d.pop("schema_version", UNSET))
        if schema_version != "1" and not isinstance(schema_version, Unset):
            raise ValueError(f"schema_version must match const '1', got '{schema_version}'")

        status = cast(Literal["accepted"] | Unset, d.pop("status", UNSET))
        if status != "accepted" and not isinstance(status, Unset):
            raise ValueError(f"status must match const 'accepted', got '{status}'")

        run_acceptance_receipt = cls(
            run_id=run_id,
            run_version=run_version,
            session_id=session_id,
            thread_id=thread_id,
            thread_version=thread_version,
            hook_subscription_id=hook_subscription_id,
            schema_version=schema_version,
            status=status,
        )

        return run_acceptance_receipt

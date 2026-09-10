from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define

T = TypeVar("T", bound="InterruptRequest")


@_attrs_define(repr=False)
class InterruptRequest:
    """
    Attributes:
        expected_run_version (int):
        expected_thread_version (int):
    """

    expected_run_version: int
    expected_thread_version: int

    def to_dict(self) -> dict[str, Any]:
        expected_run_version = self.expected_run_version

        expected_thread_version = self.expected_thread_version

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "expected_run_version": expected_run_version,
                "expected_thread_version": expected_thread_version,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        expected_run_version = d.pop("expected_run_version")

        expected_thread_version = d.pop("expected_thread_version")

        interrupt_request = cls(
            expected_run_version=expected_run_version,
            expected_thread_version=expected_thread_version,
        )

        return interrupt_request

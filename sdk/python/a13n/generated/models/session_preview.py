from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

T = TypeVar("T", bound="SessionPreview")


@_attrs_define(repr=False)
class SessionPreview:
    """
    Attributes:
        input_text (None | str):
        output_text (None | str):
        run_id (str):
        thread_id (str):
    """

    input_text: str | None
    output_text: str | None
    run_id: str
    thread_id: str

    def to_dict(self) -> dict[str, Any]:
        input_text: str | None
        input_text = self.input_text

        output_text: str | None
        output_text = self.output_text

        run_id = self.run_id

        thread_id = self.thread_id

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "input_text": input_text,
                "output_text": output_text,
                "run_id": run_id,
                "thread_id": thread_id,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)

        def _parse_input_text(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        input_text = _parse_input_text(d.pop("input_text"))

        def _parse_output_text(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        output_text = _parse_output_text(d.pop("output_text"))

        run_id = d.pop("run_id")

        thread_id = d.pop("thread_id")

        session_preview = cls(
            input_text=input_text,
            output_text=output_text,
            run_id=run_id,
            thread_id=thread_id,
        )

        return session_preview

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define

if TYPE_CHECKING:
    from ..models.configuration_draft import ConfigurationDraft
    from ..models.thread import Thread


T = TypeVar("T", bound="ConfigurationThreadView")


@_attrs_define(repr=False)
class ConfigurationThreadView:
    """
    Attributes:
        draft (ConfigurationDraft):
        thread (Thread):
    """

    draft: ConfigurationDraft
    thread: Thread

    def to_dict(self) -> dict[str, Any]:
        draft = self.draft.to_dict()

        thread = self.thread.to_dict()

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "draft": draft,
                "thread": thread,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.configuration_draft import ConfigurationDraft
        from ..models.thread import Thread

        d = dict(src_dict)
        draft = ConfigurationDraft.from_dict(d.pop("draft"))

        thread = Thread.from_dict(d.pop("thread"))

        configuration_thread_view = cls(
            draft=draft,
            thread=thread,
        )

        return configuration_thread_view

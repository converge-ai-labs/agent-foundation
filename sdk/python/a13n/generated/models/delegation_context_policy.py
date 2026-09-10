from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define

from ..models.delegation_context_policy_history import DelegationContextPolicyHistory
from ..models.delegation_context_policy_task_state import DelegationContextPolicyTaskState
from ..types import UNSET, Unset

T = TypeVar("T", bound="DelegationContextPolicy")


@_attrs_define(repr=False)
class DelegationContextPolicy:
    """
    Attributes:
        history (DelegationContextPolicyHistory | Unset):
        include_task (bool | Unset):
        task_state (DelegationContextPolicyTaskState | Unset):
    """

    history: DelegationContextPolicyHistory | Unset = UNSET
    include_task: bool | Unset = UNSET
    task_state: DelegationContextPolicyTaskState | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        history: str | Unset = UNSET
        if not isinstance(self.history, Unset):
            history = self.history.value

        include_task = self.include_task

        task_state: str | Unset = UNSET
        if not isinstance(self.task_state, Unset):
            task_state = self.task_state.value

        field_dict: dict[str, Any] = {}

        field_dict.update({})
        if history is not UNSET:
            field_dict["history"] = history
        if include_task is not UNSET:
            field_dict["include_task"] = include_task
        if task_state is not UNSET:
            field_dict["task_state"] = task_state

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        _history = d.pop("history", UNSET)
        history: DelegationContextPolicyHistory | Unset
        if isinstance(_history, Unset):
            history = UNSET
        else:
            history = DelegationContextPolicyHistory(_history)

        include_task = d.pop("include_task", UNSET)

        _task_state = d.pop("task_state", UNSET)
        task_state: DelegationContextPolicyTaskState | Unset
        if isinstance(_task_state, Unset):
            task_state = UNSET
        else:
            task_state = DelegationContextPolicyTaskState(_task_state)

        delegation_context_policy = cls(
            history=history,
            include_task=include_task,
            task_state=task_state,
        )

        return delegation_context_policy

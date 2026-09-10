from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.activity_message import ActivityMessage
    from ..models.assistant_message import AssistantMessage
    from ..models.context import Context
    from ..models.developer_message import DeveloperMessage
    from ..models.reasoning_message import ReasoningMessage
    from ..models.resume_entry import ResumeEntry
    from ..models.system_message import SystemMessage
    from ..models.tool import Tool
    from ..models.tool_message import ToolMessage
    from ..models.user_message import UserMessage


T = TypeVar("T", bound="RunAgentInput")


@_attrs_define(repr=False)
class RunAgentInput:
    """Input for running an agent.

    Attributes:
        context (list[Context]):
        forwarded_props (Any):
        messages (list[ActivityMessage | AssistantMessage | DeveloperMessage | ReasoningMessage | SystemMessage |
            ToolMessage | UserMessage]):
        run_id (str):
        state (Any):
        thread_id (str):
        tools (list[Tool]):
        parent_run_id (None | str | Unset):
        resume (list[ResumeEntry] | None | Unset):
    """

    context: list[Context]
    forwarded_props: Any
    messages: list[
        ActivityMessage
        | AssistantMessage
        | DeveloperMessage
        | ReasoningMessage
        | SystemMessage
        | ToolMessage
        | UserMessage
    ]
    run_id: str
    state: Any
    thread_id: str
    tools: list[Tool]
    parent_run_id: str | Unset | None = UNSET
    resume: list[ResumeEntry] | Unset | None = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.activity_message import ActivityMessage
        from ..models.assistant_message import AssistantMessage
        from ..models.developer_message import DeveloperMessage
        from ..models.system_message import SystemMessage
        from ..models.tool_message import ToolMessage
        from ..models.user_message import UserMessage

        context = []
        for context_item_data in self.context:
            context_item = context_item_data.to_dict()
            context.append(context_item)

        forwarded_props = self.forwarded_props

        messages = []
        for messages_item_data in self.messages:
            messages_item: dict[str, Any]
            if isinstance(messages_item_data, DeveloperMessage):
                messages_item = messages_item_data.to_dict()
            elif isinstance(messages_item_data, SystemMessage):
                messages_item = messages_item_data.to_dict()
            elif isinstance(messages_item_data, AssistantMessage):
                messages_item = messages_item_data.to_dict()
            elif isinstance(messages_item_data, UserMessage):
                messages_item = messages_item_data.to_dict()
            elif isinstance(messages_item_data, ToolMessage):
                messages_item = messages_item_data.to_dict()
            elif isinstance(messages_item_data, ActivityMessage):
                messages_item = messages_item_data.to_dict()
            else:
                messages_item = messages_item_data.to_dict()

            messages.append(messages_item)

        run_id = self.run_id

        state = self.state

        thread_id = self.thread_id

        tools = []
        for tools_item_data in self.tools:
            tools_item = tools_item_data.to_dict()
            tools.append(tools_item)

        parent_run_id: str | Unset | None
        if isinstance(self.parent_run_id, Unset):
            parent_run_id = UNSET
        else:
            parent_run_id = self.parent_run_id

        resume: list[dict[str, Any]] | Unset | None
        if isinstance(self.resume, Unset):
            resume = UNSET
        elif isinstance(self.resume, list):
            resume = []
            for resume_type_0_item_data in self.resume:
                resume_type_0_item = resume_type_0_item_data.to_dict()
                resume.append(resume_type_0_item)

        else:
            resume = self.resume

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "context": context,
                "forwardedProps": forwarded_props,
                "messages": messages,
                "runId": run_id,
                "state": state,
                "threadId": thread_id,
                "tools": tools,
            }
        )
        if parent_run_id is not UNSET:
            field_dict["parentRunId"] = parent_run_id
        if resume is not UNSET:
            field_dict["resume"] = resume

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.activity_message import ActivityMessage
        from ..models.assistant_message import AssistantMessage
        from ..models.context import Context
        from ..models.developer_message import DeveloperMessage
        from ..models.reasoning_message import ReasoningMessage
        from ..models.resume_entry import ResumeEntry
        from ..models.system_message import SystemMessage
        from ..models.tool import Tool
        from ..models.tool_message import ToolMessage
        from ..models.user_message import UserMessage

        d = dict(src_dict)
        context = []
        _context = d.pop("context")
        for context_item_data in _context:
            context_item = Context.from_dict(context_item_data)

            context.append(context_item)

        forwarded_props = d.pop("forwardedProps")

        messages = []
        _messages = d.pop("messages")
        for messages_item_data in _messages:

            def _parse_messages_item(
                data: object,
            ) -> (
                ActivityMessage
                | AssistantMessage
                | DeveloperMessage
                | ReasoningMessage
                | SystemMessage
                | ToolMessage
                | UserMessage
            ):
                try:
                    if not isinstance(data, dict):
                        raise TypeError()
                    messages_item_type_0 = DeveloperMessage.from_dict(data)

                    return messages_item_type_0
                except (TypeError, ValueError, AttributeError, KeyError):
                    pass
                try:
                    if not isinstance(data, dict):
                        raise TypeError()
                    messages_item_type_1 = SystemMessage.from_dict(data)

                    return messages_item_type_1
                except (TypeError, ValueError, AttributeError, KeyError):
                    pass
                try:
                    if not isinstance(data, dict):
                        raise TypeError()
                    messages_item_type_2 = AssistantMessage.from_dict(data)

                    return messages_item_type_2
                except (TypeError, ValueError, AttributeError, KeyError):
                    pass
                try:
                    if not isinstance(data, dict):
                        raise TypeError()
                    messages_item_type_3 = UserMessage.from_dict(data)

                    return messages_item_type_3
                except (TypeError, ValueError, AttributeError, KeyError):
                    pass
                try:
                    if not isinstance(data, dict):
                        raise TypeError()
                    messages_item_type_4 = ToolMessage.from_dict(data)

                    return messages_item_type_4
                except (TypeError, ValueError, AttributeError, KeyError):
                    pass
                try:
                    if not isinstance(data, dict):
                        raise TypeError()
                    messages_item_type_5 = ActivityMessage.from_dict(data)

                    return messages_item_type_5
                except (TypeError, ValueError, AttributeError, KeyError):
                    pass
                if not isinstance(data, dict):
                    raise TypeError()
                messages_item_type_6 = ReasoningMessage.from_dict(data)

                return messages_item_type_6

            messages_item = _parse_messages_item(messages_item_data)

            messages.append(messages_item)

        run_id = d.pop("runId")

        state = d.pop("state")

        thread_id = d.pop("threadId")

        tools = []
        _tools = d.pop("tools")
        for tools_item_data in _tools:
            tools_item = Tool.from_dict(tools_item_data)

            tools.append(tools_item)

        def _parse_parent_run_id(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        parent_run_id = _parse_parent_run_id(d.pop("parentRunId", UNSET))

        def _parse_resume(data: object) -> list[ResumeEntry] | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, list):
                    raise TypeError()
                resume_type_0 = []
                _resume_type_0 = data
                for resume_type_0_item_data in _resume_type_0:
                    resume_type_0_item = ResumeEntry.from_dict(resume_type_0_item_data)

                    resume_type_0.append(resume_type_0_item)

                return resume_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(list[ResumeEntry] | Unset | None, data)

        resume = _parse_resume(d.pop("resume", UNSET))

        run_agent_input = cls(
            context=context,
            forwarded_props=forwarded_props,
            messages=messages,
            run_id=run_id,
            state=state,
            thread_id=thread_id,
            tools=tools,
            parent_run_id=parent_run_id,
            resume=resume,
        )

        run_agent_input.additional_properties = d
        return run_agent_input

    @property
    def additional_keys(self) -> list[str]:
        return list(self.additional_properties.keys())

    def __getitem__(self, key: str) -> Any:
        return self.additional_properties[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.additional_properties[key] = value

    def __delitem__(self, key: str) -> None:
        del self.additional_properties[key]

    def __contains__(self, key: str) -> bool:
        return key in self.additional_properties

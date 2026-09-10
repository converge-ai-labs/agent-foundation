from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define

if TYPE_CHECKING:
    from ..models.agent import Agent
    from ..models.agent_revision import AgentRevision


T = TypeVar("T", bound="AgentRevisionCreateResult")


@_attrs_define(repr=False)
class AgentRevisionCreateResult:
    """
    Attributes:
        agent (Agent):
        revision (AgentRevision):
    """

    agent: Agent
    revision: AgentRevision

    def to_dict(self) -> dict[str, Any]:
        agent = self.agent.to_dict()

        revision = self.revision.to_dict()

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "agent": agent,
                "revision": revision,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.agent import Agent
        from ..models.agent_revision import AgentRevision

        d = dict(src_dict)
        agent = Agent.from_dict(d.pop("agent"))

        revision = AgentRevision.from_dict(d.pop("revision"))

        agent_revision_create_result = cls(
            agent=agent,
            revision=revision,
        )

        return agent_revision_create_result

"""The built-in `configuration` toolset: read the workspace's resources, and create agents and revisions.

Every tool acts as the run's principal within the run's delegated authority, through the same service functions
the HTTP API calls, so it can read and change exactly what that principal could through the API. The toolset's
write tools ask for the user's approval by default, so a repeated `create_agent` creates another agent only once
the user approves it again; a retried `create_agent_revision` appends an equal revision, which changes nothing a
run executes.
"""

from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from typing import Annotated, Any, Literal

from a13n_harness import AgentContext
from a13n_harness.tools.metadata import HarnessTool, HarnessToolMetadata, ToolOutputPolicy
from pydantic import BaseModel, Field, JsonValue, ValidationError
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.exceptions import ToolFailed
from pydantic_ai.toolsets import FunctionToolset

from a13n_service.infra.errors import ServiceError, invalid
from a13n_service.infra.http import etag
from a13n_service.resources.agents import service as agents
from a13n_service.resources.agents.schemas import AgentConfig, AgentCreate, AgentRevisionCreate
from a13n_service.resources.agents.toolsets import CONFIGURATION_TOOL_IDS
from a13n_service.resources.connections import service as connections
from a13n_service.resources.environment_templates import service as templates
from a13n_service.resources.models import service as models
from a13n_service.resources.providers.service import rejection_reason
from a13n_service.resources.skills import service as skills
from a13n_service.runs.attempts import Lease
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.tools import tool_failures
from a13n_service.tenancy.authorize import ExecutionAuthority, Principal, Verb, WorkspaceScope, authorize

type ResourceKind = Literal["agent", "model", "skill", "connection", "environment_template"]

_PAGE = 20
_READ = ToolOutputPolicy(max_inline_bytes=65536, max_output_bytes=262144, overflow="truncate", redact=True)
_WRITE = ToolOutputPolicy(max_inline_bytes=4096, max_output_bytes=4096, overflow="truncate", redact=True)


class ConfigurationCapability(AbstractCapability[AgentContext]):
    """The definition's enabled configuration tools, bound to one attempt of the run."""

    id = "a13n.service.configuration"

    def __init__(
        self,
        runtime: Runtime,
        lease: Lease,
        principal: Principal,
        authority: ExecutionAuthority,
        enabled: frozenset[str],
    ):
        self.runtime, self.principal, self.authority, self.enabled = runtime, principal, authority, enabled
        self.scope = WorkspaceScope(lease.organization_id, lease.workspace_id)

    def get_toolset(self) -> FunctionToolset[AgentContext]:
        tools = {
            "find": self._tool(
                self.find_resources,
                "find",
                "List resources of one kind in the workspace, a page at a time.",
                _READ,
            ),
            "read": self._tool(
                self.read_resource,
                "read",
                "Read one resource; an agent comes with the configuration of its default revision or of `revision_id`.",
                _READ,
            ),
            "describe": self._tool(
                self.describe_agent_config,
                "describe",
                "The JSON schema of an agent configuration and the catalogue of built-in toolsets.",
                _READ,
            ),
            "create_agent": self._tool(
                self.create_agent,
                "create_agent",
                "Create an agent whose first revision is `config`.",
                _WRITE,
                write=True,
            ),
            "create_revision": self._tool(
                self.create_agent_revision,
                "create_revision",
                "Create a revision of an existing agent, by default making it the agent's default.",
                _WRITE,
                write=True,
            ),
        }
        return FunctionToolset(
            tools=[tool for key, tool in tools.items() if key in self.enabled], id="a13n-service-configuration"
        )

    def _tool(
        self,
        function: Callable[..., Awaitable[Any]],
        key: str,
        description: str,
        output: ToolOutputPolicy,
        *,
        write: bool = False,
    ) -> HarnessTool:
        return HarnessTool(
            function,
            takes_ctx=False,
            description=description,
            harness_metadata=HarnessToolMetadata(
                tool_id=CONFIGURATION_TOOL_IDS[key],
                effects=frozenset({"read", "write"} if write else {"read"}),
                credential_audiences=(),
                idempotency="none" if write else "read_only",
                output_policy=output,
            ),
        )

    async def find_resources(
        self,
        kind: ResourceKind,
        cursor: Annotated[str | None, Field(max_length=1024, description="The previous page's next_cursor")] = None,
    ) -> JsonValue:
        storage, actor, workspace_id = self.runtime.storage, self.principal, self.scope.workspace_id
        page: BaseModel
        with self._acting("read"):
            match kind:
                case "agent":
                    page = await agents.list_agents(storage, actor, workspace_id, labels=[], limit=_PAGE, cursor=cursor)
                case "model":
                    page = await models.list_models(storage, actor, workspace_id, limit=_PAGE, cursor=cursor)
                case "skill":
                    page = await skills.list_skills(storage, actor, workspace_id, labels=[], limit=_PAGE, cursor=cursor)
                case "connection":
                    page = await connections.list_connections(storage, actor, workspace_id, limit=_PAGE, cursor=cursor)
                case "environment_template":
                    page = await templates.list_templates(
                        storage, actor, workspace_id, labels=[], limit=_PAGE, cursor=cursor
                    )
        return page.model_dump(mode="json")

    async def read_resource(
        self,
        kind: ResourceKind,
        reference: Annotated[
            str, Field(min_length=1, max_length=128, description="A model's key, any other resource's ID")
        ],
        revision_id: Annotated[
            str | None, Field(min_length=1, max_length=128, description="An agent's revision to read, not its default")
        ] = None,
    ) -> JsonValue:
        storage, actor, workspace_id = self.runtime.storage, self.principal, self.scope.workspace_id
        view: BaseModel
        with self._acting("read"):
            if revision_id is not None and kind != "agent":
                raise invalid("revision_id", "only an agent is read at a revision")
            match kind:
                case "agent":
                    agent = await agents.get_agent(storage, actor, workspace_id, reference)
                    selected = revision_id or agent.default_revision_id
                    revision = (
                        None
                        if selected is None
                        else await agents.get_revision(storage, actor, workspace_id, agent.id, selected)
                    )
                    return {
                        "agent": agent.model_dump(mode="json"),
                        "revision": None if revision is None else revision.model_dump(mode="json"),
                    }
                case "model":
                    view = await models.get_model(storage, actor, workspace_id, reference)
                case "skill":
                    view = await skills.get_skill(storage, actor, workspace_id, reference)
                case "connection":
                    view = await connections.get_connection(storage, actor, workspace_id, reference)
                case "environment_template":
                    view = await templates.get_template(storage, actor, workspace_id, reference)
        return view.model_dump(mode="json")

    async def describe_agent_config(self) -> JsonValue:
        with self._acting("read"):
            catalog = await agents.toolset_catalog(
                self.runtime.storage, self.principal, self.scope.workspace_id, registry=self.runtime.registry
            )
        return {"schema": AgentConfig.model_json_schema(), "toolsets": catalog.model_dump(mode="json")}

    async def create_agent(
        self,
        name: Annotated[str, Field(min_length=1, max_length=128)],
        config: Annotated[dict[str, JsonValue], Field(description="An agent configuration; see describe_agent_config")],
        description: Annotated[str, Field(max_length=2048)] = "",
    ) -> JsonValue:
        with self._acting("write"):
            body = _parsed(AgentCreate, {"name": name, "description": description, "config": config})
            agent = await agents.create_agent(
                self.runtime.storage,
                self.principal,
                self.scope.workspace_id,
                body,
                registry=self.runtime.registry,
                plugins=self.runtime.plugins,
            )
        return {"agent_id": agent.id, "default_revision_id": agent.default_revision_id}

    async def create_agent_revision(
        self,
        agent_id: Annotated[str, Field(min_length=1, max_length=128)],
        config: Annotated[dict[str, JsonValue], Field(description="The whole new configuration, not a patch")],
        note: Annotated[str | None, Field(max_length=2048)] = None,
        make_default: bool = True,
    ) -> JsonValue:
        storage, workspace_id = self.runtime.storage, self.scope.workspace_id
        with self._acting("write"):
            body = _parsed(AgentRevisionCreate, {"config": config, "note": note, "make_default": make_default})
            # Revisions are immutable and appended, so the head's current version is the one to extend.
            agent = await agents.get_agent(storage, self.principal, workspace_id, agent_id)
            revision = await agents.create_revision(
                storage,
                self.principal,
                workspace_id,
                agent.id,
                body,
                if_match=etag(agent.id, agent.version),
                registry=self.runtime.registry,
                plugins=self.runtime.plugins,
            )
        return {"agent_id": agent.id, "revision_id": revision.id, "number": revision.number}

    @contextmanager
    def _acting(self, verb: Verb) -> Iterator[None]:
        """Authorize the call within the run's delegated authority; the model sees each refusal as a failure."""
        with tool_failures(_message):
            authorize(self.principal, self.scope, verb, authority=self.authority)
            yield


def _message(error: ServiceError) -> str:
    field = error.details.get("field") if error.details else None
    return f"{error.message} (at config.{field})" if error.code == "invalid_argument" and field else error.message


def _parsed[M: BaseModel](model: type[M], value: dict[str, JsonValue]) -> M:
    """The tool's arguments as the request body they stand for; the model reads where they do not fit."""
    try:
        return model.model_validate(value)
    except ValidationError as error:
        raise ToolFailed(rejection_reason(error)) from None

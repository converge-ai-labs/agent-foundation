"""Native root tools, scoped ToolSearch and changed-only safe Connector context."""

import hashlib
import json
from datetime import UTC, datetime
from html import escape

from a13n_harness import AgentContext
from a13n_harness.capabilities.tool_search import ToolSearchCapability, ToolSearchGroup
from a13n_harness.model_context import (
    AbstractModelContextCapability,
    ModelContextBlock,
    ModelContextNext,
    ModelContextPlacement,
    ModelContextProjection,
    ModelContextProjectionRequest,
)
from a13n_harness.tools.metadata import HarnessTool, HarnessToolMetadata, ToolOutputPolicy
from a13n_harness.tools.tool_search import ProxyToolSearchConfig
from pydantic_ai import RunContext
from pydantic_ai.capabilities import CombinedCapability
from pydantic_ai.toolsets import FunctionToolset

from a13n_harness_ui.bot import BotController
from a13n_harness_ui.bot_models import BOT_THREAD_ID
from a13n_harness_ui.errors import HarnessUiError

from .models import Capture, SetupOffer
from .runtime import Connectors

CONTROLS = ProxyToolSearchConfig(
    search_name="discover_connector_tools",
    describe_name="describe_connector_tool",
    call_name="call_connector_tool",
    list_name="list_connector_tools",
    groups_name="list_connectors",
)
CONTROL_NAMES = frozenset(
    {CONTROLS.search_name, CONTROLS.describe_name, CONTROLS.call_name, CONTROLS.list_name, CONTROLS.groups_name}
)


def connector_capability(
    runtime: Connectors, *, thread_id: str, captures: tuple[Capture, ...], bot: BotController
) -> CombinedCapability[AgentContext]:
    sources = runtime.capabilities(captures)
    groups = {
        capture.group_name: ToolSearchGroup(
            source=sources[capture.connector_id],
            description=(
                f"{capture.connector_key} tools for account {capture.connector_name or capture.connector_id!r}. "
                "Upstream credentials and permissions apply."
            ),
        )
        for capture in captures
        if capture.connector_id in sources
    }
    return CombinedCapability(
        [
            ConnectorContext(runtime, thread_id, captures, bot),
            *(
                [ToolSearchCapability(groups=groups, config=CONTROLS, surface_id="a13n.ui.connector-search")]
                if groups
                else []
            ),
        ]
    )


class ConnectorContext(AbstractModelContextCapability):
    id = "a13n.ui.connectors"

    def __init__(self, runtime: Connectors, thread_id: str, captures: tuple[Capture, ...], bot: BotController) -> None:
        self.runtime, self.thread_id, self.captures, self.bot = runtime, thread_id, captures, bot

    async def offer_connector_setup(
        self, ctx: RunContext[AgentContext], connector_key: str, connector_id: str | None = None, text: str = ""
    ) -> SetupOffer:
        """Offer human setup in this conversation. Opens no connection, starts no setup and accepts no credentials."""
        if ctx.deps.thread_id != self.thread_id:
            raise HarnessUiError(
                "Connector setup is available only in its captured root.", code="connector_scope_invalid"
            )
        item = self.runtime.catalog.items.get(connector_key)
        if item is None or not item.available:
            raise HarnessUiError("Connector type is unavailable.", code="connector_plugin_unavailable")
        if (
            connector_id is not None
            and (await self.runtime.store.connectors.configuration(connector_id)).kind != connector_key
        ):
            raise HarnessUiError("Connector instance does not match the offered type.", code="connector_input_invalid")
        offer_id = (
            "offer-"
            + hashlib.sha256(f"{self.thread_id}:{ctx.deps.run_id}:{ctx.tool_call_id}".encode()).hexdigest()[:32]
        )
        offer = SetupOffer(
            offer_id=offer_id,
            thread_id=self.thread_id,
            connector_key=connector_key,
            connector_id=connector_id,
            text=text[:4096],
            tool_call_id=ctx.tool_call_id,
            run_id=ctx.deps.run_id,
            created_at=datetime.now(UTC),
        )
        if self.thread_id == BOT_THREAD_ID:
            await self.bot.publish(
                message_id=offer_id,
                text=text or f"Configure {item.declaration.name if item.declaration else connector_key} to continue.",
                connector_setups=(offer,),
            )
        else:
            offer = await self.runtime.store.connectors.offer(offer)
            await self.runtime._changed()
        return offer

    def get_toolset(self) -> FunctionToolset[AgentContext]:
        return FunctionToolset(
            id=self.id,
            tools=[
                HarnessTool(
                    self.offer_connector_setup,
                    harness_metadata=HarnessToolMetadata(
                        tool_id="connector.offer_setup",
                        effects=frozenset({"write"}),
                        credential_audiences=(),
                        idempotency="none",
                        output_policy=ToolOutputPolicy(max_inline_bytes=8192, max_output_bytes=8192),
                    ),
                )
            ],
        )

    async def wrap_model_context(
        self, ctx: RunContext[AgentContext], request: ModelContextProjectionRequest, handler: ModelContextNext
    ) -> ModelContextProjection:
        projection = await handler(request)
        current = await self.runtime.model_context(self.captures)
        block = ModelContextBlock(
            source_id="a13n.ui.connectors",
            placement=ModelContextPlacement.REQUEST_EPILOGUE,
            content="<connectors>\n"
            + escape(json.dumps(current, ensure_ascii=False, sort_keys=True, separators=(",", ":")), quote=False)
            + "\n</connectors>",
        )
        return (
            projection
            if request.history.matches_latest(block)
            else ModelContextProjection(blocks=(*projection.blocks, block))
        )

"""Shared setup for concrete Remote MCP providers using Host-owned DCR OAuth."""

from pydantic import JsonValue

from a13n_harness_ui.errors import HarnessUiError

from .models import SetupAction, Step
from .remote_mcp import RemoteMcp
from .sdk import SetupFlow


class OAuthSetup(SetupFlow):
    def __init__(self, controller: RemoteMcp, name: str, permissions: str) -> None:
        self.controller, self.name, self.permissions = controller, name, permissions
        self.done = False

    async def read(self) -> Step:
        if self.done:
            return Step(key="completed", title="Connection saved", state="completed")
        ready = (await self.controller.status()).state == "ready"
        return Step(
            key="authorization",
            title=f"Connect {self.name}",
            description="Send a new message to use it; no operation was replayed." if ready else self.permissions,
            actions=(
                SetupAction(id="authorize", label="Reconnect" if ready else "Connect", kind="oauth"),
                *(
                    (
                        SetupAction(id="test", label="Test connection", icon="test"),
                        SetupAction(id="done", label="Done", icon="done"),
                    )
                    if ready
                    else ()
                ),
            ),
        )

    async def act(self, action: str, values: dict[str, JsonValue]) -> Step:
        if action == "done":
            if (await self.controller.status()).state != "ready":
                raise HarnessUiError("Complete authorization first.", code="connector_input_invalid")
            self.done = True
        elif action == "test":
            try:
                await self.controller.mcp.list_tools()
            except Exception:
                raise HarnessUiError(
                    "Connection test failed. Check access or reconnect.", code="connector_connection_failed"
                ) from None
            return (await self.read()).model_copy(update={"description": "Connection test succeeded."})
        else:
            raise ValueError("Unknown setup action")
        return await self.read()

    async def close(self) -> None:
        oauth = self.controller.context.oauth
        if oauth is not None and oauth.cancel_logins is not None:
            await oauth.cancel_logins(f"connector:{self.controller.context.connector_id}")

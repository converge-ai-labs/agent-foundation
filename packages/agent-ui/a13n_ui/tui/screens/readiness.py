"""Cancellable production Sandbox check for ordinary conversation selectors."""

from __future__ import annotations

import asyncio
from typing import ClassVar

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from a13n_ui.app import AgentUiApp


class ReadinessScreen(ModalScreen[str | None]):
    BINDINGS: ClassVar = [("escape", "cancel", "Cancel"), ("ctrl+c", "cancel", "Cancel")]
    DEFAULT_CSS = """
    ReadinessScreen { align: center middle; }
    ReadinessScreen > VerticalScroll { width: 80; max-width: 95%; height: auto; max-height: 90%; padding: 1 2; border: round $accent; background: $surface; }
    ReadinessScreen Button { margin-top: 1; }
    """

    def __init__(self, application: AgentUiApp, roots: tuple[str, ...]) -> None:
        super().__init__()
        self._application = application
        self._roots = roots
        self._probe: asyncio.Task[None] | None = None

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield Static("Sandbox readiness", markup=False)
            yield Static(
                "Checking the exact production runtime. No system policy will be changed.",
                id="readiness-result",
                markup=False,
            )
            yield Button("Retry Sandbox", id="readiness-retry")
            yield Button("Choose Full Control (no Sandbox)", id="readiness-native", variant="warning")
            yield Static(
                "Full Control runs as your host account with ambient filesystem and network access. Shell review is not isolation.",
                markup=False,
            )
            yield Button("Cancel and keep previous selection", id="readiness-cancel")

    def on_mount(self) -> None:
        self._start_probe()

    def _start_probe(self) -> None:
        if self._probe is not None:
            self._probe.cancel()
        self.query_one("#readiness-retry", Button).disabled = True
        self._probe = asyncio.create_task(self._check())

    async def _check(self) -> None:
        try:
            for root in self._roots:
                self.query_one("#readiness-result", Static).update(
                    f"Checking Sandbox for {root}... This can take up to 90 seconds."
                )
                result = await self._application.preflight_environment(
                    profile_id="environment-sandbox", project_path=root
                )
                if not result.ready:
                    self.query_one("#readiness-result", Static).update(
                        "\n".join((result.message, *result.instructions, result.documentation_url))
                    )
                    return
            self.dismiss("environment-sandbox")
        except asyncio.CancelledError:
            raise
        except Exception:
            self.query_one("#readiness-result", Static).update(
                "Sandbox readiness could not be checked. Retry, cancel, or explicitly select Full Control."
            )
        finally:
            if self.is_mounted:
                self.query_one("#readiness-retry", Button).disabled = False

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "readiness-retry":
            self._start_probe()
        elif event.button.id == "readiness-native":
            self._cancel_probe()
            self.dismiss("environment-native")
        elif event.button.id == "readiness-cancel":
            self.action_cancel()

    def _cancel_probe(self) -> None:
        if self._probe is not None and self._probe is not asyncio.current_task():
            self._probe.cancel()

    def action_cancel(self) -> None:
        self._cancel_probe()
        self.dismiss(None)

    async def on_unmount(self) -> None:
        self._cancel_probe()
        if self._probe is not None and self._probe is not asyncio.current_task():
            await asyncio.gather(self._probe, return_exceptions=True)

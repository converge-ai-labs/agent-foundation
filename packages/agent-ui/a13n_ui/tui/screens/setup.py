"""Keyboard-accessible first-use setup backed by shared App operations."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from pydantic import ValidationError
from textual import on, work
from textual.app import ComposeResult
from textual.binding import BindingType
from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, Label, Select, Static, TextArea

from a13n_ui.app import AgentUiApp
from a13n_ui.configuration.setup import SetupPreview, SetupSelection
from a13n_ui.errors import AgentUiError
from a13n_ui.setup import SetupStatus


class SetupScreen(ModalScreen[bool]):
    DEFAULT_CSS = """
    SetupScreen { align: center middle; }
    SetupScreen > VerticalScroll { width: 90%; max-width: 110; height: 95%; border: round $accent; background: $surface; padding: 1 2; }
    SetupScreen Label { margin-top: 1; }
    SetupScreen Horizontal { height: auto; }
    SetupScreen Button { margin: 1 1 0 0; }
    SetupScreen TextArea { height: 12; margin-top: 1; }
    SetupScreen #setup-message { margin-top: 1; height: auto; }
    """
    BINDINGS: ClassVar[list[BindingType]] = [("escape", "cancel", "Cancel")]

    def __init__(self, application: AgentUiApp, *, directory: Path, status: SetupStatus | None = None) -> None:
        super().__init__()
        self._application = application
        self._directory = directory
        self._status = status
        self._preview: SetupPreview | None = None
        self._selection: SetupSelection | None = None
        self._probing = False

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield Label("Set up Agent UI")
            yield Static(
                "Choose compatible subscriptions and editable starter Agents. No credentials are copied and no model request is made during setup."
            )
            yield Checkbox("Codex subscription", id="setup-codex")
            yield Checkbox("Grok subscription", id="setup-grok")
            yield Static(
                "Sign in externally with a13n-ui auth login codex or a13n-ui auth login grok, then Retry accounts. Existing compatible Codex/Grok login is reused.",
                markup=False,
            )
            yield Button("Retry accounts", id="setup-retry")
            yield Label("Default Agent (existing Agents are preserved)")
            yield Select[str]([], id="setup-agent")
            yield Label("Codex starter model (availability depends on your subscription)")
            yield Select[str](
                [
                    ("Terra — balanced everyday coding", "gpt-5.6-terra"),
                    ("Sol — deeper reasoning", "gpt-5.6-sol"),
                    ("Astra — strongest, eligible accounts only", "gpt-6-astra"),
                ],
                value="gpt-5.6-terra",
                allow_blank=False,
                id="setup-model",
            )
            yield Checkbox("Enable shell review (Codex Luna; Grok 4.6 if Grok only)", value=True, id="setup-review")
            yield Label("Default Project")
            yield Select[str](
                [("Create/use local project", "project-local")],
                value="project-local",
                allow_blank=False,
                id="setup-project",
            )
            yield Label("New project's directory (existing Project roots are preserved)")
            yield Input(str(self._directory), id="setup-directory")
            yield Label("Execution authority")
            yield Select[str](
                [
                    ("Full Control — runs as your Host user, no sandbox", "environment-native"),
                    ("Sandbox — required isolation, denied network", "environment-sandbox"),
                ],
                value="environment-native",
                allow_blank=False,
                id="setup-environment",
            )
            yield Static(
                "Full Control has ambient Host filesystem and network access. Shell review is a guardrail, not isolation.",
                id="setup-authority",
            )
            with Horizontal():
                yield Button("Preview files", id="setup-preview")
                yield Button("Check Sandbox", id="setup-probe")
                yield Button("Apply setup", id="setup-apply", variant="primary", disabled=True)
                yield Button("Cancel", id="setup-cancel")
            yield Static("Discovering accounts...", id="setup-message", markup=False)
            yield TextArea("", read_only=True, id="setup-files")

    def on_mount(self) -> None:
        if self._status is not None:
            self._project_status(self._status)
        else:
            self._discover()

    def _message(self, value: str) -> None:
        self.query_one("#setup-message", Static).update(value)

    @work(exclusive=True, group="setup-operation")
    async def _discover(self) -> None:
        try:
            self._project_status(await self._application.setup_status(rediscover=True))
        except AgentUiError as exc:
            self._message(f"{exc.code}: {exc}")

    def _project_status(self, status: SetupStatus) -> None:
        self._status = status
        for provider in status.providers:
            checkbox = self.query_one(f"#setup-{provider.provider}", Checkbox)
            checkbox.value = provider.selected
            checkbox.disabled = not provider.available
        projects = {"project-local": "Create/use local project", **status.projects}
        self.query_one("#setup-project", Select).set_options([(name, key) for key, name in projects.items()])
        self.query_one("#setup-project", Select).value = status.default_project or "project-local"
        if status.environment_profile in {"environment-native", "environment-sandbox"}:
            self.query_one("#setup-environment", Select).value = status.environment_profile
        self._agents()
        self._message(
            "\n".join(
                [
                    f"Configuration: {status.configuration_path}",
                    *(
                        f"{provider.provider}: {provider.action}"
                        + (f" — {provider.diagnostic}" if provider.diagnostic else "")
                        for provider in status.providers
                    ),
                    status.diagnostic or "Preview before applying. Existing resources are never overwritten.",
                ]
            )
        )

    def _agents(self) -> None:
        agents = {} if self._status is None else dict(self._status.agents)
        for provider in ("codex", "grok"):
            if self.query_one(f"#setup-{provider}", Checkbox).value:
                agents.setdefault(f"agent-{provider}", f"{provider.title()} coding starter")
        selector = self.query_one("#setup-agent", Select)
        old = selector.value
        selector.set_options([(name, key) for key, name in agents.items()])
        default = None if self._status is None else self._status.default_agent
        selector.value = old if old in agents else default if default in agents else next(iter(agents), Select.BLANK)

    def _invalidate(self) -> None:
        if self._probing:
            self.workers.cancel_group(self, "setup-operation")
            self._probing = False
            self._message("Check cancelled because the selection changed. Preview and check the new selection.")
        self._preview = None
        self._selection = None
        self.query_one("#setup-apply", Button).disabled = True

    @on(Checkbox.Changed)
    def changed_checkbox(self, event: Checkbox.Changed) -> None:
        self._invalidate()
        if event.checkbox.id in {"setup-codex", "setup-grok"}:
            self._agents()

    @on(Input.Changed)
    @on(Select.Changed)
    def changed_selection(self) -> None:
        self._invalidate()
        environment = self.query_one("#setup-environment", Select).value
        self.query_one("#setup-authority", Static).update(
            "Sandbox must pass production preflight. Failure never silently enables Full Control."
            if environment == "environment-sandbox"
            else "Full Control has ambient Host filesystem and network access. Applying this choice explicitly authorizes execution without Sandbox."
        )

    def _read_selection(self) -> SetupSelection:
        return SetupSelection.model_validate(
            {
                "providers": tuple(
                    provider for provider in ("codex", "grok") if self.query_one(f"#setup-{provider}", Checkbox).value
                ),
                "default_agent": self.query_one("#setup-agent", Select).value,
                "project": self.query_one("#setup-project", Select).value,
                "project_path": self.query_one("#setup-directory", Input).value,
                "environment_profile": self.query_one("#setup-environment", Select).value,
                "shell_review": self.query_one("#setup-review", Checkbox).value,
                "codex_model": self.query_one("#setup-model", Select).value,
            }
        )

    @on(Button.Pressed)
    def pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "setup-cancel":
            self.action_cancel()
        elif event.button.id == "setup-retry":
            self._discover()
        else:
            self._operate(event.button.id or "")

    @work(exclusive=True, group="setup-operation")
    async def _operate(self, action: str) -> None:
        try:
            selection = self._read_selection()
            if action == "setup-apply":
                if self._preview is None or selection != self._selection:
                    self._message("Preview the current choices before applying.")
                    return
                result = await self._application.apply_setup(selection, expected_generation=self._preview.generation)
                if result.completed:
                    self.dismiss(True)
                else:
                    self._invalidate()
                    self._message(
                        f"{result.error_message}\nPublished: {', '.join(result.published_paths) or 'none'}. Preview again to retry safely."
                    )
                return
            preview = await self._application.preview_setup(selection)
            if selection != self._read_selection():
                return
            self._preview, self._selection = preview, selection
            self.query_one("#setup-files", TextArea).load_text(
                "\n\n".join(f"# {name}\n{text}" for name, text in preview.files.items())
            )
            self.query_one("#setup-apply", Button).disabled = False
            if action == "setup-probe":
                self._probing = True
                self._message("Checking production Sandbox readiness... Cancel or change choices to leave this check.")
                for root in preview.project_paths:
                    result = await self._application.preflight_environment("environment-sandbox", project_path=root)
                    if selection != self._read_selection():
                        return
                    self._message(result.message + "\n" + "\n".join(result.instructions))
                    if not result.ready:
                        return
            else:
                self._message(
                    f"Review {len(preview.files)} file(s). Preserved {len(preview.preserved_paths)} existing resource(s)."
                    + (
                        " Check Sandbox before applying."
                        if selection.environment_profile == "environment-sandbox"
                        else " Apply explicitly selects Full Control without Sandbox."
                    )
                )
        except (AgentUiError, ValidationError) as exc:
            self._message(
                str(exc)
                if isinstance(exc, AgentUiError)
                else "Choose a default Agent, an existing directory, and valid setup selections."
            )
        finally:
            self._probing = False

    def action_cancel(self) -> None:
        self.workers.cancel_group(self, "setup-operation")
        self.dismiss(False)

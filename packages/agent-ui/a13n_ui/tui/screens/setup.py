"""Three-step first-use setup backed by shared App operations."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from pydantic import ValidationError
from textual import on, work
from textual.app import ComposeResult
from textual.binding import BindingType
from textual.containers import Horizontal, Vertical, VerticalScroll
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
    SetupScreen Horizontal, SetupScreen Vertical { height: auto; }
    SetupScreen Button { margin: 1 1 0 0; }
    SetupScreen TextArea { height: 8; margin-top: 1; }
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
        self._step = 1
        self._busy = ""
        self._initialized = False
        self._ready_roots: tuple[str, ...] | None = None

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield Label("Set up Agent UI")
            yield Static("1. Model connection / 2. Execution environment / 3. Agent", id="setup-progress")
            with Vertical(id="setup-step-1"):
                yield Label("Model connection")
                yield Select[str](
                    [
                        ("Subscription — BYOS", "subscription"),
                        ("API key — BYOK", "api_key"),
                        ("Connect later", "later"),
                    ],
                    value="subscription",
                    allow_blank=False,
                    id="setup-connection",
                )
                with Vertical(id="setup-subscriptions"):
                    yield Checkbox("Codex subscription", id="setup-codex")
                    yield Checkbox("Grok subscription", id="setup-grok")
                    yield Static(
                        "Add an account in another terminal on this host: a13n-ui auth login codex or a13n-ui auth login grok. Finish login, then refresh. This wizard reuses existing login; it does not present the authorization flow.",
                        markup=False,
                    )
                    yield Button("Refresh accounts", id="setup-retry")
                with Vertical(id="setup-api-key"):
                    yield Static(
                        "Use an existing API key from the host environment. No key is saved or tested here. Set the variable before launching Agent UI; enter its name below, not the secret.",
                        markup=False,
                    )
                    yield Label("Model route (provider:model-name)")
                    yield Input(placeholder="provider:model-name", id="setup-api-route")
                    yield Label("API key environment variable")
                    yield Input("OPENAI_API_KEY", id="setup-api-env")
            with Vertical(id="setup-step-2"):
                yield Label("Execution environment")
                yield Label("Project")
                yield Select[str](
                    [("Create/use local project", "project-local")],
                    value="project-local",
                    allow_blank=False,
                    id="setup-project",
                )
                yield Label("New project's directory (existing roots are preserved)")
                yield Input(str(self._directory), id="setup-directory")
                yield Static("", id="setup-roots", markup=False)
                yield Select[str](
                    [
                        ("Sandbox — required isolation, denied network", "environment-sandbox"),
                        ("Full Control — host user, no sandbox", "environment-native"),
                    ],
                    value="environment-native",
                    allow_blank=False,
                    id="setup-environment",
                )
                yield Static("", id="setup-authority")
                yield Button("Check Sandbox / Retry", id="setup-probe")
            with Vertical(id="setup-step-3"):
                yield Label("Your default Agent")
                yield Select[str]([], id="setup-agent")
                yield Static(
                    "Existing Agents are preserved. An unconfigured Agent cannot send until you connect a model. Defaults affect new conversations only.",
                    markup=False,
                )
                yield Label("Codex model (subscription access varies)", id="setup-model-label")
                yield Select[str](
                    [
                        ("Terra — balanced coding", "gpt-5.6-terra"),
                        ("Sol — deeper reasoning", "gpt-5.6-sol"),
                        ("Astra — eligible accounts only", "gpt-6-astra"),
                    ],
                    value="gpt-5.6-terra",
                    allow_blank=False,
                    id="setup-model",
                )
                yield Checkbox("Shell review (Codex Luna; Grok 4.6 if Grok only)", value=True, id="setup-review")
                yield Label("Additional instructions (optional; the built-in system prompt always applies)")
                yield TextArea("", id="setup-instructions")
                yield Static("", id="setup-default-prompt", markup=False)
                yield Button("Preview files", id="setup-preview")
                yield TextArea("", read_only=True, id="setup-files")
            yield Static("Discovering accounts...", id="setup-message", markup=False)
            with Horizontal():
                yield Button("Back", id="setup-back")
                yield Button("Continue", id="setup-next", variant="primary")
                yield Button("Not now", id="setup-skip")
                yield Button("Finish setup", id="setup-apply", variant="primary", disabled=True)
                yield Button("Cancel check", id="setup-cancel-check")
                yield Button("Cancel setup", id="setup-cancel")

    def on_mount(self) -> None:
        self._render_step()
        if self._status is not None:
            self._project_status(self._status)
        else:
            self._discover()

    def _message(self, value: str) -> None:
        self.query_one("#setup-message", Static).update(value)

    def _roots(self) -> tuple[str, ...]:
        project = self.query_one("#setup-project", Select).value
        if self._status is not None and isinstance(project, str) and project in self._status.project_paths:
            return self._status.project_paths[project]
        return (self.query_one("#setup-directory", Input).value,)

    def _environment_ready(self) -> bool:
        return (
            self.query_one("#setup-environment", Select).value == "environment-native"
            or self._ready_roots == self._roots()
        )

    def _render_step(self) -> None:
        for step in (1, 2, 3):
            box = self.query_one(f"#setup-step-{step}", Vertical)
            box.display = step == self._step
            box.disabled = bool(self._busy)
        self.query_one("#setup-progress", Static).update(
            f"Step {self._step} of 3: " + ("Model connection", "Execution environment", "Agent")[self._step - 1]
        )
        mode = self.query_one("#setup-connection", Select).value
        self.query_one("#setup-subscriptions", Vertical).display = mode == "subscription"
        self.query_one("#setup-api-key", Vertical).display = mode == "api_key"
        for name, visible in {
            "back": self._step > 1,
            "next": self._step < 3,
            "skip": self._step == 1,
            "apply": self._step == 3,
            "cancel-check": self._busy == "setup-probe",
        }.items():
            button = self.query_one(f"#setup-{name}", Button)
            button.display = visible
            button.disabled = bool(self._busy) and name != "cancel-check"
        self.query_one("#setup-apply", Button).disabled = (
            bool(self._busy) or self._preview is None or not self._environment_ready()
        )
        self.query_one("#setup-next", Button).disabled = bool(self._busy) or (
            self._step == 2 and not self._environment_ready()
        )
        self.query_one("#setup-cancel", Button).disabled = self._busy == "setup-apply"
        codex = mode == "subscription" and self.query_one("#setup-codex", Checkbox).value
        self.query_one("#setup-model", Select).display = codex
        self.query_one("#setup-model-label", Label).display = codex
        self.query_one("#setup-review", Checkbox).display = mode == "subscription"
        existing = self._status is not None and self.query_one("#setup-agent", Select).value in self._status.agents
        self.query_one("#setup-instructions", TextArea).read_only = existing
        project = self.query_one("#setup-project", Select).value
        self.query_one("#setup-directory", Input).disabled = (
            self._status is not None and project in self._status.project_paths
        )
        self.query_one("#setup-roots", Static).update("Effective roots: " + ", ".join(self._roots()))
        sandbox = self.query_one("#setup-environment", Select).value == "environment-sandbox"
        self.query_one("#setup-probe", Button).display = sandbox
        self.query_one("#setup-authority", Static).update(
            "Sandbox must pass production preflight; system policy is never changed."
            if sandbox
            else "Full Control runs as your host account with ambient filesystem and network access. Shell review is not isolation."
        )

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
        if not self._initialized:
            self.query_one("#setup-project", Select).set_options(
                [(name, key) for key, name in {"project-local": "Create/use local project", **status.projects}.items()]
            )
            self.query_one("#setup-project", Select).value = status.default_project or "project-local"
            if status.environment_profile in {"environment-native", "environment-sandbox"}:
                self.query_one("#setup-environment", Select).value = status.environment_profile
            self._initialized = True
        self.query_one("#setup-default-prompt", Static).update("Built-in system prompt:\n" + status.system_prompt)
        self._agents()
        self._render_step()
        self._message(
            "\n".join(
                [
                    f"Configuration: {status.configuration_path}",
                    *(
                        f"{p.provider}: {p.action}" + (f" — {p.diagnostic}" if p.diagnostic else "")
                        for p in status.providers
                    ),
                    status.diagnostic or "Connect now or choose Not now. Nothing is written until Finish setup.",
                ]
            )
        )

    def _agents(self) -> None:
        agents = {} if self._status is None else dict(self._status.agents)
        mode = self.query_one("#setup-connection", Select).value
        added = False
        for provider in ("codex", "grok"):
            if mode == "subscription" and self.query_one(f"#setup-{provider}", Checkbox).value:
                agents.setdefault(f"agent-{provider}", f"{provider.title()} coding starter")
                added = True
        if mode == "api_key":
            agents.setdefault("agent-api-key", "API key Agent")
        elif not added:
            agents.setdefault("agent-default", "Default Agent — connect a model later")
        selector = self.query_one("#setup-agent", Select)
        old = selector.value
        selector.set_options([(name, key) for key, name in agents.items()])
        default = None if self._status is None else self._status.default_agent
        selector.value = old if old in agents else default if default in agents else next(iter(agents))

    def _invalidate(self) -> None:
        self._preview = None
        self._selection = None
        self.query_one("#setup-files", TextArea).load_text("")

    @on(Checkbox.Changed)
    def changed_checkbox(self, event: Checkbox.Changed) -> None:
        self._invalidate()
        if event.checkbox.id in {"setup-codex", "setup-grok"}:
            self._agents()
        self._render_step()

    @on(Input.Changed)
    @on(Select.Changed)
    def changed_selection(self, event: Input.Changed | Select.Changed) -> None:
        self._invalidate()
        if event.control.id in {"setup-directory", "setup-project", "setup-environment"}:
            self._ready_roots = None
        if event.control.id == "setup-connection":
            self._agents()
        self._render_step()

    @on(TextArea.Changed, "#setup-instructions")
    def changed_instructions(self) -> None:
        self._invalidate()
        self._render_step()

    def _read_selection(self) -> SetupSelection:
        mode = self.query_one("#setup-connection", Select).value
        return SetupSelection.model_validate(
            {
                "providers": tuple(
                    p
                    for p in ("codex", "grok")
                    if mode == "subscription" and self.query_one(f"#setup-{p}", Checkbox).value
                ),
                "api_key_model": {
                    "route": self.query_one("#setup-api-route", Input).value,
                    "authentication": {"kind": "api_key", "env": self.query_one("#setup-api-env", Input).value},
                }
                if mode == "api_key"
                else None,
                "instructions": self.query_one("#setup-instructions", TextArea).text,
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
        action = event.button.id
        if self._busy and action not in {"setup-cancel", "setup-cancel-check"}:
            return
        if action == "setup-cancel":
            self.action_cancel()
        elif action == "setup-cancel-check":
            self.workers.cancel_group(self, "setup-operation")
            self._ready_roots = None
            self._message("Sandbox check cancelled. Retry or explicitly choose Full Control.")
        elif action == "setup-retry":
            self._discover()
        elif action in {"setup-back", "setup-next", "setup-skip"}:
            if action == "setup-skip":
                self.query_one("#setup-connection", Select).value = "later"
                for provider in ("codex", "grok"):
                    self.query_one(f"#setup-{provider}", Checkbox).value = False
                self.query_one("#setup-api-route", Input).value = ""
                self._agents()
                self.query_one("#setup-agent", Select).value = "agent-default"
                self._step = 2
            elif action == "setup-back":
                self._step = max(1, self._step - 1)
            elif self._step == 2 and not self._environment_ready():
                return
            else:
                if self._step == 1:
                    try:
                        selection = self._read_selection()
                        if (
                            self.query_one("#setup-connection", Select).value == "subscription"
                            and not selection.providers
                            and not (self._status and self._status.agents)
                        ):
                            self._message("Add an account and refresh, or choose Not now.")
                            return
                    except ValidationError:
                        self._message("Enter a model route and valid environment-variable name, or choose Not now.")
                        return
                self._step = min(3, self._step + 1)
            self._render_step()
            self.query_one(f"#setup-step-{self._step}").scroll_visible()
        elif action in {"setup-probe", "setup-preview", "setup-apply"}:
            if action == "setup-apply" and (self._step != 3 or not self._environment_ready()):
                return
            self._operate(action)

    @work(exclusive=True, group="setup-operation")
    async def _operate(self, action: str) -> None:
        self._busy = action
        self._render_step()
        try:
            if action == "setup-probe":
                self._ready_roots = None
                roots = self._roots()
                self._message("Checking production Sandbox readiness... up to 90 seconds per root.")
                for root in roots:
                    result = await self._application.preflight_environment("environment-sandbox", project_path=root)
                    self._message(result.message + "\n" + "\n".join(result.instructions))
                    if not result.ready:
                        return
                if roots == self._roots():
                    self._ready_roots = roots
                return
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
            self._message(
                f"Review {len(preview.files)} file(s). Preserved {len(preview.preserved_paths)} existing resource(s). Finish setup to publish."
            )
        except (AgentUiError, ValidationError) as exc:
            if action == "setup-apply":
                self._invalidate()
            self._message(
                str(exc)
                if isinstance(exc, AgentUiError)
                else "Choose valid model, Agent, directory, and environment selections."
            )
        finally:
            self._busy = ""
            if self.is_mounted:
                self._render_step()

    def action_cancel(self) -> None:
        if self._busy == "setup-apply":
            self._message("Wait for publication to finish. Cancellation cannot roll back written files.")
            return
        self.workers.cancel_group(self, "setup-operation")
        self.dismiss(False)

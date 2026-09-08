"""Single-screen onboarding over the App's existing publication boundary."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from anyio import to_thread
from prompt_toolkit.application import Application
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.key_binding import KeyBindings, KeyPressEvent
from prompt_toolkit.layout import ConditionalContainer, HSplit, Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.styles import Style
from prompt_toolkit.utils import get_cwidth
from prompt_toolkit.widgets import TextArea

from .rendering import terminal_text
from .selection import Choice, Selection, resolve_choice
from .setup import Question, SetupWizard
from .theme import prompt_toolkit_style_rules, resolve_theme

if TYPE_CHECKING:
    from a13n_harness_ui.app import HarnessUiApp
    from a13n_harness_ui.setup import SetupStatus

    from .backend import SessionBackend
    from .shell import CliShell


class SetupCancelled(Exception):
    """Leave setup without requesting further publication."""


class SetupBack(Exception):
    """Return to the preceding explicit selection."""


class LandingScreen:
    """One terminal owner across loading, questions, account actions, and preview."""

    def __init__(self) -> None:
        self.title = "Harness UI · Startup"
        self.notice = "Checking local configuration…"
        self.question: Question | None = None
        self.selection: Selection | None = None
        self.cancel_requested = False
        self._answer: asyncio.Future[str] | None = None
        self._task: asyncio.Task[None] | None = None
        self._owner: asyncio.Task[object] | None = None
        self._ready = asyncio.Event()
        self.field = TextArea(
            multiline=False,
            prompt="> ",
            height=1,
            password=Condition(lambda: self.question is not None and self.question.password),
        )
        keys = KeyBindings()

        @keys.add("enter", eager=True)
        def confirm(event: KeyPressEvent) -> None:
            if self._answer is None or self._answer.done() or self.question is None:
                return
            value = self.field.text.strip()
            if not value:
                value = self.selection.answer() if self.selection is not None else self.question.default
            self._answer.set_result(value)

        @keys.add("up", filter=Condition(lambda: self.selection is not None and not self.field.text))
        def previous(event: KeyPressEvent) -> None:
            assert self.selection is not None
            self.selection.move(-1)

        @keys.add("down", filter=Condition(lambda: self.selection is not None and not self.field.text))
        def following(event: KeyPressEvent) -> None:
            assert self.selection is not None
            self.selection.move(1)

        @keys.add("escape")
        def back(event: KeyPressEvent) -> None:
            if self._answer is not None and not self._answer.done():
                self._answer.set_exception(SetupBack())

        @keys.add("c-c")
        @keys.add("c-d")
        def cancel(event: KeyPressEvent) -> None:
            if self._answer is not None and not self._answer.done():
                self._answer.set_exception(SetupCancelled())
            elif self._owner is not None:
                self.cancel_requested = True
                self._owner.cancel()

        self.application: Application[None] = Application(
            layout=Layout(
                HSplit(
                    [
                        Window(height=1),
                        Window(
                            FormattedTextControl(lambda: "  " + self.title),
                            height=1,
                            style="class:session-selector.title",
                        ),
                        Window(height=1),
                        Window(
                            FormattedTextControl(lambda: "  " + terminal_text(self.notice)),
                            wrap_lines=True,
                            dont_extend_height=True,
                        ),
                        Window(
                            FormattedTextControl(
                                lambda: "  " + terminal_text(self.question.text) if self.question else ""
                            ),
                            wrap_lines=True,
                            dont_extend_height=True,
                        ),
                        Window(height=1),
                        Window(FormattedTextControl(self._choices), wrap_lines=True, dont_extend_height=True),
                        ConditionalContainer(self.field, filter=Condition(lambda: self.question is not None)),
                        Window(),
                        Window(
                            FormattedTextControl(
                                lambda: (
                                    "  Enter continue · Esc back · Ctrl+C cancel"
                                    if self.question
                                    else "  Working · Ctrl+C cancels"
                                )
                            ),
                            height=1,
                        ),
                    ]
                ),
                focused_element=self.field,
            ),
            key_bindings=keys,
            full_screen=True,
            mouse_support=False,
            style=Style.from_dict(
                {
                    **prompt_toolkit_style_rules(resolve_theme("auto")),
                    "selection.focus": "reverse",
                    "selection.description": "fg:ansibrightblack",
                    "selection.hint": "fg:ansibrightblack",
                }
            ),
        )

    def _choices(self) -> FormattedText:
        if self.selection is not None:
            size = self.application.output.get_size()
            columns = max(1, size.columns)

            def wrapped_rows(text: str) -> int:
                return sum(max(1, (get_cwidth(line) + columns - 1) // columns) for line in text.splitlines())

            header = wrapped_rows("  " + self.notice)
            if self.question is not None:
                header += wrapped_rows("  " + terminal_text(self.question.text))
            # Title/spacers, one input row, and the footer own six fixed rows.
            available = max(1, size.rows - 6 - header)
            lines = self.selection.lines(max_choices=1, descriptions=True)[:-1]
            for count in range(2, min(len(self.selection.choices), available) + 1):
                candidate = self.selection.lines(max_choices=count, descriptions=True)[:-1]
                if wrapped_rows("".join(text for _style, text in candidate)) > available:
                    break
                lines = candidate
            return FormattedText(lines)
        if self.question is not None:
            return FormattedText([("", f"Default: {terminal_text(self.question.default) or '(none)'}")])
        return FormattedText([])

    def emit(self, text: str) -> None:
        self.notice = terminal_text(text)
        self.application.invalidate()

    async def ask(self, question: Question, selection: Selection | None = None) -> str:
        self.question, self.selection = question, selection
        self.field.text = ""
        self._answer = asyncio.get_running_loop().create_future()
        self.application.layout.focus(self.field)
        self.application.invalidate()
        try:
            return await self._answer
        finally:
            self.field.buffer.reset()
            self.question, self.selection, self._answer = None, None, None
            self.application.invalidate()

    async def __aenter__(self) -> LandingScreen:
        self._owner = asyncio.current_task()
        self._task = asyncio.create_task(
            self.application.run_async(pre_run=self._ready.set, set_exception_handler=False)
        )
        self._task.add_done_callback(lambda task: self._ready.set())
        await self._ready.wait()
        if self._task.done():
            self._task.result()
        return self

    async def run_chat(self, shell: CliShell, backend: SessionBackend) -> None:
        """Replace the startup view without leaving the alternate screen."""
        assert self._task is not None
        self.application.layout = shell.app.layout
        self.application.key_bindings = shell.app.key_bindings
        self.application.style = shell.app.style
        self.application.mouse_support = shell.app.mouse_support
        self.application.renderer.mouse_support = shell.app.mouse_support
        self.application.min_redraw_interval = shell.app.min_redraw_interval
        shell.app = self.application
        self.application.invalidate()
        await shell.run(backend, terminal_task=self._task)

    async def close(self) -> None:
        if self._task is not None:
            if not self._task.done():
                self.application.exit()
            try:
                await self._task
            finally:
                self._task = None

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        await self.close()


type Ask = Callable[[Question, Selection | None], Awaitable[str]]


async def _choose(ask_user: Ask, title: str, choices: tuple[Choice, ...]) -> str:
    values = tuple(choice.value for choice in choices)
    while True:
        answer = await ask_user(Question("action", title, values[0], values), Selection(choices, cursor=0))
        value = resolve_choice(answer, values)
        if isinstance(value, str) and value in values:
            return value


def _describe_accounts(status: SetupStatus) -> dict[str, str]:
    return {
        account.provider: (
            "Use existing login (refreshes when needed)"
            if account.available and account.action == "refresh"
            else "Use existing login"
            if account.available
            else account.diagnostic or f"Account action: {account.action}"
        )
        for account in status.providers
    }


async def _ensure_account(app: HarnessUiApp, provider: str, ask_user: Ask, emit: Callable[[str], None]) -> None:
    while True:
        status = await app.setup_status(rediscover=True)
        account = next(item for item in status.providers if item.provider == provider)
        if account.available:
            emit(f"Using existing {provider.title()} login. No new sign-in is needed.")
            return
        emit(account.diagnostic or f"{provider.title()} account requires: {account.action}.")
        emit(f"Sign in outside this TUI: a13n-harness-ui login {provider}")
        action = await _choose(
            ask_user,
            f"Connect {provider.title()}",
            (
                Choice("retry", "Check again", "After signing in externally or fixing the account store"),
                Choice(
                    "later",
                    "Configure without signing in",
                    "Authenticate with a13n-harness-ui login before using this model",
                ),
            ),
        )
        if action == "later":
            return


async def run_setup(
    app: HarnessUiApp,
    directory: Path,
    *,
    ask_user: Ask,
    emit: Callable[[str], None],
    environment: str | None = None,
    add_agent: bool = False,
    add_model: bool = False,
    advanced: bool = False,
) -> bool:
    """Publish the completed choices. Cancellation never starts chat."""
    from pydantic import SecretStr

    from a13n_harness_ui.configuration.setup import SetupSelection
    from a13n_harness_ui.model_accounts.api_keys import ApiKeyInput

    emit(
        "Add an agent · Choose an existing Model or create a new one. Existing agents and defaults stay unchanged."
        if add_agent
        else "Add a model · Only a Model is created; agents and defaults stay unchanged."
        if add_model
        else "Connect a model and review its settings. Saved after the final choice · Ctrl+C to cancel."
    )
    status = await app.setup_status(rediscover=True)
    if status.diagnostic:
        emit(f"Configuration needs repair: {status.diagnostic}\nFix the source and run a13n-harness-ui setup again.")
        return False
    if (add_agent or add_model) and status.needed:
        emit("Run a13n-harness-ui setup first, then add models or agents.")
        return False
    configuration = await app.current_configuration() if add_agent or add_model else None
    models = configuration.models if configuration is not None else {}
    wizard = SetupWizard(
        add_agent=add_agent,
        add_model=add_model,
        model_choices=tuple(Choice(model.id, model.name, model.route) for model in models.values()),
        subscription_models=frozenset(model.id for model in models.values() if model.authentication.kind != "api_key"),
        advanced=advanced,
        default_provider=next((item.provider for item in status.providers if item.available), "codex"),
        default_environment="sandbox"
        if (environment or status.environment_profile) == "environment-sandbox"
        else "full-control",
        provider_descriptions=_describe_accounts(status),
        existing_agent_ids=frozenset(status.agents),
    )
    checked_provider: str | None = None
    try:
        while True:
            try:
                question = wizard.question
                if question is not None:
                    emit(wizard.notice())
                    answer = await ask_user(question, wizard.selection_prompt())
                    if (
                        question.key == "credential"
                        and answer.strip()
                        and not answer.strip().startswith(("env:", "key:"))
                    ):
                        if len(answer) > 32768 or any(c.isspace() for c in answer.strip()):
                            raise ValueError(
                                "Enter a non-empty API key without whitespace (at most 32,768 characters)."
                            )
                        reference = f"key-{uuid4().hex[:16]}"
                        await app.put_api_key(ApiKeyInput(credential_ref=reference, key=SecretStr(answer.strip())))
                        answer = f"key:{reference}"
                    wizard.accept(answer)
                    answer = ""
                    if question.key == "model" and wizard.values.get("provider") == "api":
                        from a13n_harness_ui.model_presets import known_context_window

                        wizard.context_window_hint = await to_thread.run_sync(
                            known_context_window,
                            wizard.values["api_provider"],
                            wizard.values["model"],
                            wizard.values["base_url"],
                        )
                    if question.key == "provider" and wizard.values["provider"] != "api":
                        await _ensure_account(app, wizard.values["provider"], ask_user, emit)
                        checked_provider = wizard.values["provider"]
                    if (add_agent or add_model) and (
                        question.key == "model"
                        or (question.key == "model_source" and wizard.existing_model_id is not None)
                    ):
                        from a13n_harness_ui.model_presets import connection_display_name

                        if wizard.existing_model_id is not None:
                            base = models[wizard.existing_model_id].name[:110]
                        else:
                            provider = wizard.values["provider"]
                            route = (
                                wizard.values["api_provider"]
                                if provider == "api"
                                else "grok-subscription"
                                if provider == "grok"
                                else provider
                            )
                            base = connection_display_name(route, wizard.values["model"])
                        if add_agent:
                            base += " · Coding"
                        name, number = base, 2
                        names = {model.name for model in models.values()} if add_model else set(status.agents.values())
                        while name in names:
                            name = f"{base} {number}"
                            number += 1
                        wizard.suggested_name = name
                    continue
                provider = wizard.values.get("provider", "existing")
                if provider in {"codex", "grok"} and provider != checked_provider:
                    await _ensure_account(app, provider, ask_user, emit)
                    checked_provider = provider
                selection = SetupSelection.model_validate(wizard.selection(str(directory)))
                if not (add_agent or add_model):
                    projects = await app.cwd_project_ids(directory)
                    if len(projects) > 1:
                        raise ValueError(
                            "Multiple Projects use this default directory. Resolve their roots before setup."
                        )
                    if projects:
                        selection = selection.model_copy(update={"project": projects[0]})
                preview = await app.preview_setup(selection)
                wizard.preview_generation = preview.generation
                emit("Saving agent…" if add_agent else "Saving model…" if add_model else "Saving your configuration…")
                if not (add_agent or add_model) and selection.environment_profile == "environment-sandbox":
                    emit("Checking Sandbox prerequisites. Ctrl+C cancels; no fallback to Full Control.")
                    for root in preview.project_paths:
                        ready = await app.preflight_environment("environment-sandbox", project_path=root)
                        if not ready.ready:
                            raise ValueError(ready.message + "\n" + "\n".join(ready.instructions))
                publication = await app.apply_setup(selection, expected_generation=preview.generation)
                if not publication.completed:
                    raise ValueError(publication.error_message or "Setup publication is incomplete.")
                emit(
                    f"Added {selection.new_agent_name}. Select it with /agent {selection.new_agent_id}."
                    if add_agent
                    else f"Added Model {selection.new_model_name} ({selection.new_model_id}). Use it when adding an agent."
                    if add_model
                    else "Ready. Use /agent to switch agents, /help for shortcuts."
                )
                return True
            except SetupBack:
                if not wizard.back():
                    raise SetupCancelled() from None
            except Exception as exc:
                if isinstance(exc, SetupCancelled):
                    raise
                emit(f"Setup could not complete: {exc}\nNo automatic retry. Completed writes are retained.")
                action = await _choose(
                    ask_user,
                    "Next action",
                    (
                        Choice("retry", "Try saving again"),
                        Choice("back", "Change selections"),
                        Choice("cancel", "Cancel"),
                    ),
                )
                if action == "cancel":
                    raise SetupCancelled() from None
                if action == "back":
                    wizard.back()
    except (SetupCancelled, SetupBack):
        emit("Setup cancelled. Completed credential or configuration writes are retained.")
        return False

"""Selectable setup conversation. Selection is inert until an explicit publication."""

from __future__ import annotations

from dataclasses import dataclass, field

from a13n_ui.environment_profiles import WINDOWS_EXECUTION_NOTICE, local_sandbox_supported

from .selection import Choice, Selection, resolve_choice


@dataclass(frozen=True, slots=True)
class ContextPreset:
    name: str
    tokens: int
    explanation: str


CONTEXT_PRESETS = (
    ContextPreset("standard", 272000, "Codex catalog default; conservative working budget."),
    ContextPreset("balanced", 350000, "YAACLI-sized working budget; recommended for repository work."),
    ContextPreset("extended", 872000, "Catalog maximum, not an entitlement guarantee; higher latency and cost."),
)


@dataclass(frozen=True, slots=True)
class Question:
    key: str
    text: str
    default: str
    choices: tuple[str, ...] = ()


_QUESTIONS = (
    Question("access", "Step 1/3 · Connect a model", "byos", ("byos", "byok")),
    Question(
        "provider",
        "Subscription provider (BYOS)",
        "codex",
        ("codex", "grok"),
    ),
    Question("route", "API model route (for example openai-responses:gpt-5.6-sol)", "openai-responses:gpt-5.6-sol"),
    Question(
        "credential",
        "Credential source: env:VARIABLE or key:credential-id. Never paste a secret here.\n"
        "Manage stored keys separately with `a13n-ui auth key set <id>`.",
        "env:OPENAI_API_KEY",
    ),
    Question("model", "Codex model", "gpt-5.6-sol", ("gpt-5.6-sol", "gpt-5.6-terra", "gpt-6-astra")),
    Question(
        "context",
        "Working context budget:\n"
        + "\n".join(f"  {preset.name}: {preset.tokens:,} — {preset.explanation}" for preset in CONTEXT_PRESETS)
        + "\nThese are local management budgets, not changes to provider limits.",
        "balanced",
        tuple(p.name for p in CONTEXT_PRESETS),
    ),
    Question(
        "thinking",
        "Reasoning effort (independent of concise/detailed display)",
        "high",
        ("low", "medium", "high", "xhigh"),
    ),
    Question(
        "environment",
        "Step 2/3 · Configure your coding agent\nExecution permissions:\n"
        "  full-control: commands and file edits run directly on this host.\n"
        "  sandbox: isolated Environment provider; readiness checked only when executing.\n"
        "No silent fallback between these modes.",
        "full-control",
        ("full-control", "sandbox"),
    ),
    Question(
        "review",
        "Review shell commands with a model and request approval for flagged/errors?\n"
        "Codex uses a separate Luna/low reviewer. This is not a filesystem sandbox.",
        "yes",
        ("yes", "no"),
    ),
    Question(
        "instructions", "Additional Agent instructions (optional; built-in system instructions remain active)", ""
    ),
)


@dataclass(slots=True)
class SetupWizard:
    values: dict[str, str] = field(default_factory=dict)
    index: int = 0
    history: list[int] = field(default_factory=list)
    preview_generation: str | None = None

    @property
    def question(self) -> Question | None:
        question = _QUESTIONS[self.index] if self.index < len(_QUESTIONS) else None
        if question is not None and question.key == "environment" and not local_sandbox_supported():
            return Question(
                "environment",
                "Step 2/3 · Configure your coding agent\n" + WINDOWS_EXECUTION_NOTICE,
                "full-control",
                ("full-control",),
            )
        return question

    def selection_prompt(self) -> Selection | None:
        question = self.question
        if question is None:
            return Selection(
                (
                    Choice("no", "Keep preview", "Do not write yet"),
                    Choice("yes", "Publish configuration", "Create missing files only"),
                )
            )
        if not question.choices:
            return None
        labels = {
            "byos": "BYOS — use a subscription account",
            "byok": "BYOK — use your API key",
        }
        return Selection(
            tuple(Choice(value, labels.get(value, value)) for value in question.choices),
            cursor=question.choices.index(question.default),
        )

    def back(self) -> bool:
        if not self.history:
            return False
        self.preview_generation = None
        self.index = self.history.pop()
        for question in _QUESTIONS[self.index :]:
            self.values.pop(question.key, None)
        return True

    def prompt(self) -> str:
        question = self.question
        if question is None:
            return "Publish these files? Choose Publish or type yes. /cancel leaves everything unchanged."
        return f"{question.text}\n[{question.default or 'keep built-in instructions'}] (Enter accepts; Esc goes back; /cancel leaves setup)"

    def accept(self, text: str) -> None:
        question = self.question
        if question is None:
            raise ValueError("Setup is awaiting publication confirmation.")
        selected = text.strip() or question.default
        if question.choices:
            selected = str(resolve_choice(selected, question.choices)).lower()
            if selected not in question.choices:
                raise ValueError(f"Choose one of: {', '.join(question.choices)}")
        if question.key == "credential":
            kind, separator, name = selected.partition(":")
            if not separator or kind not in {"env", "key"} or not name or any(c.isspace() for c in name):
                raise ValueError("Use env:VARIABLE or key:credential-id; do not enter the API key itself.")
        self.values[question.key] = selected
        self.history.append(self.index)
        self.index += 1
        if question.key == "access" and selected == "byok":
            self.values["provider"] = "api"
            self.index += 1
        provider = self.values.get("provider", "codex")
        while self.question is not None:
            key = self.question.key
            if key in {"route", "credential"} and provider != "api":
                self.index += 1
            elif key in {"model", "context", "thinking"} and provider != "codex":
                self.index += 1
            elif key == "review" and provider == "api":
                self.values[key] = "no"
                self.index += 1
            else:
                break

    def selection(self, directory: str) -> dict[str, object]:
        provider = self.values["provider"]
        result: dict[str, object] = {
            "providers": [provider] if provider != "api" else [],
            "default_agent": f"agent-{provider if provider != 'api' else 'api-key'}",
            "project_path": directory,
            "environment_profile": "environment-native"
            if self.values["environment"] == "full-control"
            else "environment-sandbox",
            "shell_review": self.values["review"] == "yes",
            "connect_default": True,
            "instructions": self.values.get("instructions", ""),
        }
        if provider == "codex":
            result.update(
                codex_model=self.values["model"],
                codex_thinking=self.values["thinking"],
                codex_context_window=next(p.tokens for p in CONTEXT_PRESETS if p.name == self.values["context"]),
                proactive_context_management_threshold=0.65,
                compact_threshold=0.90,
            )
        if provider == "api":
            kind, _, name = self.values["credential"].partition(":")
            result["api_key_model"] = {
                "route": self.values["route"],
                "authentication": {
                    "kind": "api_key",
                    "env" if kind == "env" else "credential_ref": name,
                },
            }
        return result

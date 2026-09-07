"""Inert setup choices shared by the standalone terminal wizard and its tests."""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from a13n_harness_ui.environment_profiles import WINDOWS_EXECUTION_NOTICE, local_sandbox_supported

from .selection import Choice, Selection, resolve_choice


@dataclass(frozen=True, slots=True)
class ContextPreset:
    name: str
    tokens: int
    explanation: str


CONTEXT_PRESETS = (
    ContextPreset("standard", 272000, "Codex catalog default; conservative working budget."),
    ContextPreset("balanced", 350000, "Recommended for repository work."),
    ContextPreset("extended", 872000, "Catalog maximum, not an entitlement guarantee; higher latency and cost."),
)


@dataclass(frozen=True, slots=True)
class Question:
    key: str
    text: str
    default: str
    choices: tuple[str, ...] = ()


_QUESTIONS = (
    Question("provider", "Connect a model", "codex", ("codex", "grok", "api")),
    Question("route", "API model route", "openai-responses:gpt-5.6-sol"),
    Question(
        "credential",
        "Credential reference: env:VARIABLE or key:credential-id. Never paste a secret here.\n"
        "Store keys separately with `a13n-harness-ui auth key set <id>`.",
        "env:OPENAI_API_KEY",
    ),
    Question(
        "environment",
        "Execution permissions",
        "full-control",
        ("full-control", "sandbox"),
    ),
    Question(
        "subagents",
        "Include the default subagents (code-reviewer, executor, explorer)?",
        "all",
        ("all", "none"),
    ),
    Question("model", "Codex model", "gpt-5.6-sol", ("gpt-5.6-sol", "gpt-5.6-terra", "gpt-6-astra")),
    Question(
        "context",
        "Working context budget (does not change provider limits)",
        "balanced",
        tuple(p.name for p in CONTEXT_PRESETS),
    ),
    Question("thinking", "Reasoning effort", "high", ("low", "medium", "high", "xhigh")),
    Question(
        "review", "Review shell commands; flagged commands and review errors require approval", "yes", ("yes", "no")
    ),
    Question("instructions", "Additional Agent instructions (optional)", ""),
)


@dataclass(slots=True)
class SetupWizard:
    values: dict[str, str] = field(default_factory=dict)
    index: int = 0
    history: list[int] = field(default_factory=list)
    preview_generation: str | None = None
    advanced: bool = False
    default_provider: str = "codex"
    default_environment: str = "full-control"
    provider_descriptions: dict[str, str] = field(default_factory=dict)

    @property
    def question(self) -> Question | None:
        if self.index >= len(_QUESTIONS) or (self.index >= 5 and not self.advanced):
            return None
        question = _QUESTIONS[self.index]
        default = self.values.get(question.key, question.default)
        if question.key == "provider":
            default = self.values.get("provider", self.default_provider)
        if question.key == "environment":
            default = self.values.get("environment", self.default_environment)
            if not local_sandbox_supported():
                return Question("environment", WINDOWS_EXECUTION_NOTICE, "full-control", ("full-control",))
        return replace(question, default=default)

    def selection_prompt(self) -> Selection | None:
        question = self.question
        if question is None or not question.choices:
            return None
        labels = {
            "codex": "Codex subscription",
            "grok": "Grok subscription",
            "api": "API key",
            "full-control": "Full Control",
            "sandbox": "Sandbox",
            "all": "Include all defaults",
            "none": "Do not include defaults",
        }
        descriptions = {
            **self.provider_descriptions,
            "api": "Use a stored key or environment variable",
            "full-control": "Run directly as your host account; not a sandbox",
            "sandbox": "Isolated execution; prerequisites checked before saving",
            "all": "Package-owned roles; choose individual names later in subagents.include",
            "none": "Keep only explicitly configured children",
            **{preset.name: f"{preset.tokens:,} tokens — {preset.explanation}" for preset in CONTEXT_PRESETS},
        }
        return Selection(
            tuple(Choice(value, labels.get(value, value), descriptions.get(value, "")) for value in question.choices),
            cursor=question.choices.index(question.default),
        )

    def customize(self) -> None:
        self.advanced = True
        self.index = 5
        self.preview_generation = None
        self._skip_irrelevant()

    def back(self) -> bool:
        if not self.history:
            return False
        self.preview_generation = None
        self.index = self.history.pop()
        return True

    def prompt(self) -> str:
        question = self.question
        if question is None:
            return "Review configuration before saving."
        return question.text

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
        if question.key == "provider" and self.values.get("provider") != selected:
            self.values.clear()
            self.advanced = False
        self.values[question.key] = selected
        self.preview_generation = None
        self.history.append(self.index)
        self.index += 1
        self._skip_irrelevant()

    def _skip_irrelevant(self) -> None:
        provider = self.values.get("provider", self.default_provider)
        while self.question is not None:
            key = self.question.key
            if key in {"route", "credential"} and provider != "api":
                self.index += 1
            elif key in {"model", "context", "thinking"} and provider != "codex":
                self.index += 1
            elif key == "review" and provider == "api":
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
            "shell_review": provider != "api" and self.values.get("review", "yes") == "yes",
            "connect_default": True,
            "include_default_subagents": self.values.get("subagents", "all") == "all",
            "instructions": self.values.get("instructions", ""),
        }
        if provider == "codex":
            result.update(
                codex_model=self.values.get("model", "gpt-5.6-sol"),
                codex_thinking=self.values.get("thinking", "high"),
                codex_context_window=next(
                    p.tokens for p in CONTEXT_PRESETS if p.name == self.values.get("context", "balanced")
                ),
                proactive_context_management_threshold=0.65,
                compact_threshold=0.90,
            )
        if provider == "api":
            kind, _, name = self.values["credential"].partition(":")
            result["api_key_model"] = {
                "route": self.values["route"],
                "authentication": {"kind": "api_key", "env" if kind == "env" else "credential_ref": name},
            }
        return result

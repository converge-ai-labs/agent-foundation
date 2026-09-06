"""Inline setup conversation. Selection is inert until an explicit publication."""

from __future__ import annotations

from dataclasses import dataclass, field


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
    Question(
        "provider",
        "Model access: codex subscription, grok subscription, or api key?",
        "codex",
        ("codex", "grok", "api"),
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
        "Execution permissions:\n"
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
)


@dataclass(slots=True)
class SetupWizard:
    values: dict[str, str] = field(default_factory=dict)
    index: int = 0
    preview_generation: str | None = None

    @property
    def question(self) -> Question | None:
        return _QUESTIONS[self.index] if self.index < len(_QUESTIONS) else None

    def prompt(self) -> str:
        question = self.question
        if question is None:
            return "Publish these files? Type yes to confirm, or /cancel to leave everything unchanged."
        return f"{question.text}\n[{question.default}] (Enter accepts; /cancel leaves setup)"

    def accept(self, text: str) -> None:
        question = self.question
        if question is None:
            raise ValueError("Setup is awaiting publication confirmation.")
        selected = text.strip() or question.default
        if question.choices and selected not in question.choices:
            raise ValueError(f"Choose one of: {', '.join(question.choices)}")
        if question.key == "credential":
            kind, separator, name = selected.partition(":")
            if not separator or kind not in {"env", "key"} or not name or any(c.isspace() for c in name):
                raise ValueError("Use env:VARIABLE or key:credential-id; do not enter the API key itself.")
        self.values[question.key] = selected
        self.index += 1
        provider = self.values["provider"]
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

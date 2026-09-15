"""Inert setup choices shared by the standalone terminal wizard and its tests."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from pydantic import JsonValue

from a13n_harness_ui.environment_profiles import WINDOWS_EXECUTION_NOTICE, local_sandbox_supported
from a13n_harness_ui.model_presets import (
    API_MODEL_SUGGESTIONS,
    API_PROVIDER_BY_ROUTE,
    API_PROVIDERS,
    known_model_capabilities,
    settings_presets,
    validate_base_url,
)
from a13n_harness_ui.tool_presets import NATIVE_TOOLS_DOCS, selected_tool_capabilities, tool_choices

from .selection import Choice, Selection, resolve_choice

if TYPE_CHECKING:
    from a13n_harness_ui.configuration.models import ModelResource


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
    password: bool = False
    allow_custom: bool = False
    multiple: bool = False


_QUESTIONS = (
    Question("model_source", "Choose a model for the new agent", "new"),
    Question("provider", "Connect a model", "codex", ("codex", "grok", "api")),
    Question(
        "api_provider", "Choose an API provider / protocol", "openai-responses", tuple(p.route for p in API_PROVIDERS)
    ),
    Question("base_url", "Base URL (HTTP or HTTPS; no credentials in the URL)", ""),
    Question(
        "session_affinity_header",
        "Gateway session affinity header (optional). A stable UUID derived from the current Thread ID is supplied automatically.\n"
        "Choose a preset or enter a custom header name; your gateway must be configured to use it.",
        "off",
        allow_custom=True,
    ),
    Question(
        "credential",
        "API key (hidden), or env:VARIABLE / key:credential-id.\n"
        "Entering a new key saves it immediately in the local key store, even if setup is later cancelled.",
        "",
        password=True,
    ),
    Question("model", "Choose a model", "gpt-5.6-sol", ("gpt-6-astra", "gpt-5.6-sol", "gpt-5.6-terra")),
    Question("preset", "Choose model settings (editable in the saved YAML)", ""),
    Question("fast", "Codex service tier (saved with this Model)", "on", ("on", "off")),
    Question(
        "subagents",
        "Include the default subagents (code-reviewer, executor, explorer)?",
        "all",
        ("all", "none"),
    ),
    Question(
        "context",
        "Working context budget (does not change provider limits)",
        "balanced",
        tuple(p.name for p in CONTEXT_PRESETS),
    ),
    Question("thinking", "Reasoning effort", "high", ("low", "medium", "high", "xhigh")),
    Question("tools", "Choose native Agent tools (Space toggles; Enter confirms; none keeps Host web only)", ""),
    Question(
        "file_stores",
        "Provider file/vector store IDs (comma-separated; upload and index files with the provider first)",
        "",
    ),
    Question(
        "mcp_url", "Remote MCP URL (provider-accessible HTTP/S; no credentials; public servers only in this wizard)", ""
    ),
    Question("mcp_id", "Remote MCP server label", "remote"),
    Question(
        "advisor_model",
        "Advisor model ID (provider model name, not a Harness UI Model ID; account access required)",
        "",
    ),
    Question(
        "review",
        "Initialize shell review for all Agents (existing root settings stay unchanged)",
        "yes",
        ("yes", "no"),
    ),
    Question("instructions", "Additional Agent instructions (optional)", ""),
    Question(
        "environment",
        "Execution permissions",
        "full-control",
        ("full-control", "sandbox"),
    ),
)


@dataclass(slots=True)
class SetupWizard:
    values: dict[str, str] = field(default_factory=dict)
    index: int = 0
    history: list[int] = field(default_factory=list)
    advanced: bool = False
    default_provider: str = "codex"
    default_environment: str = "full-control"
    provider_descriptions: dict[str, str] = field(default_factory=dict)
    add_agent: bool = False
    add_model: bool = False
    model_choices: tuple[Choice, ...] = ()
    model_resources: dict[str, ModelResource] = field(default_factory=dict)
    suggested_name: str = ""
    existing_agent_ids: frozenset[str] = frozenset()
    context_window_hint: int | None = None

    def __post_init__(self) -> None:
        self._skip_irrelevant()

    @property
    def existing_model_id(self) -> str | None:
        value = self.values.get("model_source", "new")
        return value if value != "new" else None

    def tool_connection(self) -> tuple[str, str, str | None]:
        if self.existing_model_id is not None:
            model = self.model_resources.get(self.existing_model_id)
            if model is None:
                return "", "", None
            base_url = model.model_configuration.get("base_url")
            return model.route, model.authentication.kind, base_url if isinstance(base_url, str) else None
        provider = self.values.get("provider", self.default_provider)
        route = (
            self.values.get("api_provider", "openai-responses")
            if provider == "api"
            else "openai-codex"
            if provider == "codex"
            else "grok"
        )
        return (
            f"{route}:{self.values.get('model', '')}",
            "api_key" if provider == "api" else f"{provider}_subscription",
            self.values.get("base_url"),
        )

    @property
    def question(self) -> Question | None:
        if self.index >= len(_QUESTIONS):
            return None
        question = _QUESTIONS[self.index]
        default = self.values.get(question.key, question.default)
        if question.key == "session_affinity_header":
            from a13n_harness.model_affinity import SESSION_AFFINITY_PRESETS

            return replace(question, default=default, choices=("off", *(p.header for p in SESSION_AFFINITY_PRESETS)))
        if question.key == "tools":
            route, authentication, base_url = self.tool_connection()
            options = tool_choices(route, authentication=authentication, base_url=base_url)
            default = self.values.get(
                "tools", ",".join(choice.key for choice in options if choice.recommended) or "none"
            )
            return Question("tools", question.text, default, tuple(choice.key for choice in options), multiple=True)
        if question.key == "model_source":
            choices = (*tuple(str(c.value) for c in self.model_choices), "new")
            return Question("model_source", question.text, self.values.get("model_source", choices[0]), choices)
        if question.key == "provider":
            default = self.values.get("provider", self.default_provider)
        if self.values.get("provider") == "api":
            provider = API_PROVIDER_BY_ROUTE[self.values.get("api_provider", "openai-responses")]
            if question.key == "base_url":
                default = self.values.get("base_url", provider.base_url)
            elif question.key == "credential":
                default = self.values.get("credential", f"env:{provider.credential_env}")
            elif question.key == "model":
                suggestions = API_MODEL_SUGGESTIONS[provider.route]
                selected = self.values.get("model", suggestions[0])
                choices = suggestions if selected in suggestions else (selected, *suggestions)
                return Question(
                    "model",
                    "Model ID (choose a suggestion or type a custom ID; no provider: prefix)",
                    selected,
                    choices,
                    allow_custom=True,
                )
            elif question.key == "context":
                recommended = min(350000, self.context_window_hint) if self.context_window_hint else 350000
                catalog_note = (
                    f"Bundled model context: {self.context_window_hint:,} tokens."
                    if self.context_window_hint
                    else "Model limit unknown; 350,000 is a working default, not a provider guarantee."
                )
                return Question(
                    "context",
                    "Working context budget (tokens, or e.g. 350k; editable)\n"
                    + catalog_note
                    + "\nSummary reminder at 65%; automatic compaction at 90%, using the default summary prompt.",
                    self.values.get("context", str(recommended)),
                )
            elif question.key == "preset":
                presets = settings_presets(provider.route, self.values.get("model", ""))
                return Question(
                    "preset", question.text, self.values.get("preset", presets[0].key), tuple(p.key for p in presets)
                )
        if question.key == "model" and self.values.get("provider") == "grok":
            return Question(
                "model",
                "Choose a model",
                self.values.get("model", "grok-4.6"),
                ("grok-4.6", "grok-4.5", "grok-4.20-0309-reasoning"),
            )
        if question.key == "environment" and (self.add_agent or self.add_model):
            return Question(
                "name",
                "Name this model" if self.add_model else "Name this agent",
                self.values.get("name", self.suggested_name or self.values.get("model", "New agent")),
            )
        if question.key == "environment":
            default = self.values.get("environment", self.default_environment)
            if not local_sandbox_supported():
                return Question("environment", WINDOWS_EXECUTION_NOTICE, "full-control", ("full-control",))
        return replace(question, default=default)

    @property
    def progress(self) -> tuple[int, int]:
        visible = tuple(index for index, question in enumerate(_QUESTIONS) if self._is_relevant(question.key))
        current = visible.index(self.index) + 1 if self.index < len(_QUESTIONS) else len(visible)
        return current, len(visible)

    def notice(self) -> str:
        if self.question is None:
            return "Ready to save."
        current, total = self.progress
        return f"{current} / {total} · {self._notice()}"

    def _notice(self) -> str:
        question = self.question
        if question is None:
            return "Ready to save."
        if question.key == "tools":
            route, authentication, _base_url = self.tool_connection()
            return (
                f"Tools for {route} ({authentication}). Recommendations are editable, not an account entitlement check.\n"
                "Host web is always included: public HTTP fetch/download, keyless search and HTML-to-Markdown.\n"
                "Native Search replaces Host search; native Web Fetch replaces Host scrape. No extra web keys needed.\n"
                "Memory requires a Host memory handler and is not available in this wizard. "
                "Listed native tools follow adapter support; provider/model restrictions still apply.\n"
                "Custom endpoints and unreviewed model IDs are not preselected: verify their native-tool support before enabling.\n"
                f"Provider requirements and all native options: {NATIVE_TOOLS_DOCS}"
            )
        if question.key in {"file_stores", "mcp_url", "mcp_id", "advisor_model"}:
            return "Configure the selected native tool · Esc returns to your choices. No remote resource is created."
        if question.key in {"environment", "name", "provider", "preset"}:
            hint = ""
            if question.key in {"environment", "name"}:
                hint += "Review your selection. "
                hint += "Saved after this choice."
                if self.existing_model_id is not None:
                    hint += f"\nUse existing Model: {self.existing_model_id}. Its settings stay unchanged."
                elif self.values["provider"] == "api":
                    preset = next(
                        p
                        for p in settings_presets(self.values["api_provider"], self.values["model"])
                        if p.key == self.values["preset"]
                    )
                    endpoint = self.values.get("base_url", "xAI SDK default (gRPC)")
                    hint += f"\n{self.values['api_provider']}:{self.values['model']}\nEndpoint: {endpoint}\nSettings: {json.dumps(preset.settings, sort_keys=True)}"
                    hint += f"\n{preset.output_limit_label}. Saved as editable settings; not adjusted during a run."
                    hint += f"\nContext: {int(self.values['context']):,} tokens · Summary reminder: 65% · Compact: 90%."
                else:
                    hint += f"\n{self.values['provider']}:{self.values['model']}"
                    if self.values["provider"] == "codex":
                        hint += f" · Thinking: {self.values.get('thinking', 'high')} · Reasoning summary: detailed."
                        hint += "\nService tier: " + (
                            "Fast (priority)." if self.values.get("fast", "on") == "on" else "Standard (default)."
                        )
                if not self.add_model:
                    hint += (
                        "\nShell review shortcut: extra-high risk requires approval; uses a Model request."
                        if self.values.get("review", "yes") == "yes"
                        else "\nShell review shortcut: off; explicit Agent policies remain active."
                    )
                    hint += " Existing root security.shell_review settings stay unchanged."
                if self.existing_model_id is None:
                    provider = self.values["provider"]
                    route_provider = (
                        self.values["api_provider"]
                        if provider == "api"
                        else "openai-codex"
                        if provider == "codex"
                        else "grok"
                    )
                    known = known_model_capabilities(f"{route_provider}:{self.values['model']}")
                    media = (
                        ", ".join(sorted(capability.value.removesuffix("_understanding") for capability in known))
                        or "none"
                        if known is not None
                        else "unknown; no native media enabled"
                    )
                    hint += f"\nNative media input: {media}. Editable in model_characteristics.capabilities."
                if not self.add_model:
                    hint += "\nAgent tools: " + self.values.get("tools", "recommended") + "."
                if self.add_agent:
                    hint += "\nCreates a new agent; existing agents and defaults stay unchanged."
                elif self.add_model:
                    hint += "\nCreates only a Model; no Agent or default is changed. Next, optionally set up an Agent and choose its tools."
            elif question.key == "provider":
                hint += "Reuse a subscription or connect an API key."
            elif question.key == "preset":
                hint += (
                    "Thinking and output limits are saved together as editable settings. Provider limits still apply.\n"
                    "Provider defaults adds no output cap; unreviewed models keep their existing preset behavior. "
                    "Custom endpoints may impose different limits."
                )
            return hint
        return "Esc goes back; Ctrl+C cancels."

    def selection_prompt(self) -> Selection | None:
        question = self.question
        if question is None or not question.choices:
            return None
        if question.key == "tools":
            route, authentication, base_url = self.tool_connection()
            options = tool_choices(route, authentication=authentication, base_url=base_url)
            selected = set(question.default.split(","))
            return Selection(
                tuple(
                    Choice(
                        option.key, option.label + (" (recommended)" if option.recommended else ""), option.description
                    )
                    for option in options
                ),
                cursor=0,
                multiple=True,
                empty_answer="none",
                checked={index for index, option in enumerate(options) if option.key in selected},
            )
        if question.key == "model_source":
            return Selection(
                (
                    *self.model_choices,
                    Choice("new", "Create a new model", "Configure a provider, credentials and settings"),
                ),
                cursor=question.choices.index(question.default),
            )
        if question.key == "model" and self.values.get("provider") == "api":
            return Selection(
                tuple(Choice(value, value) for value in question.choices),
                cursor=question.choices.index(question.default),
            )
        if question.key == "fast":
            return Selection(
                (
                    Choice(
                        "on",
                        "Fast (default)",
                        "Request priority service; may use more quota or cost more. Speed is not guaranteed.",
                    ),
                    Choice("off", "Standard", "Request the default service tier; keep the same model and reasoning."),
                ),
                cursor=question.choices.index(question.default),
            )
        if question.key == "session_affinity_header":
            from a13n_harness.model_affinity import SESSION_AFFINITY_PRESETS

            choices = (
                Choice("off", "Disabled", "Do not send a gateway affinity header"),
                *(Choice(p.header, f"{p.label} · {p.header}", p.description) for p in SESSION_AFFINITY_PRESETS),
            )
            if question.default not in question.choices:
                choices = (*choices, Choice(question.default, question.default, "Custom gateway header"))
            return Selection(
                choices, cursor=next(i for i, choice in enumerate(choices) if choice.value == question.default)
            )
        if question.key == "api_provider":
            return Selection(
                tuple(
                    Choice(
                        p.route,
                        p.label,
                        p.base_url or "Official gRPC endpoint; enables X Search and other native xAI tools",
                    )
                    for p in API_PROVIDERS
                ),
                cursor=question.choices.index(question.default),
            )
        if question.key == "preset":
            return Selection(
                tuple(
                    Choice(p.key, p.label, f"{p.description} · {p.output_limit_label}")
                    for p in settings_presets(self.values["api_provider"], self.values["model"])
                ),
                cursor=question.choices.index(question.default),
            )
        labels = {
            "codex": "Codex subscription",
            "grok": "Grok subscription",
            "api": "API key",
            "full-control": "Full Control",
            "sandbox": "Sandbox",
            "all": "Include all defaults",
            "none": "Do not include defaults",
            "gpt-6-astra": "GPT-6 Astra",
            "gpt-5.6-sol": "GPT-5.6 Sol",
            "gpt-5.6-terra": "GPT-5.6 Terra",
            "grok-4.6": "Grok 4.6",
            "grok-4.5": "Grok 4.5",
            "grok-4.20-0309-reasoning": "Grok 4.20 Reasoning",
        }
        descriptions = {
            **self.provider_descriptions,
            "gpt-6-astra": "Most capable · complex, end-to-end work",
            "gpt-5.6-sol": "Recommended · strong coding and reasoning",
            "gpt-5.6-terra": "Lighter · balance capability and cost",
            "grok-4.6": "Recommended · latest coding and agentic model",
            "grok-4.5": "Previous generation · configurable reasoning",
            "grok-4.20-0309-reasoning": "Earlier reasoning model · long-context work",
            "api": "Choose a provider, base URL, API key, model ID and settings",
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

    def back(self) -> bool:
        if not self.history:
            return False
        self.index = self.history.pop()
        return True

    def prompt(self) -> str:
        question = self.question
        if question is None:
            return "Ready to save."
        return question.text

    def accept(self, text: str) -> None:
        question = self.question
        if question is None:
            raise ValueError("Setup choices are complete.")
        selected = text.strip() or question.default
        if question.multiple:
            resolved = resolve_choice(selected, question.choices, multiple=True)
            keys = (
                resolved
                if isinstance(resolved, tuple)
                else tuple(part.strip() for part in resolved.split(","))
                if resolved != "none"
                else ()
            )
            if len(set(keys)) != len(keys) or any(key not in question.choices for key in keys):
                raise ValueError("Choose tool numbers or names separated by commas, or none.")
            route, _authentication, _base_url = self.tool_connection()
            if route.startswith("google:") and "file_search" in keys and any(key != "file_search" for key in keys):
                raise ValueError(
                    "Google File Search cannot combine with other native tools. Deselect native search/fetch/code; Host web remains available."
                )
            selected = ",".join(keys) or "none"
        elif question.choices:
            selected = str(resolve_choice(selected, question.choices))
            if not question.allow_custom and selected not in question.choices:
                raise ValueError(f"Choose one of: {', '.join(question.choices)}")
        if question.key == "context" and self.values.get("provider") == "api":
            tokens = selected.lower()
            multiplier = 1000 if tokens.endswith("k") else 1
            digits = tokens[:-1] if multiplier == 1000 else tokens
            if not digits.isascii() or not digits.isdecimal() or int(digits) <= 0:
                raise ValueError("Enter a positive whole token count, such as 350000 or 350k.")
            selected = str(int(digits) * multiplier)
        if question.key == "name" and (not selected or len(selected) > 128):
            raise ValueError("Choose a name between 1 and 128 characters.")
        if question.key in {"base_url", "mcp_url"}:
            validate_base_url(selected)
        if question.key == "session_affinity_header" and selected != "off":
            from a13n_harness.model_affinity import validate_session_affinity_header

            selected = validate_session_affinity_header(selected)
        if question.key == "file_stores":
            stores = [part.strip() for part in selected.split(",")]
            if not all(stores) or any(any(char.isspace() for char in store) for store in stores):
                raise ValueError("Enter existing provider store IDs, separated by commas.")
            selected = ",".join(stores)
        if question.key in {"mcp_id", "advisor_model"} and (not selected or any(char.isspace() for char in selected)):
            raise ValueError("Enter a non-empty provider identifier without whitespace.")
        if question.key == "model" and self.values.get("provider") == "api":
            if not selected or len(selected) > 480 or any(c.isspace() for c in selected) or ":" in selected:
                raise ValueError("Enter a model ID without whitespace or a provider prefix.")
        agent_route = (
            f"{self.values.get('api_provider', '')}:{selected}"
            if question.key == "model" and not self.add_model
            else self.model_resources[selected].route
            if question.key == "model_source" and selected in self.model_resources
            else ""
        )
        if agent_route.startswith("google:"):
            from pydantic_ai.profiles.google import google_model_profile

            if not (google_model_profile(agent_route.partition(":")[2]) or {}).get("supports_tools", True):
                raise ValueError(
                    "Gemini image-only models cannot use this coding Agent's function tools. Add the Model separately and author a dedicated Agent without function tools."
                )
        if question.key == "credential":
            kind, separator, name = selected.partition(":")
            if not separator or kind not in {"env", "key"} or not name or any(c.isspace() for c in name):
                raise ValueError("Use env:VARIABLE or key:credential-id; do not enter the API key itself.")
        if question.key == "model_source" and self.values.get("model_source") != selected:
            self.values.clear()
        if question.key == "provider" and self.values.get("provider") != selected:
            self.values = {key: value for key, value in self.values.items() if key == "model_source"}
        if question.key == "api_provider" and self.values.get("api_provider") != selected:
            self.values = {
                **{key: value for key, value in self.values.items() if key == "model_source"},
                "provider": "api",
            }
        if (
            question.key in {"model_source", "provider", "api_provider", "base_url", "model"}
            and self.values.get(question.key) != selected
        ):
            self.context_window_hint = None
            self.values.pop("context", None)
            for key in ("tools", "file_stores", "mcp_url", "mcp_id", "advisor_model"):
                self.values.pop(key, None)
        if question.key == "model" and self.values.get("model") != selected:
            self.values.pop("preset", None)
        self.values[question.key] = selected
        self.history.append(self.index)
        self.index += 1
        self._skip_irrelevant()

    def _skip_irrelevant(self) -> None:
        while self.index < len(_QUESTIONS) and not self._is_relevant(_QUESTIONS[self.index].key):
            self.index += 1

    def _is_relevant(self, key: str) -> bool:
        """One visibility rule for navigation and progress, including conditional resource steps."""
        provider = self.values.get("provider", self.default_provider)
        if (
            key in {"subagents", "context", "thinking", "review", "instructions"}
            and not self.advanced
            and not (key == "context" and provider == "api")
        ):
            return False
        if key == "model_source" and not self.add_agent:
            return False
        if self.existing_model_id is not None and key in {
            "provider",
            "api_provider",
            "base_url",
            "session_affinity_header",
            "credential",
            "model",
            "preset",
            "fast",
            "context",
            "thinking",
        }:
            return False
        if key == "subagents" and (self.add_agent or self.add_model):
            return False
        resources = {
            "file_stores": "file_search",
            "mcp_url": "mcp_server",
            "mcp_id": "mcp_server",
            "advisor_model": "advisor",
        }
        if key in resources:
            return not self.add_model and resources[key] in self.values.get("tools", "").split(",")
        if self.add_model and key in {"review", "instructions", "tools"}:
            return False
        if key == "tools":
            route, authentication, base_url = self.tool_connection()
            return bool(tool_choices(route, authentication=authentication, base_url=base_url))
        if key == "base_url" and self.values.get("api_provider") == "xai":
            return False
        if (
            key == "session_affinity_header"
            and not API_PROVIDER_BY_ROUTE[self.values.get("api_provider", "openai-responses")].supports_session_affinity
        ):
            return False
        if key == "fast" and provider != "codex":
            return False
        if key in {"api_provider", "base_url", "session_affinity_header", "credential", "preset"} and provider != "api":
            return False
        if (key == "context" and provider not in {"codex", "api"}) or (key == "thinking" and provider != "codex"):
            return False
        return True

    def selection(self, directory: str) -> dict[str, object]:
        provider = self.values.get("provider", "existing")
        result: dict[str, object] = {
            "providers": [provider] if provider in {"codex", "grok"} else [],
            "default_agent": f"agent-{provider if provider != 'api' else 'api-key'}",
            "environment_profile": "environment-native"
            if self.values.get("environment", self.default_environment) == "full-control"
            else "environment-sandbox",
            "shell_review": not self.add_model and self.values.get("review", "yes") == "yes",
            "connect_default": True,
            "include_default_subagents": self.values.get("subagents", "all") == "all",
            "instructions": self.values.get("instructions", ""),
        }
        if self.add_agent or self.add_model:
            name = self.values["name"]
            slug = (
                re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:80]
                or hashlib.sha256(name.encode()).hexdigest()[:12]
            )
            prefix = "model" if self.add_model else "agent"
            occupied = (
                {str(choice.value) for choice in self.model_choices} if self.add_model else self.existing_agent_ids
            )
            resource_id, number = f"{prefix}-{slug}", 2
            while resource_id in occupied:
                resource_id = f"{prefix}-{slug}-{number}"
                number += 1
            result.update(
                {
                    f"new_{prefix}_id": resource_id,
                    f"new_{prefix}_name": name,
                    "include_default_subagents": None,
                    "connect_default": False,
                }
            )
        if not self.add_model and "tools" in self.values:
            _route, authentication, _base_url = self.tool_connection()
            selected = tuple(key for key in self.values["tools"].split(",") if key != "none")
            stores: list[JsonValue] = list(self.values.get("file_stores", "").split(","))
            parameters: dict[str, dict[str, JsonValue]] = {
                "file_search": {"file_store_ids": stores},
                "mcp_server": {"id": self.values.get("mcp_id", "remote"), "url": self.values.get("mcp_url", "")},
                "advisor": {"model": self.values.get("advisor_model", "")},
            }
            result["tool_capabilities"] = selected_tool_capabilities(
                selected, authentication=authentication, parameters=parameters
            )
        if self.existing_model_id is not None:
            result["existing_model_id"] = self.existing_model_id
            return result
        if provider == "grok":
            result["grok_model"] = self.values.get("model", "grok-4.6")
        if provider == "codex":
            result.update(
                codex_model=self.values.get("model", "gpt-5.6-sol"),
                codex_thinking=self.values.get("thinking", "high"),
                codex_service_tier="priority" if self.values.get("fast", "on") == "on" else "default",
                codex_context_window=next(
                    p.tokens for p in CONTEXT_PRESETS if p.name == self.values.get("context", "balanced")
                ),
                proactive_context_management_threshold=0.65,
                compact_threshold=0.90,
            )
        if provider == "api":
            kind, _, name = self.values["credential"].partition(":")
            result["api_key_model"] = {
                "route": f"{self.values['api_provider']}:{self.values['model']}",
                "authentication": {"kind": "api_key", "env" if kind == "env" else "credential_ref": name},
                "model_configuration": {
                    **({"base_url": self.values["base_url"]} if "base_url" in self.values else {}),
                    **(
                        {"session_affinity_header": self.values["session_affinity_header"]}
                        if self.values.get("session_affinity_header", "off") != "off"
                        else {}
                    ),
                },
                "model_characteristics": {
                    "context_window_tokens": int(self.values["context"]),
                    "proactive_context_management_threshold": 0.65,
                    "compact_threshold": 0.90,
                },
                "settings": next(
                    p.settings
                    for p in settings_presets(self.values["api_provider"], self.values["model"])
                    if p.key == self.values["preset"]
                ),
            }
        return result

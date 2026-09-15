"""Canonical composition of Model declarations and Agent context policy."""

from a13n_harness import HarnessModelCharacteristics

from a13n_service.models.domain import ModelDeclarations

from .domain import AgentModelCharacteristics


def compose_model_characteristics(
    declarations: ModelDeclarations,
    policy: AgentModelCharacteristics | None = None,
) -> HarnessModelCharacteristics:
    """Resolve Harness characteristics without mixing declarations into native settings."""

    selected = policy or AgentModelCharacteristics()
    return HarnessModelCharacteristics(
        capabilities=declarations.capabilities,
        context_window_tokens=(
            selected.context_window_tokens
            if selected.context_window_tokens is not None
            else declarations.context_window_tokens
        ),
        proactive_context_management_threshold=selected.proactive_context_management_threshold,
        compact_threshold=selected.compact_threshold,
    )

"""Installed Pydantic AI base-model directory and authoring inference."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from pydantic_ai.models import infer_model_profile, known_model_names
from pydantic_ai.profiles import ModelProfile
from pydantic_ai.providers.bedrock_mantle import bedrock_mantle_model_profile

from .domain import BaseModelCandidate, BaseModelCandidateCollection
from .model_apis import BUILT_IN_MODEL_APIS

type MatchSource = Literal["none", "explicit", "exact", "normalized", "name_tokens", "ambiguous"]

_DEFAULT_API_BY_NAMESPACE = {
    "anthropic": "anthropic.messages",
    "bedrock": "bedrock.converse",
    "deepseek": "openai.chat_completions",
    "google": "google.generate_content",
    "google-cloud": "google.generate_content",
    "moonshotai": "openai.chat_completions",
    "openai": "openai.responses",
    "openai-chat": "openai.chat_completions",
    "zai": "openai.chat_completions",
}
_ACTUAL_NAMESPACE_BY_PROVIDER = {
    "anthropic": "anthropic",
    "deepseek": "deepseek",
    "google_gemini": "google",
    "google_vertex": "google-cloud",
    "moonshot": "moonshotai",
    "zhipu": "zai",
}
_CATALOG_NAMESPACE = {
    "anthropic": "anthropic",
    "deepseek": "deepseek",
    "google": "google",
    "google-cloud": "google",
    "moonshotai": "moonshotai",
    "openai": "openai",
    "openai-chat": "openai",
    "zai": "zhipuai",
}
_API_FAMILY = {
    "anthropic.messages": "anthropic",
    "bedrock.converse": "bedrock",
    "bedrock_mantle.chat_completions": "openai_chat",
    "bedrock_mantle.responses": "openai_responses",
    "google.generate_content": "google",
    "ollama.chat_completions": "openai_chat",
    "openai.chat_completions": "openai_chat",
    "openai.responses": "openai_responses",
    "openrouter.chat_completions": "openai_chat",
}
_EQUIVALENT_NAMESPACE_GROUPS = (("openai", "openai-chat"), ("google", "google-cloud"))
_EQUIVALENT_NAMESPACES = {namespace: group for group in _EQUIVALENT_NAMESPACE_GROUPS for namespace in group}


@dataclass(frozen=True, slots=True)
class BaseModelReference:
    base_model: str
    namespace: str | None
    model_name: str
    default_model_api: str | None
    catalog_model_id: str | None


@dataclass(frozen=True, slots=True)
class BaseModelSelection:
    reference: BaseModelReference
    model_api: str | None


@dataclass(frozen=True, slots=True)
class BaseModelResolution:
    source: MatchSource
    items: tuple[BaseModelSelection, ...] = ()


class BaseModelDirectory:
    """One immutable view over the installed Pydantic AI model directory."""

    def __init__(self, names: Sequence[str] | None = None) -> None:
        selected_names = known_model_names() if names is None else names
        self._references = tuple(_reference(name) for name in selected_names)
        self._by_name = {reference.base_model: reference for reference in self._references}

    def candidates(self) -> BaseModelCandidateCollection:
        return BaseModelCandidateCollection(
            items=tuple(
                BaseModelCandidate(
                    base_model=reference.base_model,
                    inferred_model_api=reference.default_model_api,
                    model_api_label=_api_label(reference.default_model_api),
                )
                for reference in self._references
            )
        )

    def resolve(
        self,
        *,
        provider_type: str,
        provider_configuration: Mapping[str, object],
        supported_model_apis: Sequence[str],
        upstream_model: str,
        base_model: str | None,
        base_model_supplied: bool,
        model_api: str | None,
    ) -> BaseModelResolution:
        if base_model_supplied:
            if base_model is None:
                return BaseModelResolution(source="none")
            reference = self.require(base_model)
            return BaseModelResolution(
                source="explicit",
                items=(
                    BaseModelSelection(
                        reference,
                        model_api or _compatible_model_api(reference.default_model_api, supported_model_apis),
                    ),
                ),
            )

        ranked: list[tuple[int, int, BaseModelReference]] = []
        for reference in self._references:
            if reference.namespace is None or reference.namespace.startswith("gateway/"):
                continue
            rank = _match_rank(upstream_model, reference)
            if rank is not None:
                ranked.append((*rank, reference))
        if not ranked:
            return BaseModelResolution(source="none")

        best_quality = max((quality, length) for quality, length, _ in ranked)
        winners = [reference for quality, length, reference in ranked if (quality, length) == best_quality]
        supported_references = [reference for reference in winners if reference.default_model_api is not None]
        if supported_references:
            winners = supported_references
        actual_namespace = _actual_namespace(provider_type, provider_configuration, model_api)
        actual = [reference for reference in winners if reference.namespace == actual_namespace]
        if actual:
            winners = actual
        winners = _collapse_equivalent_references(winners, supported_model_apis, model_api)
        selections = tuple(
            BaseModelSelection(
                reference,
                model_api or _compatible_model_api(reference.default_model_api, supported_model_apis),
            )
            for reference in sorted(winners, key=lambda item: item.base_model)
        )
        if len(selections) != 1:
            return BaseModelResolution(source="ambiguous", items=selections)
        return BaseModelResolution(source=_source(best_quality[0]), items=selections)

    def require(self, base_model: str) -> BaseModelReference:
        try:
            return self._by_name[base_model]
        except KeyError as error:
            raise ValueError("base_model must be an installed Pydantic AI model name") from error


def compatible_model_profile(base_model: str | None, model_api: str) -> ModelProfile | None:
    if base_model is None:
        return None
    reference = _reference(base_model)
    if _same_api_family(reference.default_model_api, model_api):
        return infer_model_profile(base_model)
    if reference.namespace in {"openai", "openai-chat"} and model_api in {
        "openai.responses",
        "openai.chat_completions",
    }:
        return infer_model_profile(base_model)
    return None


def _reference(base_model: str) -> BaseModelReference:
    namespace, separator, model_name = base_model.partition(":")
    if not separator:
        namespace = ""
        model_name = base_model
    default_model_api = _default_model_api(namespace, model_name)
    catalog_namespace = _CATALOG_NAMESPACE.get(namespace)
    return BaseModelReference(
        base_model=base_model,
        namespace=namespace or None,
        model_name=model_name,
        default_model_api=default_model_api,
        catalog_model_id=f"{catalog_namespace}/{model_name}" if catalog_namespace is not None else None,
    )


def _default_model_api(namespace: str, model_name: str) -> str | None:
    if namespace == "bedrock-mantle":
        interface = bedrock_mantle_model_profile(model_name).get("bedrock_mantle_interface")
        return "bedrock_mantle.chat_completions" if interface == "chat" else "bedrock_mantle.responses"
    return _DEFAULT_API_BY_NAMESPACE.get(namespace)


def _compatible_model_api(default_model_api: str | None, supported_model_apis: Sequence[str]) -> str | None:
    if default_model_api is None:
        return None
    if default_model_api in supported_model_apis:
        return default_model_api
    compatible = tuple(
        model_api for model_api in supported_model_apis if _same_api_family(default_model_api, model_api)
    )
    return compatible[0] if len(compatible) == 1 else None


def _same_api_family(left: str | None, right: str) -> bool:
    return left is not None and _API_FAMILY.get(left) == _API_FAMILY.get(right)


def _collapse_equivalent_references(
    winners: list[BaseModelReference],
    supported_model_apis: Sequence[str],
    explicit_model_api: str | None,
) -> list[BaseModelReference]:
    groups: dict[tuple[str, ...], list[BaseModelReference]] = {}
    for winner in winners:
        equivalent_namespaces = _EQUIVALENT_NAMESPACES.get(winner.namespace or "")
        key = (
            (equivalent_namespaces[0], winner.catalog_model_id or winner.model_name)
            if equivalent_namespaces
            else (winner.base_model,)
        )
        groups.setdefault(key, []).append(winner)

    collapsed: list[BaseModelReference] = []
    for variants in groups.values():
        if len(variants) == 1:
            collapsed.extend(variants)
            continue
        collapsed.append(
            min(
                variants,
                key=lambda item: _equivalent_rank(item, supported_model_apis, explicit_model_api),
            )
        )
    return collapsed


def _equivalent_rank(
    reference: BaseModelReference,
    supported_model_apis: Sequence[str],
    explicit_model_api: str | None,
) -> tuple[int, int]:
    namespace_order = _EQUIVALENT_NAMESPACES[reference.namespace or ""]
    if explicit_model_api is not None:
        api_rank = 0 if _same_api_family(reference.default_model_api, explicit_model_api) else 1
    else:
        selected_api = _compatible_model_api(reference.default_model_api, supported_model_apis)
        api_rank = supported_model_apis.index(selected_api) if selected_api is not None else len(supported_model_apis)
    return api_rank, namespace_order.index(reference.namespace or "")


def _match_rank(upstream_model: str, reference: BaseModelReference) -> tuple[int, int] | None:
    upstream_folded = upstream_model.casefold()
    if upstream_folded in {reference.base_model.casefold(), reference.model_name.casefold()}:
        return 3, len(_tokens(reference.model_name))
    upstream_tokens = _tokens(upstream_model)
    model_tokens = _tokens(reference.model_name)
    if upstream_tokens == model_tokens:
        return 2, len(model_tokens)
    if model_tokens and _contains_sequence(upstream_tokens, model_tokens):
        return 1, len(model_tokens)
    return None


def _tokens(value: str) -> tuple[str, ...]:
    return tuple(re.findall(r"[^\W_]+", value.casefold()))


def _contains_sequence(values: tuple[str, ...], candidate: tuple[str, ...]) -> bool:
    return any(values[index : index + len(candidate)] == candidate for index in range(len(values) - len(candidate) + 1))


def _actual_namespace(
    provider_type: str,
    configuration: Mapping[str, object],
    explicit_model_api: str | None,
) -> str | None:
    if any(key.endswith("base_url") and value for key, value in configuration.items()):
        return None
    if provider_type == "openai":
        return "openai-chat" if explicit_model_api == "openai.chat_completions" else "openai"
    if provider_type == "aws_bedrock":
        return (
            "bedrock-mantle"
            if explicit_model_api is not None and explicit_model_api.startswith("bedrock_mantle.")
            else "bedrock"
        )
    return _ACTUAL_NAMESPACE_BY_PROVIDER.get(provider_type)


def _api_label(model_api: str | None) -> str | None:
    return BUILT_IN_MODEL_APIS[model_api].display_name if model_api is not None else None


def _source(quality: int) -> MatchSource:
    if quality == 3:
        return "exact"
    if quality == 2:
        return "normalized"
    return "name_tokens"


__all__ = [
    "BaseModelDirectory",
    "BaseModelReference",
    "BaseModelResolution",
    "BaseModelSelection",
    "compatible_model_profile",
]

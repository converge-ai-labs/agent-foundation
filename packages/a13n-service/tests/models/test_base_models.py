from a13n_service.models.base_models import BaseModelDirectory, compatible_model_profile

NAMES = (
    "openai:gpt-5",
    "openai-chat:gpt-5",
    "openai:gpt-5-mini",
    "openai:gpt-5.5",
    "openai-chat:gpt-5.5",
    "xai:grok-4",
)


def _resolve(
    upstream_model: str,
    *,
    provider_type: str = "openai",
    configuration: dict[str, object] | None = None,
    supported: tuple[str, ...] = ("openai.responses", "openai.chat_completions"),
    base_model: str | None = None,
    base_model_supplied: bool = False,
    model_api: str | None = None,
):
    return BaseModelDirectory(NAMES).resolve(
        provider_type=provider_type,
        provider_configuration=configuration or {},
        supported_model_apis=supported,
        upstream_model=upstream_model,
        base_model=base_model,
        base_model_supplied=base_model_supplied,
        model_api=model_api,
    )


def test_actual_provider_and_explicit_api_select_pydantic_variant() -> None:
    inferred = _resolve("gpt_5")
    explicit_api = _resolve("gpt_5", model_api="openai.chat_completions")

    assert inferred.source == "normalized"
    assert inferred.items[0].reference.base_model == "openai:gpt-5"
    assert inferred.items[0].model_api == "openai.responses"
    assert explicit_api.items[0].reference.base_model == "openai-chat:gpt-5"
    assert explicit_api.items[0].model_api == "openai.chat_completions"


def test_separator_alias_longest_name_and_numeric_boundaries() -> None:
    dotted = _resolve("my-gpt-5-5")
    longest = _resolve("proxy-gpt-5-mini")
    fifty = _resolve("proxy-gpt-50")
    fifty_five = _resolve("proxy-gpt-55")

    assert dotted.items[0].reference.base_model == "openai:gpt-5.5"
    assert longest.items[0].reference.base_model == "openai:gpt-5-mini"
    assert fifty.source == "none"
    assert fifty_five.source == "none"


def test_custom_endpoint_equivalent_api_variants_use_default_or_explicit_routing() -> None:
    defaulted = _resolve("gpt-5", configuration={"base_url": "https://relay.example/v1"})
    selected = _resolve(
        "gpt-5",
        configuration={"base_url": "https://relay.example/v1"},
        model_api="openai.chat_completions",
    )

    assert defaulted.source == "exact"
    assert defaulted.items[0].reference.base_model == "openai:gpt-5"
    assert defaulted.items[0].model_api == "openai.responses"
    assert selected.items[0].reference.base_model == "openai-chat:gpt-5"


def test_explicit_api_applies_after_supported_identity_resolution() -> None:
    match = BaseModelDirectory(
        (
            "anthropic:claude-sonnet-4-5",
            "snowflake:claude-sonnet-4-5",
            "openai:gpt-5",
        )
    ).resolve(
        upstream_model="relay-claude-sonnet-4-5",
        provider_type="openai",
        provider_configuration={"base_url": "https://relay.example/v1"},
        supported_model_apis=("openai.responses", "openai.chat_completions"),
        model_api="openai.chat_completions",
        base_model=None,
        base_model_supplied=False,
    )

    assert match.source == "name_tokens"
    assert match.items[0].reference.base_model == "anthropic:claude-sonnet-4-5"
    assert match.items[0].model_api == "openai.chat_completions"


def test_identity_resolution_survives_unavailable_api_inference() -> None:
    match = BaseModelDirectory(("anthropic:claude-sonnet-4-6",)).resolve(
        upstream_model="my-claude-sonnet-4-6",
        provider_type="openai",
        provider_configuration={"base_url": "https://relay.example/v1"},
        supported_model_apis=("openai.responses", "openai.chat_completions"),
        model_api=None,
        base_model=None,
        base_model_supplied=False,
    )

    assert match.source == "name_tokens"
    assert match.items[0].reference.base_model == "anthropic:claude-sonnet-4-6"
    assert match.items[0].model_api is None


def test_equivalent_google_namespaces_collapse_for_custom_endpoint() -> None:
    match = BaseModelDirectory().resolve(
        upstream_model="my-gemini-2.5-pro",
        provider_type="google_gemini",
        provider_configuration={"base_url": "https://relay.example"},
        supported_model_apis=("google.generate_content",),
        model_api=None,
        base_model=None,
        base_model_supplied=False,
    )

    assert match.source == "name_tokens"
    assert match.items[0].reference.base_model == "google:gemini-2.5-pro"
    assert match.items[0].model_api == "google.generate_content"


def test_native_google_provider_retains_its_exact_namespace() -> None:
    match = BaseModelDirectory(("google:gemini-2.5-pro", "google-cloud:gemini-2.5-pro")).resolve(
        upstream_model="gemini-2.5-pro",
        provider_type="google_vertex",
        provider_configuration={},
        supported_model_apis=("google.generate_content",),
        model_api=None,
        base_model=None,
        base_model_supplied=False,
    )

    assert match.items[0].reference.base_model == "google-cloud:gemini-2.5-pro"


def test_explicit_google_base_model_preserves_selected_pydantic_name() -> None:
    match = BaseModelDirectory(("google:gemini-2.5-pro", "google-cloud:gemini-2.5-pro")).resolve(
        upstream_model="my-gemini-2.5-pro",
        provider_type="google_gemini",
        provider_configuration={"base_url": "https://relay.example"},
        supported_model_apis=("google.generate_content",),
        model_api=None,
        base_model="google-cloud:gemini-2.5-pro",
        base_model_supplied=True,
    )

    assert match.source == "explicit"
    assert match.items[0].reference.base_model == "google-cloud:gemini-2.5-pro"


def test_equal_quality_distinct_chat_identities_remain_ambiguous() -> None:
    match = BaseModelDirectory(("openai-chat:gpt-5", "deepseek:gpt-5")).resolve(
        upstream_model="gpt-5",
        provider_type="azure_openai",
        provider_configuration={},
        supported_model_apis=("openai.chat_completions",),
        model_api="openai.chat_completions",
        base_model=None,
        base_model_supplied=False,
    )

    assert match.source == "ambiguous"
    assert [item.reference.base_model for item in match.items] == [
        "deepseek:gpt-5",
        "openai-chat:gpt-5",
    ]


def test_explicit_reference_and_null_suppress_inference() -> None:
    explicit = _resolve(
        "custom",
        base_model="xai:grok-4",
        base_model_supplied=True,
        model_api="openai.responses",
    )
    custom = _resolve("gpt-5", base_model=None, base_model_supplied=True, model_api="openai.responses")

    assert explicit.source == "explicit"
    assert explicit.items[0].model_api == "openai.responses"
    assert custom.source == "none"


def test_candidates_and_compatible_profiles_come_from_pydantic_names_and_api() -> None:
    candidates = BaseModelDirectory(("openai:gpt-5", "xai:grok-4")).candidates().items

    assert [item.base_model for item in candidates] == ["openai:gpt-5", "xai:grok-4"]
    assert candidates[0].inferred_model_api == "openai.responses"
    assert candidates[0].model_api_label == "OpenAI Responses"
    assert candidates[1].inferred_model_api is None
    responses_profile = compatible_model_profile("openai:gpt-5", "openai.responses")
    chat_profile = compatible_model_profile("openai:gpt-5", "openai.chat_completions")
    deepseek_profile = compatible_model_profile("deepseek:deepseek-reasoner", "openai.chat_completions")
    assert responses_profile is not None and responses_profile["supports_thinking"] is True
    assert chat_profile is not None and chat_profile["supports_thinking"] is True
    assert deepseek_profile is not None
    assert deepseek_profile["supports_thinking"] is True
    assert deepseek_profile["thinking_always_enabled"] is True
    assert dict(deepseek_profile)["openai_chat_thinking_field"] == "reasoning_content"
    assert dict(deepseek_profile)["openai_supports_tool_choice_required"] is False
    assert compatible_model_profile("anthropic:claude-sonnet-4-5", "openai.chat_completions") is None


def test_bedrock_mantle_candidate_uses_pydantic_profile_interface() -> None:
    candidates = {
        item.base_model: item
        for item in BaseModelDirectory(
            (
                "bedrock-mantle:openai.gpt-5.6-sol",
                "bedrock-mantle:openai.gpt-oss-safeguard-20b",
            )
        )
        .candidates()
        .items
    }

    assert candidates["bedrock-mantle:openai.gpt-5.6-sol"].inferred_model_api == "bedrock_mantle.responses"
    assert (
        candidates["bedrock-mantle:openai.gpt-oss-safeguard-20b"].inferred_model_api
        == "bedrock_mantle.chat_completions"
    )

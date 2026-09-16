from a13n_service.models.catalog_identity import catalog_identity, model_identities


def test_native_and_regional_offerings_share_identity_without_collapsing_versions():
    identities = model_identities(
        {
            "anthropic/claude-opus-4-8": {"name": "Claude Opus 4.8"},
            "anthropic/claude-opus-5": {"name": "Claude Opus 5"},
        }
    )
    for provider, model in [
        ("anthropic", "claude-opus-4-8"),
        ("amazon-bedrock", "au.anthropic.claude-opus-4-8"),
        ("amazon-bedrock", "global.anthropic.claude-opus-4-8"),
        ("openrouter", "anthropic/claude-opus-4.8"),
        ("google-vertex", "claude-opus-4-8@default"),
    ]:
        assert catalog_identity(provider, model, "Channel name", identities) == (
            "anthropic/claude-opus-4-8",
            "Claude Opus 4.8",
        )
    assert catalog_identity("anthropic", "claude-opus-5", "Opus", identities)[0] == "anthropic/claude-opus-5"
    assert catalog_identity("anthropic", "claude-opus-4-8-20260101", "Opus", identities)[0] == (
        "anthropic/claude-opus-4-8-20260101"
    )


def test_ambiguous_leaf_and_similar_display_name_do_not_guess_identity():
    identities = model_identities({"a/model-1": {"name": "Same"}, "b/model-1": {"name": "Same"}})
    assert catalog_identity("gateway", "model-1", "Same", identities) == ("gateway/model-1", "Same")
    assert catalog_identity("gateway", "other-id", "Same", identities) == ("gateway/other-id", "Same")

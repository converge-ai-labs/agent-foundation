"""Credential ceilings and exact deployment knowledge are independent of model behavior."""

import json
import shutil

import pytest
from a13n_service.agent_configuration.definition import ASSETS, load_definition
from a13n_service.agent_configuration.knowledge import KnowledgeBundles
from a13n_service.agent_configuration.projections import PROTECTED, contains_protected_input, model_safe
from a13n_service.agents.domain import AgentConfig
from a13n_service.application_errors import ApplicationError


def test_projection_withholds_nested_credentials_urls_and_diff_values_without_mutation():
    value = {
        "plugins": [{"config": {"api_key": "private-value", "region": "us"}}],
        "endpoint": "https://user:private-value@example.com/api",
        "diff": [{"path": ["plugins", "config", "extra_headers"], "before": "private-value", "after": None}],
        "secret_requirements": [{"secret_key": "service-key"}],
    }
    safe = model_safe(value)
    assert "private-value" not in json.dumps(safe)
    assert safe["plugins"][0]["config"]["region"] == "us"
    assert safe["secret_requirements"] == value["secret_requirements"]
    assert value["plugins"][0]["config"]["api_key"] == "private-value"


@pytest.mark.parametrize(
    "value",
    [
        {"config": {"Authorization": "value"}},
        {"value": PROTECTED},
        {"value": "https://example.com/?access_token=value"},
    ],
)
def test_model_input_cannot_replace_protected_values(value):
    assert contains_protected_input(value)
    assert not contains_protected_input({"secret_key": "service-key", "instructions": "Be concise."})


def test_bundle_schema_matches_authoring_contract_and_tampering_fails(tmp_path):
    reference = load_definition().knowledge_bundle
    root = KnowledgeBundles().verify(reference)
    schema = json.loads((root / "configure-agent/agent-config.schema.json").read_text())
    assert schema == AgentConfig.model_json_schema(by_alias=True)
    shutil.copytree(ASSETS / "bundles", tmp_path / "bundles")
    retained = tmp_path / "bundles" / reference.bundle_id
    (retained / "configure-agent/SKILL.md").write_text("Changed knowledge")
    with pytest.raises(ApplicationError, match="exact accepted knowledge bundle"):
        KnowledgeBundles(tmp_path / "bundles").verify(reference)


def test_bundle_rejects_unlisted_files_and_symlinks(tmp_path):
    reference = load_definition().knowledge_bundle
    shutil.copytree(ASSETS / "bundles", tmp_path / "bundles")
    retained = tmp_path / "bundles" / reference.bundle_id
    extra = retained / "extra.md"
    extra.write_text("Unreviewed content")
    with pytest.raises(ApplicationError):
        KnowledgeBundles(tmp_path / "bundles").verify(reference)
    extra.unlink()
    extra.symlink_to(ASSETS / "assistant.yaml")
    with pytest.raises(ApplicationError):
        KnowledgeBundles(tmp_path / "bundles").verify(reference)

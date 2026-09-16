"""Selective reads reduce model context without broadening safe projections."""

import json
from copy import deepcopy

import pytest
from a13n_service.agent_configuration.projections import (
    PROTECTED,
    ReadFields,
    model_safe,
    read_path_parts,
    select_fields,
)
from pydantic import TypeAdapter, ValidationError
from pydantic_ai import ModelRetry


def test_nested_selection_omits_large_baselines_and_preserves_edit_metadata():
    full = {
        "draft_id": "draft",
        "version": 4,
        "content_digest": "digest",
        "status": "open",
        "config": {"instructions": "candidate" * 1000, "model": "model-id"},
        "base": {"config": {"instructions": "baseline" * 1000}},
        "validation": None,
    }
    original = deepcopy(full)
    metadata = ("draft_id", "version", "content_digest", "status")
    result = select_fields(full, ("config.model", "validation"), required=metadata)
    assert result == {**{key: full[key] for key in metadata}, "config": {"model": "model-id"}, "validation": None}
    assert len(json.dumps(result)) < len(json.dumps(full)) / 20
    assert select_fields(full, (), required=metadata) == {key: full[key] for key in metadata}
    assert select_fields(full, None) == full
    assert full == original


def test_overlapping_paths_and_literal_keys_preserve_subtrees_without_mutation():
    full = {"config": {"tools.with.dots": {"enabled": True}, "items": [1, 2]}}
    original = deepcopy(full)
    assert select_fields(full, ("config['tools.with.dots'].enabled",)) == {
        "config": {"tools.with.dots": {"enabled": True}}
    }
    assert select_fields(full, ("config.items", "config", "$.config.items")) == full
    assert select_fields(full, ()) == {}
    assert full == original


@pytest.mark.parametrize("path", ["missing", "nullable.key", "items['0']", "name.key"])
def test_invalid_paths_fail_instead_of_returning_full_content(path):
    with pytest.raises(ModelRetry, match="Unknown or unavailable"):
        select_fields({"nullable": None, "items": [1], "name": "text"}, (path,))


def test_selection_cannot_bypass_redaction_or_traverse_protected_objects():
    safe = model_safe({"config": {"headers": {"private": "secret"}, "region": "us"}})
    assert select_fields(safe, ("config.headers",)) == {"config": {"headers": PROTECTED}}
    with pytest.raises(ModelRetry):
        select_fields(safe, ("config.headers.private",))


@pytest.mark.parametrize("fields", [[[]], [""], ["x" * 129], [".".join(["x"] * 17)], ["x"] * 33, [0], ["x" * 8193]])
def test_field_schema_rejects_unbounded_or_malformed_paths(fields):
    with pytest.raises(ValidationError):
        TypeAdapter(ReadFields).validate_python(fields)


def test_field_schema_accepts_limits_and_empty_selection():
    adapter = TypeAdapter(ReadFields)
    assert adapter.validate_python([]) == ()
    paths = [".".join(["x" * 128] * 16)] * 32
    assert len(adapter.validate_python(paths)) == 32


@pytest.mark.parametrize(
    ("path", "parts"),
    [
        ("config.input_adapter.adapter_key", ("config", "input_adapter", "adapter_key")),
        ("$.config.instructions", ("config", "instructions")),
        ("config['tools.with.dots'].enabled", ("config", "tools.with.dots", "enabled")),
        ('config["tools.with.dots"].enabled', ("config", "tools.with.dots", "enabled")),
        ("['root.key']['nested-key']", ("root.key", "nested-key")),
        ("$['root.key']", ("root.key",)),
        ("config.tool-name", ("config", "tool-name")),
        ("config['0']", ("config", "0")),
        ("config['*']", ("config", "*")),
    ],
)
def test_read_path_syntax(path, parts):
    assert read_path_parts(path) == parts
    assert TypeAdapter(ReadFields).validate_python([path]) == (path,)


@pytest.mark.parametrize(
    "path",
    [
        "$",
        "$.",
        ".config",
        "config.",
        "config..name",
        "config.*",
        "config[*]",
        "config[0]",
        "config[0:2]",
        "config[?(@.enabled)]",
        "config['a','b']",
        "config['']",
        "config['unterminated]",
        'config.["name"]',
        "config['a']name",
        "config['a'].",
        "config name",
        "config['line\nkey']",
    ],
)
def test_unsupported_or_malformed_selectors_are_rejected(path):
    with pytest.raises(ValidationError):
        TypeAdapter(ReadFields).validate_python([path])
    with pytest.raises(ModelRetry):
        select_fields({"config": {}}, (path,))


def test_quoted_keys_escape_quotes_and_backslashes():
    for key in ["a'b", 'a"b', "a\\b", "a.b", "a]b", "中文"]:
        for quote in ["'", '"']:
            escaped = key.replace("\\", "\\\\").replace(quote, "\\" + quote)
            path = f"config[{quote}{escaped}{quote}]"
            assert select_fields({"config": {key: True}}, (path,)) == {"config": {key: True}}

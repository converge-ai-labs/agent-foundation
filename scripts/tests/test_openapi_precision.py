"""Regression: Service must not publish rounded Pydantic integer bounds."""

import json
from copy import deepcopy
from pathlib import Path

import pytest
from a13n_envd_client.eip.v1.models import SessionLifecyclePolicy
from a13n_service.app import build_app
from a13n_service.distribution import OSS
from a13n_service.runs.stream import frames_schema
from a13n_service.runs.thread_stream import frames_schema as thread_frames_schema
from fastapi.testclient import TestClient
from pydantic import ValidationError

MAX = 18446744073709551615


def test_openapi_integer_bounds_match_runtime_and_stream_without_other_changes(monkeypatch):
    app = build_app(distribution=OSS, apps=())
    app.openapi_schema = None
    with monkeypatch.context() as patch:
        patch.setattr("a13n_service.app.preserve_integer_schema_bounds", lambda *_: None)
        baseline = deepcopy(app.openapi())
    app.openapi_schema = None
    corrected = app.openapi()
    fields = corrected["components"]["schemas"]["SessionLifecyclePolicy"]["properties"]
    stream = frames_schema()["$defs"]["SessionLifecyclePolicy"]["properties"]
    for key in ("idle_timeout_ms", "max_lifetime_ms"):
        field = fields[key] if key == "idle_timeout_ms" else fields[key]["anyOf"][0]
        source = stream[key] if key == "idle_timeout_ms" else stream[key]["anyOf"][0]
        assert type(field["maximum"]) is int
        assert field["maximum"] == source["maximum"] == MAX
        assert field["minimum"] == source["minimum"] == 1
    for name in ("idle_timeout_ms", "max_lifetime_ms"):
        for value in (1, MAX):
            values = {"idle_timeout_ms": 1, name: value}
            SessionLifecyclePolicy(**values)
        for value in (0, MAX + 1, 1.0, True):
            with pytest.raises(ValidationError):
                SessionLifecyclePolicy(**({"idle_timeout_ms": 1, name: value}))
    assert SessionLifecyclePolicy(idle_timeout_ms=1, max_lifetime_ms=None).max_lifetime_ms is None

    differences = []

    def compare(old, new, path=()):
        if isinstance(old, dict) and isinstance(new, dict):
            assert old.keys() == new.keys()
            for k in old:
                compare(old[k], new[k], (*path, k))
        elif isinstance(old, list) and isinstance(new, list):
            assert len(old) == len(new)
            for i, (a, b) in enumerate(zip(old, new, strict=True)):
                compare(a, b, (*path, i))
        elif old != new:
            differences.append((path, old, new))

    compare(baseline, corrected)
    assert len(differences) == 67
    assert all(path[-1] == "maximum" and old == float(2**64) and new == MAX for path, old, new in differences)
    assert (
        fields["idle_timeout_ms"]["minimum"]
        == baseline["components"]["schemas"]["SessionLifecyclePolicy"]["properties"]["idle_timeout_ms"]["minimum"]
    )
    root = Path(__file__).resolve().parents[2]
    assert frames_schema() == json.loads((root / "proto/a13n-service/session-stream.schema.json").read_text())
    assert thread_frames_schema() == json.loads((root / "proto/a13n-service/thread-stream.schema.json").read_text())
    client = TestClient(app)
    response = client.get("/api/v1/openapi.json")
    assert response.status_code == 200
    assert (
        response.json()["components"]["schemas"]["SessionLifecyclePolicy"]["properties"]["idle_timeout_ms"]["maximum"]
        == MAX
    )


def test_integer_restoration_preserves_ordinary_and_fractional_constraints():
    from a13n_service.infra.openapi_precision import _restore_integer_bounds

    source = {
        "properties": {
            "large": {"type": "integer", "maximum": 9007199254740993},
            "small": {"type": "integer", "minimum": 1, "maximum": 100},
            "ratio": {"type": "number", "multipleOf": 0.125},
        }
    }
    target = deepcopy(source)
    target["properties"]["large"]["maximum"] = float(9007199254740993)
    target["properties"]["small"]["minimum"] = 1.0
    target["properties"]["small"]["maximum"] = 100.0
    _restore_integer_bounds(source, target)
    assert target["properties"]["large"]["maximum"] == 9007199254740993
    assert target["properties"]["small"]["minimum"] == 1
    assert target["properties"]["small"]["maximum"] == 100
    assert target["properties"]["ratio"]["multipleOf"] == 0.125
    with pytest.raises(ValueError, match="lost numeric"):
        _restore_integer_bounds({"maximum": MAX}, {})
    with pytest.raises(ValueError, match="array changed"):
        _restore_integer_bounds([1], [])


def test_failed_restoration_does_not_leave_unrepaired_cache(monkeypatch):
    from a13n_service.app import contract
    from fastapi import FastAPI

    app = FastAPI()

    def fail(*_):
        raise ValueError("no authority")

    monkeypatch.setattr("a13n_service.app.preserve_integer_schema_bounds", fail)
    with pytest.raises(ValueError, match="no authority"):
        contract(app, locally_authenticated=())
    assert app.openapi_schema is None

"""Modal mode admission, frozen recipe compatibility and native execution selection."""

import hashlib
import json

import pytest
from a13n_harness.providers.environment.modal.execution import ModalExecution
from a13n_harness.providers.environment.modal.provider import MODAL, construct
from a13n_harness.providers.environment.modal.shared import (
    ModalConnectionConfiguration,
    ModalCredential,
    ModalEnvironmentConfiguration,
)
from a13n_harness.providers.environment.native.environment import NativeRuntime
from pydantic import ValidationError


@pytest.mark.parametrize("execution", ["envd_http", "envd_ws"])
@pytest.mark.parametrize("environment", [{}, {"UNICODE": "你好"}])
def test_existing_envd_fingerprints_are_unchanged(execution, environment):
    old = {"image": "im-envd", "desktop": False}
    if execution != "envd_http":
        old["execution"] = execution
    if environment:
        old["environment"] = environment
    old.update(request_timeout_seconds=120.0, timeout_seconds=86400, cpu=1.0, memory=1024)
    expected = hashlib.sha256(json.dumps(old, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    config = ModalEnvironmentConfiguration(image="im-envd", execution=execution, environment=environment)
    assert config.fingerprint == expected
    assert ModalEnvironmentConfiguration.model_validate_json(config.model_dump_json()).fingerprint == expected


def test_native_is_explicit_files_and_foreground_shell_without_io():
    config = ModalEnvironmentConfiguration(image="python:3.13-slim", execution="native")
    assert config.shell == "/bin/sh"
    assert config.root == "/workspace"
    descriptor = MODAL.describe_environment(config)
    assert descriptor.operation_families == frozenset({"files", "shell"})
    assert (
        construct(
            configuration=config,
            environment_id="env-fixed",
            state=None,
            runtime=NativeRuntime(
                ModalConnectionConfiguration(), ModalCredential(token_id="id", token_secret="secret")
            ),
        ).__class__
        is ModalExecution
    )
    assert config.fingerprint != ModalEnvironmentConfiguration(image=config.image).fingerprint


def test_native_cannot_claim_computer_support():
    with pytest.raises(ValidationError, match="Computer use requires Envd"):
        ModalEnvironmentConfiguration(image="python:3.13-slim", execution="native", desktop=True)

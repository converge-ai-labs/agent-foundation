"""Encrypted browser handoff material shared by authorization protocols."""

from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass

from a13n_service.connectivity.domain import JsonObject
from a13n_service.connectivity.management import canonical_json
from a13n_service.secrets import SecretProtector

from .domain import CreateAuthorizationRequest
from .models import AuthorizationRecord


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def secret_bundle(record: AuthorizationRecord, protector: SecretProtector) -> JsonObject:
    if record.ciphertext is None:
        return {}
    value = json.loads(record.credential_snapshot().decrypt(protector))
    if not isinstance(value, dict):
        raise ValueError("Invalid authorization material")
    return value


def store_material(record: AuthorizationRecord, protector: SecretProtector, **values: str) -> None:
    bundle = secret_bundle(record, protector)
    bundle.update(values)
    record.replace_credential(canonical_json(bundle), protector)


@dataclass(frozen=True, slots=True)
class BrowserHandoff:
    return_url: str
    state: str
    completion_challenge: str

    @classmethod
    def from_request(cls, request: CreateAuthorizationRequest) -> BrowserHandoff:
        assert request.return_url is not None and request.state is not None and request.completion_challenge is not None
        return cls(request.return_url, request.state, request.completion_challenge)

    def initialize(self, record: AuthorizationRecord, protector: SecretProtector) -> None:
        token = secrets.token_urlsafe(32)
        record.return_url = self.return_url
        record.client_state = self.state
        record.completion_challenge = self.completion_challenge
        record.launch_token_digest = digest(token)
        store_material(record, protector, launch_token=token)

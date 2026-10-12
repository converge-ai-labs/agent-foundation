from datetime import UTC, datetime, timedelta

import pytest
from a13n_harness.providers.environment.remote_envd.pairing import (
    PAIRING_LIFETIME,
    PairingPending,
    PairingRequest,
    PendingPairing,
    credential_digest,
    credential_matches,
    pairing_id,
)
from pydantic import ValidationError


def test_pairing_credential_is_narrow_and_never_in_validation_errors() -> None:
    token = "ab" * 32
    digest = credential_digest(token)
    assert credential_matches(token, digest)
    assert not credential_matches("cd" * 32, digest)
    assert not credential_matches("user-api-key", digest)
    assert token not in digest
    with pytest.raises(ValueError, match="Invalid envd pairing credential") as error:
        credential_digest("secret-user-api-key")
    assert "secret-user-api-key" not in str(error.value)


def test_pairing_has_stable_identity_and_expiring_safe_challenge() -> None:
    token = "ab" * 32
    digest = credential_digest(token)
    request = PairingRequest(device_id="device-laptop", name="Laptop")
    now = datetime(2026, 9, 19, tzinfo=UTC)
    pending = PendingPairing.create(request, digest, now=now)
    repeated = PendingPairing.create(request, digest, now=now + timedelta(seconds=1))
    assert pending.challenge.pairing_id == repeated.challenge.pairing_id == pairing_id(digest)
    assert pending.challenge.verification_code == repeated.challenge.verification_code
    assert not pending.expired(now)
    assert pending.expired(now + PAIRING_LIFETIME)
    response = PairingPending(challenge=pending.challenge)
    serialized = response.model_dump_json()
    assert token not in serialized and digest not in serialized
    assert digest not in repr(pending)
    assert PendingPairing.model_validate_json(pending.model_dump_json()) == pending


@pytest.mark.parametrize("name", ["", " name", "name\n", "name\0"])
def test_pairing_rejects_invalid_display_metadata(name: str) -> None:
    with pytest.raises(ValidationError):
        PairingRequest(device_id="device-laptop", name=name)

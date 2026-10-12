"""Bounded pending approvals; approved trust lives in ordinary Device resources."""

from __future__ import annotations

from collections.abc import Mapping

from a13n_harness.providers.environment.remote_envd.pairing import (
    MAX_PENDING_PAIRINGS,
    PairingApproved,
    PairingChallenge,
    PairingPending,
    PairingRequest,
    PairingResponse,
    PendingPairing,
    credential_digest,
    pairing_id,
)

from a13n_harness_ui.configuration.models import DeviceResource, PairedDeviceAuthentication, WebSocketDeviceTransport
from a13n_harness_ui.errors import HarnessUiError


class DevicePairings:
    """Access is serialized by the App configuration lock, including publication."""

    def __init__(self) -> None:
        self._pending: dict[str, PendingPairing] = {}
        self._rejected: set[str] = set()

    def _prune(self) -> None:
        self._pending = {key: value for key, value in self._pending.items() if not value.expired()}
        self._rejected.intersection_update(self._pending)

    def list_pending(self) -> tuple[PairingChallenge, ...]:
        self._prune()
        return tuple(value.challenge for key, value in self._pending.items() if key not in self._rejected)

    def poll(
        self, request: PairingRequest, credential: str, resources: Mapping[str, DeviceResource]
    ) -> PairingResponse:
        try:
            digest = credential_digest(credential)
        except ValueError:
            raise HarnessUiError("Invalid envd pairing credential.", code="device_authentication_failed") from None
        for resource in resources.values():
            authentication = resource.authentication
            if not isinstance(authentication, PairedDeviceAuthentication) or authentication.credential_digest != digest:
                continue
            if resource.device_id != request.device_id:
                raise HarnessUiError("Credential belongs to another Device.", code="device_pairing_conflict")
            if authentication.revoked:
                raise HarnessUiError("Device registration is revoked.", code="device_revoked")
            return PairingApproved(
                resource_id=resource.id,
                websocket_url=f"/api/devices/{resource.id}/connect",
            )
        self._prune()
        key = pairing_id(digest)
        if key in self._rejected:
            raise HarnessUiError("Device pairing was rejected.", code="device_pairing_rejected")
        pending = self._pending.get(key)
        if pending is None:
            if len(self._pending) >= MAX_PENDING_PAIRINGS:
                raise HarnessUiError("Too many pending Device pairings. Try later.", code="device_pairing_capacity")
            pending = PendingPairing.create(request, digest)
            self._pending[key] = pending
        elif pending.challenge.device_id != request.device_id or pending.challenge.name != request.name:
            raise HarnessUiError("Pairing details cannot change during approval.", code="device_pairing_conflict")
        return PairingPending(challenge=pending.challenge, approval_url="/settings/environments")

    def approve(self, key: str, resources: Mapping[str, DeviceResource]) -> DeviceResource:
        self._prune()
        # Retried approvals resolve from the durable resource, including after restart.
        for resource in resources.values():
            authentication = resource.authentication
            if (
                isinstance(authentication, PairedDeviceAuthentication)
                and pairing_id(authentication.credential_digest) == key
            ):
                if authentication.revoked:
                    raise HarnessUiError("Device registration is revoked.", code="device_revoked")
                return resource
        pending = self._pending.get(key)
        if pending is None or key in self._rejected:
            raise HarnessUiError("Pending Device pairing was not found.", code="device_pairing_not_found")
        for resource in resources.values():
            if resource.device_id == pending.challenge.device_id:
                raise HarnessUiError(
                    "This Device identity is already registered. Use its saved Host credential or a separate envd instance.",
                    code="device_pairing_conflict",
                )
        return DeviceResource(
            schema_version="1",
            kind="device",
            id=f"device-{pending.credential_digest[:24]}",
            name=pending.challenge.name,
            device_id=pending.challenge.device_id,
            transport=WebSocketDeviceTransport(kind="websocket"),
            authentication=PairedDeviceAuthentication(credential_digest=pending.credential_digest),
        )

    def approved(self, key: str) -> None:
        self._pending.pop(key, None)

    def reject(self, key: str) -> None:
        self._prune()
        if key not in self._pending:
            raise HarnessUiError("Pending Device pairing was not found.", code="device_pairing_not_found")
        self._rejected.add(key)

"""Service lifecycle observation, cleanup, and barriers for real providers."""

from contextlib import contextmanager
from uuid import uuid4

from ..infrastructure.management_support import ManagementJourney
from ..infrastructure.round_two_lab import private_json
from .e2b_support import eventually


class ServiceEnvironments:
    def __init__(self, lab, pool):
        self.lab, self.pool = lab, pool
        self.journey = ManagementJourney(lab)
        self.environments = []

    async def record(self, environment):
        return await self.journey.live.request("GET", f"/__live__/environments/{environment['id']}/lifecycle")

    async def delete(self, identity):
        key = uuid4().hex
        command = None

        async def deleted():
            nonlocal command
            row = await self.record({"id": identity})
            if row["status"] == "deleted" and row["operation_id"] is None:
                return True
            if command is not None:
                result = await self.journey.live.request("GET", f"/api/v1/environment-commands/{command}")
                assert result["status"] != "failed", f"Cleanup delete command failed: {command}"
                return False
            if row["operation_id"]:
                return False
            response = await self.journey.live.http.post(
                f"/api/v1/environments/{identity}/delete", headers={"Idempotency-Key": key}, json={}
            )
            # Retention may acquire ownership between the read and the command.
            if response.status_code == 409 and response.json().get("error", {}).get("code") == "environment_busy":
                return False
            assert response.status_code == 202, f"Cleanup delete: HTTP {response.status_code}"
            command = response.json()["id"]
            return False

        await eventually(deleted, bool, f"Service cleanup deleted {identity}")

    async def status(self, environment, status):
        return await eventually(
            lambda: self.record(environment), lambda row: row["status"] == status, f"Service {status}"
        )


@contextmanager
def lifecycle_barrier(lab, environment, action, *, provider="e2b"):
    root = lab.root / (provider + "-fault")
    root.mkdir(mode=0o700, exist_ok=True)
    for name in ("claimed", "release"):
        (root / name).unlink(missing_ok=True)
    private_json(root / "fault.json", {"environment_id": environment["id"], "action": action})
    try:
        yield root
    finally:
        (root / "release").touch()
        (root / "fault.json").unlink(missing_ok=True)


def shell(script):
    return {"tool": "shell_exec", "arguments": {"command": script, "yield_time_seconds": 5}}

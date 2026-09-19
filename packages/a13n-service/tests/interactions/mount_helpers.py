"""Persisted accepted-mount fixtures shared by Worker and Control tests."""

from a13n_service.environments.mount_models import RunEnvironmentMountRecord

from .conftest import NOW, ORGANIZATION_ID, USER_ID, WORKSPACE_ID


def accepted_mount(run_id, environment_id, **changes):
    return RunEnvironmentMountRecord(
        **{
            "run_id": run_id,
            "name": "computer",
            "organization_id": ORGANIZATION_ID,
            "workspace_id": WORKSPACE_ID,
            "environment_id": environment_id,
            "created_at": NOW,
            "principal_type": "user",
            "principal_id": USER_ID,
            "application_status": "pending",
            **changes,
        }
    )

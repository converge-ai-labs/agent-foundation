"""Observed Worker identities and barriers around real Environment coordination.

All lifecycle barriers run outside database transactions. Evidence contains no
credentials, lease tokens or native bootstrap material, and never mutates rows.
"""

import json
import os
import socket
from functools import wraps
from pathlib import Path

from .run_faults import Faults


def install(config, role):
    if role != "worker":
        return
    from a13n_service.environments.lifecycle import EnvironmentLifecycle
    from a13n_service.interactions.scheduling import AttemptScheduler
    from a13n_service.interactions.worker import WorkerExecutionLoop

    root = Path(config["workspace_root"]).parent / "environment-workers"
    root.mkdir(mode=0o700, exist_ok=True)
    faults = Faults(root / "faults", role)
    hostname = os.environ.get("LIVE_TEST_HOSTNAME")
    if hostname:
        # A distinct Host identity exercises the production affinity guards.
        socket.gethostname = lambda: hostname

    original_init = WorkerExecutionLoop.__init__

    @wraps(original_init)
    def initialize(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        (root / f"worker-{os.getpid()}.json").write_text(
            json.dumps({"pid": os.getpid(), "worker_id": self._worker_id, "host_id": socket.gethostname()})
        )

    WorkerExecutionLoop.__init__ = initialize
    original_claim = AttemptScheduler.claim

    @wraps(original_claim)
    async def claim(self, *args, **kwargs):
        # Route only new claims. Existing Runs and maintenance continue normally,
        # and placement never suspends a process holding a database transaction.
        if (root / f"claims-paused-{os.getpid()}").exists():
            return None
        return await original_claim(self, *args, **kwargs)

    AttemptScheduler.claim = claim

    def facts(operation):
        return {
            "environment_id": operation.environment_id,
            "run_id": operation.run_id or "",
            "action": operation.action,
            "operation_id": operation.operation_id,
            "fence": operation.fence,
            "pid": os.getpid(),
        }

    def record(point, values):
        with (root / f"events-{os.getpid()}.jsonl").open("a") as output:
            output.write(json.dumps({"point": point, **values}) + "\n")

    original_acquire = EnvironmentLifecycle.acquire

    @wraps(original_acquire)
    async def acquire(self, *args, **kwargs):
        operation = await original_acquire(self, *args, **kwargs)
        record("environment.acquired", facts(operation))
        await faults.reach("environment.acquired", **facts(operation))
        return operation

    EnvironmentLifecycle.acquire = acquire
    original_execute = EnvironmentLifecycle.execute

    @wraps(original_execute)
    async def execute(self, operation, **kwargs):
        await faults.reach("environment.before_effect", **facts(operation))
        return await original_execute(self, operation, **kwargs)

    EnvironmentLifecycle.execute = execute
    original_publish = EnvironmentLifecycle.publish

    @wraps(original_publish)
    async def publish(self, operation, environment, **kwargs):
        values = {**facts(operation), "success": kwargs.get("error") is None}
        await faults.reach("environment.before_publication", **values)
        try:
            result = await original_publish(self, operation, environment, **kwargs)
        except Exception:
            record("environment.publication_rejected", values)
            raise
        record("environment.published", {**values, "generation": result})
        await faults.reach("environment.after_publication", **values)
        return result

    EnvironmentLifecycle.publish = publish

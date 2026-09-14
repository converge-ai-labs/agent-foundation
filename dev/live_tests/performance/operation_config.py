"""One concurrency matrix and optional per-operation budgets."""

import tomllib
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCENARIOS = {
    "pg.select": "PG / indexed SELECT",
    "pg.insert": "PG / INSERT",
    "pg.update": "PG / UPDATE",
    "pg.commit": "PG / COMMIT one write",
    "s3.put": "S3 / PUT",
    "s3.get": "S3 / GET full body",
    "s3.head": "S3 / HEAD",
    "s3.delete": "S3 / DELETE",
    "s3.conditional_put": "S3 / conditional PUT on independent keys",
    "s3.stale_conditional_put": "S3 / reject stale conditional PUT",
    "s3.contended_conditional_put": "S3 / competing conditional PUT on one key",
    "thread.create": "Thread / create with new Session",
    "attempt.claim": "Attempt / claim independent Runs",
    "attempt.claim_same_run": "Attempt / compete to claim one Run",
    "attempt.heartbeat": "Attempt / renew lease",
    "run.complete": "Run / commit running to completed",
    "steer.append": "Steer / insert pending on independent Threads",
    "steer.append_same_thread": "Steer / insert pending on one Thread",
    "steer.consume": "Steer / confirm one consumed receipt",
    "queue.enqueue": "Queue / enqueue on independent Threads",
    "queue.enqueue_same_thread": "Queue / enqueue on one Thread",
    "queue.enqueue_full": "Queue / reject enqueue at capacity 256",
}


class OperationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scenarios: list[str] = list(SCENARIOS)
    concurrency: list[Annotated[int, Field(strict=True, ge=1, le=256)]] = [1, 8, 32, 64, 256]
    samples: int = Field(default=256, strict=True, ge=1, le=10000)
    warmup_waves: int = Field(default=1, strict=True, ge=0, le=10)
    payload_bytes: list[Annotated[int, Field(strict=True, ge=1, le=4194304)]] = [1024, 1048576]
    pg_pool_size: int = Field(default=256, strict=True, ge=1, le=256)
    s3_pool_size: int = Field(default=256, strict=True, ge=1, le=256)
    http_pool_size: int = Field(default=256, strict=True, ge=1, le=1024)
    thread_connection_states: list[Literal["cold", "warm"]] = ["cold", "warm"]
    budgets_ms: dict[str, dict[str, Annotated[float, Field(gt=0, allow_inf_nan=False)]]] = {}

    @model_validator(mode="after")
    def coherent(self):
        if (
            not self.scenarios
            or len(set(self.scenarios)) != len(self.scenarios)
            or set(self.scenarios) - SCENARIOS.keys()
        ):
            raise ValueError("Select nonempty unique scenario IDs from the catalog")
        if (
            not self.concurrency
            or not self.payload_bytes
            or len(set(self.concurrency)) != len(self.concurrency)
            or len(set(self.payload_bytes)) != len(self.payload_bytes)
        ):
            raise ValueError("Use nonempty unique concurrency and payload matrices")
        if self.samples < max(self.concurrency) or self.pg_pool_size < max(self.concurrency):
            raise ValueError("samples and PG pool must cover the largest concurrency")
        if self.http_pool_size < max(self.concurrency):
            raise ValueError("HTTP pool must cover the largest preparation wave")
        if not self.thread_connection_states or len(set(self.thread_connection_states)) != len(
            self.thread_connection_states
        ):
            raise ValueError("Use nonempty unique Thread connection states")
        if set(self.budgets_ms) - SCENARIOS.keys():
            raise ValueError("Unknown operation budget; use an explicit scenario ID from the catalog")
        if any(set(budget) - {"p95", "p99"} for budget in self.budgets_ms.values()):
            raise ValueError("Budgets support only p95 and p99")
        return self


def read_config(path=None):
    return OperationConfig.model_validate(tomllib.loads(Path(path).read_text()) if path else {})

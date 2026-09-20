"""GitHub App event conditions, sender policy, and safe occurrence facts."""

from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field

from ...domain import JsonObject
from ...ingress.provider import InboundEvent
from ...subscriptions import EventTrigger, EventType, MatchedEvent, require_event_type
from .polling_config import GitHubReceptionPolicy


class PullRequestFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pull_request_number: int = Field(gt=0, description="Exact pull request number in this repository.")


class WorkflowFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")
    branch: str | None = Field(default=None, min_length=1, max_length=256)
    workflow_id: int | None = Field(default=None, gt=0)


class GitHubSubscriptions:
    config_versions = frozenset({"github_app_http_v1"})
    target_kind = "repository"
    event_types = (
        EventType(
            "github.pull_request.merged",
            "An exact pull request merges; ordinary closure does not match.",
            PullRequestFilter,
        ),
        EventType(
            "github.workflow.failed",
            "A GitHub Actions run completes with failure; success, cancellation and timeout do not match.",
            WorkflowFilter,
        ),
    )
    setup = (
        "Configure GitHub App Pull request / Workflow run webhooks and Actions read permission. No polling fallback."
    )

    def match(
        self, trigger: EventTrigger, event: InboundEvent, *, configuration: JsonObject, policy: JsonObject
    ) -> MatchedEvent | None:
        filters = require_event_type(self, trigger).validate(trigger)
        reception = GitHubReceptionPolicy.model_validate(policy)
        sender = (event.actor or {}).get("login")
        if "*" not in reception.allowed_senders and (
            not isinstance(sender, str) or sender.casefold() not in {s.casefold() for s in reception.allowed_senders}
        ):
            return None
        if isinstance(filters, PullRequestFilter):
            if not (
                event.context.get("event_action") == "pull_request.closed"
                and event.context.get("number") == filters.pull_request_number
                and event.data.get("merged") is True
            ):
                return None
            key = f"pr:{filters.pull_request_number}:merged"
            suffix = f"/pull/{filters.pull_request_number}"
        else:
            assert isinstance(filters, WorkflowFilter)
            if not (
                event.context.get("event_action") == "workflow_run.completed"
                and event.data.get("conclusion") == "failure"
                and (filters.branch is None or event.context.get("head_branch") == filters.branch)
                and (filters.workflow_id is None or event.context.get("workflow_id") == filters.workflow_id)
            ):
                return None
            run_id, attempt = event.data.get("workflow_run_id"), event.data.get("run_attempt")
            if type(run_id) is not int or run_id <= 0 or type(attempt) is not int or attempt <= 0:
                return None
            key, suffix = f"workflow:{run_id}:{attempt}", f"/actions/runs/{run_id}"
        owner, name = event.context.get("repository_owner"), event.context.get("repository_name")
        repository_id = event.context.get("repository_id")
        if (
            not isinstance(owner, str)
            or not isinstance(name, str)
            or event.occurred_at is None
            or repository_id is None
        ):
            return None
        url = str(configuration["web_origin"]).rstrip("/") + f"/{quote(owner, safe='')}/{quote(name, safe='')}" + suffix
        return MatchedEvent(
            key=key,
            external_target_id=str(repository_id),
            occurred_at=event.occurred_at,
            timestamp_precision_seconds=True,
            facts={
                "event_type": trigger.event_type,
                "url": url,
                "repository_id": str(repository_id),
                "number": event.context.get("number"),
                "workflow_run_id": event.data.get("workflow_run_id"),
                "run_attempt": event.data.get("run_attempt"),
                "head_branch": event.context.get("head_branch"),
                "occurred_at": event.occurred_at.isoformat(),
            },
        )

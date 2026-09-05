"""GitHub App HTTP v1 webhook authentication and safe normalization."""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import datetime
from typing import cast

from pydantic import JsonValue, TypeAdapter, ValidationError

from a13n_service.connectivity.domain import JsonObject
from a13n_service.connectivity.ingress.provider import (
    ExternalRef,
    InboundEvent,
    ProviderCompleteDecision,
    ProviderEventDecision,
    ProviderHttpResponse,
    ProviderRequest,
    ProviderRequestDecision,
    ProviderRequestError,
)

CONTEXT_VERSION = "github_app_event_v1"

_EVENT_ACTIONS = frozenset(
    {
        "issues.opened",
        "issues.reopened",
        "issues.closed",
        "issues.labeled",
        "issues.unlabeled",
        "issues.assigned",
        "issues.unassigned",
        "issue_comment.created",
        "pull_request.opened",
        "pull_request.reopened",
        "pull_request.closed",
        "pull_request.ready_for_review",
        "pull_request.converted_to_draft",
        "pull_request.synchronize",
        "pull_request.labeled",
        "pull_request.unlabeled",
        "pull_request_review.submitted",
        "pull_request_review.dismissed",
        "pull_request_review_comment.created",
    }
)
_JSON_OBJECT = TypeAdapter(JsonObject)


@dataclass(frozen=True, slots=True)
class GitHubIdentity:
    app_id: int
    installation_id: int
    installation_account_id: int
    bot_account_id: int


def authenticate_and_normalize(
    request: ProviderRequest,
    *,
    identity: GitHubIdentity,
    webhook_secret: str,
    received_at: datetime,
) -> ProviderRequestDecision:
    delivery_id = _required_header(request, "x-github-delivery", max_length=2048)
    event_name = _required_header(request, "x-github-event", max_length=128)
    signature = _required_header(request, "x-hub-signature-256", max_length=128)
    expected = "sha256=" + hmac.new(webhook_secret.encode(), request.body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise _request_error(401, "invalid_signature")
    payload = _parse_object(request.body)
    _verify_installation(payload, identity, event_name=event_name)
    if event_name == "ping":
        return ProviderCompleteDecision(response=ProviderHttpResponse(status_code=200))
    action = payload.get("action")
    if not isinstance(action, str) or f"{event_name}.{action}" not in _EVENT_ACTIONS:
        return ProviderCompleteDecision(response=ProviderHttpResponse(status_code=200))
    sender = _object(payload.get("sender"))
    if sender is None:
        raise _request_error(400, "invalid_payload")
    if sender.get("id") == identity.bot_account_id:
        return ProviderCompleteDecision(response=ProviderHttpResponse(status_code=200))
    try:
        normalized = _normalize_event(
            delivery_id,
            event_name,
            action,
            payload,
            identity,
            received_at,
        )
    except (ValidationError, ValueError, OverflowError) as error:
        raise _request_error(400, "invalid_payload") from error
    return ProviderEventDecision(event=normalized)


def event_actions() -> frozenset[str]:
    return _EVENT_ACTIONS


def _verify_installation(payload: JsonObject, identity: GitHubIdentity, *, event_name: str) -> None:
    installation = _object(payload.get("installation"))
    repository = _object(payload.get("repository"))
    hook = _object(payload.get("hook"))
    if event_name == "ping":
        if hook is None or hook.get("app_id") != identity.app_id:
            raise _request_error(404, "ingress_not_found")
        target_id = hook.get("installation_target_id")
        if target_id is not None and target_id != identity.installation_account_id:
            raise _request_error(404, "ingress_not_found")
        return
    if installation is None or installation.get("id") != identity.installation_id or repository is None:
        raise _request_error(404, "ingress_not_found")
    owner = _object(repository.get("owner"))
    if owner is None or owner.get("id") != identity.installation_account_id:
        raise _request_error(404, "ingress_not_found")
    if hook is not None and hook.get("app_id") != identity.app_id:
        raise _request_error(404, "ingress_not_found")


def _normalize_event(
    delivery_id: str,
    event_name: str,
    action: str,
    payload: JsonObject,
    identity: GitHubIdentity,
    received_at: datetime,
) -> InboundEvent:
    repository = _required_object(payload, "repository")
    repository_id = _required_int(repository, "id")
    owner = _required_object(repository, "owner")
    owner_login = _required_string(owner, "login", max_length=256)
    repository_name = _required_string(repository, "name", max_length=256)
    sender = _required_object(payload, "sender")
    actor = {
        "id": _required_int(sender, "id"),
        "login": _required_string(sender, "login", max_length=256),
        "type": _optional_string(sender.get("type"), max_length=128),
    }
    target, target_kind = _target(payload, event_name)
    number = _required_int(target, "number")
    labels = _labels(target.get("labels"))
    action_label = _label_name(payload.get("label"))
    base_branch = _base_branch(target) if target_kind == "pull_request" else None
    title = _optional_string(target.get("title"), max_length=40_000)
    content_source = _event_content_source(payload, event_name, target)
    body = _optional_string(content_source.get("body"), max_length=262_144)
    state = _optional_string(target.get("state"), max_length=128)
    html_url = _optional_string(target.get("html_url"), max_length=2048)
    changed = _changed_fields(payload.get("changes"))
    message_ref = _message_ref(payload, event_name)
    occurred_at = _occurred_at(payload, event_name, target)
    context: JsonObject = {
        "event_name": event_name,
        "action": action,
        "event_action": f"{event_name}.{action}",
        "repository_id": repository_id,
        "repository_owner": owner_login,
        "repository_name": repository_name,
        "number": number,
        "target_kind": target_kind,
        "labels": labels,
        "action_label": action_label,
        "base_branch": base_branch,
    }
    data: JsonObject = {
        "title": title,
        "body": body,
        "state": state,
        "html_url": html_url,
        "changed_fields": changed,
    }
    refs = {
        "repository": ExternalRef(
            kind="github.repository",
            id=f"{identity.installation_id}:{repository_id}",
        ),
        "target": ExternalRef(
            kind=f"github.{target_kind}",
            id=f"{repository_id}:{target_kind}:{number}",
        ),
    }
    if message_ref is not None:
        refs["message"] = message_ref
    return InboundEvent(
        identity_kind="github.delivery",
        external_event_id=delivery_id,
        normalization_version=CONTEXT_VERSION,
        type=f"github.{event_name}",
        occurred_at=occurred_at,
        received_at=received_at,
        text=body or title,
        actor=actor,
        context=context,
        refs=refs,
        data=data,
        ordering_key=f"{occurred_at.isoformat() if occurred_at is not None else received_at.isoformat()}:{delivery_id}",
    )


def _target(payload: JsonObject, event_name: str) -> tuple[JsonObject, str]:
    if event_name.startswith("pull_request"):
        return _required_object(payload, "pull_request"), "pull_request"
    issue = _required_object(payload, "issue")
    return issue, "pull_request" if isinstance(issue.get("pull_request"), dict) else "issue"


def _message_ref(payload: JsonObject, event_name: str) -> ExternalRef | None:
    key = {
        "issue_comment": "comment",
        "pull_request_review": "review",
        "pull_request_review_comment": "comment",
    }.get(event_name)
    if key is None:
        return None
    value = _required_object(payload, key)
    stable_id = value.get("node_id")
    if not isinstance(stable_id, str) or not stable_id:
        stable_id = str(_required_int(value, "id"))
    return ExternalRef(kind=f"github.{key}", id=stable_id)


def _event_content_source(payload: JsonObject, event_name: str, target: JsonObject) -> JsonObject:
    key = {
        "issue_comment": "comment",
        "pull_request_review": "review",
        "pull_request_review_comment": "comment",
    }.get(event_name)
    return _required_object(payload, key) if key is not None else target


def _occurred_at(payload: JsonObject, event_name: str, target: JsonObject) -> datetime | None:
    key = {
        "issue_comment": "comment",
        "pull_request_review": "review",
        "pull_request_review_comment": "comment",
    }.get(event_name)
    source = _required_object(payload, key) if key is not None else target
    for field in ("submitted_at", "updated_at", "created_at", "closed_at"):
        value = source.get(field)
        if isinstance(value, str):
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError("provider timestamp lacks timezone")
            return parsed
    return None


def _labels(value: JsonValue | None) -> list[JsonValue]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 256:
        raise ValueError("invalid labels")
    labels: list[JsonValue] = []
    for item in value:
        name = _label_name(item)
        if name is not None:
            labels.append(name)
    return labels


def _label_name(value: JsonValue | None) -> str | None:
    item = _object(value)
    return _optional_string(item.get("name"), max_length=256) if item is not None else None


def _base_branch(target: JsonObject) -> str | None:
    base = _object(target.get("base"))
    return _optional_string(base.get("ref"), max_length=1024) if base is not None else None


def _changed_fields(value: JsonValue | None) -> list[JsonValue]:
    changes = _object(value)
    if changes is None:
        return []
    names = sorted(changes)
    if len(names) > 64 or any(not name or len(name) > 128 for name in names):
        raise ValueError("invalid changed fields")
    return cast(list[JsonValue], names)


def _required_header(request: ProviderRequest, name: str, *, max_length: int) -> str:
    value = request.headers.get(name)
    if value is None or not 1 <= len(value) <= max_length:
        raise _request_error(401, "invalid_signature")
    return value


def _required_object(value: JsonObject, key: str) -> JsonObject:
    selected = _object(value.get(key))
    if selected is None:
        raise ValueError(f"missing {key}")
    return selected


def _object(value: JsonValue | None) -> JsonObject | None:
    return value if isinstance(value, dict) else None


def _required_string(value: JsonObject, key: str, *, max_length: int) -> str:
    selected = value.get(key)
    if not isinstance(selected, str) or not 1 <= len(selected) <= max_length:
        raise ValueError(f"invalid {key}")
    return selected


def _optional_string(value: JsonValue | None, *, max_length: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > max_length:
        raise ValueError("invalid provider string")
    return value


def _required_int(value: JsonObject, key: str) -> int:
    selected = value.get(key)
    if type(selected) is not int or selected <= 0:
        raise ValueError(f"invalid {key}")
    return selected


def _parse_object(body: bytes) -> JsonObject:
    try:
        return _JSON_OBJECT.validate_python(json.loads(body))
    except (json.JSONDecodeError, UnicodeDecodeError, ValidationError, RecursionError) as error:
        raise _request_error(400, "invalid_payload") from error


def _request_error(status_code: int, reason_code: str) -> ProviderRequestError:
    return ProviderRequestError(ProviderHttpResponse(status_code=status_code), reason_code=reason_code)

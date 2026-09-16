"""Bounded, actionable model feedback without raw exception or configuration values."""

import json
import re

from pydantic import ValidationError

from a13n_service.application_errors import ApplicationError
from a13n_service.iam import AuthorizationError


def safe_path(parts: tuple[str | int, ...]) -> list[str | int]:
    return [
        part if isinstance(part, int) or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]{0,127}", part) else "[field]"
        for part in parts[:16]
    ]


def validation_issues(error: ValidationError) -> list[dict[str, object]]:
    # Never expose input, ctx, custom validator messages, or exception repr.
    return [
        {"path": safe_path(item["loc"]), "reason": item["type"]}
        for item in error.errors(include_input=False, include_context=False, include_url=False)[:8]
    ]


_HINTS = {
    "configuration_edit_invalid": "Correct the indicated operation or config field using the AgentConfig schema. Paths in issues are relative to config.",
    "configuration_metadata_invalid": "Omit creation_metadata in update mode; it is only accepted in create mode.",
    "configuration_version_conflict": "Read the draft again and use its current version and content_digest.",
    "configuration_draft_conflict": "Read the draft again and use its current version and content_digest.",
    "precondition_failed": "Read the draft again before editing; its representation changed.",
    "configuration_draft_terminal": "This draft is closed. Ask the user to start a new configuration conversation.",
    "configuration_uninitialized": "Initialize the draft with a complete AgentConfig using a root set operation.",
    "agent_revision_create_failed": "Correct the indicated configuration or select an available authorized resource.",
}


def update_failure_feedback(error: ApplicationError | AuthorizationError) -> str:
    """Expose reviewed error categories and details, never arbitrary domain messages."""
    if not isinstance(error, ApplicationError) or error.code not in _HINTS:
        return json.dumps(
            {
                "code": "configuration_update_unavailable",
                "hint": "The draft or a required resource is unavailable or unauthorized. Check authorized resources or ask the user for help.",
            }
        )
    result: dict[str, object] = {"code": error.code, "hint": _HINTS[error.code]}
    if error.code == "configuration_edit_invalid":
        index = error.details.get("operation_index")
        if isinstance(index, int) and 0 <= index < 32:
            result["operation_index"] = index
        issues = error.details.get("issues")
        if isinstance(issues, list):
            result["issues"] = [
                {"path": safe_path(tuple(item["path"])), "reason": item["reason"]}
                for item in issues[:8]
                if isinstance(item, dict)
                and isinstance(item.get("path"), list)
                and all(isinstance(part, (str, int)) for part in item["path"])
                and isinstance(item.get("reason"), str)
                and re.fullmatch(r"[a-z_]{1,80}", item["reason"])
            ]
    if error.code == "agent_revision_create_failed":
        reason, path = error.details.get("reason"), error.details.get("path")
        if isinstance(reason, str) and re.fullmatch(r"[a-z_]{1,80}", reason):
            result["reason"] = reason
        if isinstance(path, str):
            result["path"] = safe_path(tuple(path.split(".")))
        if reason == "web_provider_required":
            result["hint"] = (
                "Set toolsets.web.tools.search.config.provider_id (or scrape.config.provider_id) to an authorized Web Provider ID in the same update that enables the tool."
            )
    return json.dumps(result)

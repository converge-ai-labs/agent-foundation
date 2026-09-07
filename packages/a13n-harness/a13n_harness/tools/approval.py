"""Bind deferred approvals to the mutable resource facts used at authorization."""

from collections.abc import Mapping

from pydantic import JsonValue
from pydantic_ai.exceptions import ToolFailed

from .policy import ToolInvocationContext

RESOURCE_APPROVAL_KEY = "a13n.resource-approval"


def approval_facts(invocation: ToolInvocationContext) -> dict[str, JsonValue] | None:
    revisions: list[JsonValue] = [
        {"namespace": resource.namespace, "kind": resource.kind, "revision": resource.approval_revision}
        for resource in invocation.resources
        if resource.approval_revision is not None
    ]
    if not revisions:
        return None
    return {"tool_id": invocation.tool_id, "arguments": invocation.arguments_digest, "resources": revisions}


def verify_approval_facts(invocation: ToolInvocationContext, metadata: object) -> None:
    expected = metadata.get(RESOURCE_APPROVAL_KEY) if isinstance(metadata, Mapping) else None
    current = approval_facts(invocation)
    if expected != current:
        raise ToolFailed("Approved resources changed. Inspect the current environment and request approval again.")

"""Safe model-visible outcomes for authoritative provider refusals."""

from .contracts import ConnectorProviderError, ConnectorToolFailure, ConnectorToolOutcome


def rejected_tool_outcome(error: ConnectorProviderError, *, request_id: str) -> ConnectorToolOutcome | None:
    if error.outcome_unknown or error.code not in {
        "scope_missing",
        "provider_rejected",
        "tool_rejected",
        "rate_limited",
    }:
        return None
    if error.code == "scope_missing":
        failure = ConnectorToolFailure(
            code="scope_missing",
            message="The connected account lacks a required OAuth scope. Ask an administrator to update its authorization.",
        )
    elif error.http_status == 401:
        failure = ConnectorToolFailure(code="authentication_required", message="The provider rejected authentication.")
    elif error.http_status == 403:
        failure = ConnectorToolFailure(
            code="permission_denied", message="The provider denied access. Check account permissions and OAuth scopes."
        )
    elif error.http_status == 404:
        failure = ConnectorToolFailure(
            code="not_found", message="The requested resource was not found or is not accessible to this account."
        )
    elif error.code == "rate_limited":
        failure = ConnectorToolFailure(
            code="rate_limited", message="The provider rate limit was reached. Do not immediately repeat this call."
        )
    else:
        failure = ConnectorToolFailure(
            code="tool_rejected",
            message="The provider rejected this tool call. Check the arguments and account permissions.",
        )
    return ConnectorToolOutcome(kind="failed", error=failure, request_id=request_id)

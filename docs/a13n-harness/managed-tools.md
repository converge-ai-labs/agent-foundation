# Managed tools and invocation policy

Ordinary Pydantic AI tools remain ordinary Python: they can use the process's ambient authority. Use Harness-managed tools when a Host needs a common boundary for current authorization, resolved resource identity, short-lived credentials, output bounds, and dispatch certainty.

This is not an operating-system sandbox. Provider isolation and Host access policy remain separate. Client-executed declarations belong in [Client-side tools](client-tools.md).

## Select tool permissions

Permissions work for ordinary local Pydantic tools as well as managed tools. Add `ToolPermissionsCapability` to the definition; keep fresh resource authorization in `InvocationPolicyCapability`:

```python
from a13n_harness.tools import ToolPermissions, ToolPermissionsCapability

permissions = ToolPermissionsCapability(
    ToolPermissions(
        default="auto",
        rules={
            "environment.shell_exec": "review",
            "filesystem.remove": "ask",
            "tool/reporting/*": "allow",
            "mcp/untrusted-source/*": "deny",
        },
    )
)
```

Use the actual prepared stable IDs, not display names. Managed tools retain their declared IDs; ordinary tools use `tool/<toolset-id>/<original-name>`, and local MCP tools use `mcp/<source-id>/<original-name>`. Source/name segments are percent-encoded. MCP needs a stable source ID. Prefixing, renaming, ToolProxy, and CodeAct do not change a target's permission identity. Hosts using custom naming wrappers can attach `ToolIdentityToolset` before them.

Exact rules win over the longest `.*` or `/*` prefix, then `*`, then `default`. `auto` uses the tool default: shell command launches use `review`, other tools use `allow`. `allow` continues, `deny` fails before custom validation, `ask` requests human approval, and `review` consults a matching reviewer. If no reviewer is configured or matches, review adds no restriction. None of these modes supplies credentials or bypasses Host/Environment policy.

### Configure or replace the reviewer

A model-backed reviewer is separate from the business Agent and has no execution tools:

```python
from a13n_harness.capabilities import ToolReviewCapability, ToolReviewConfig

review = ToolReviewCapability(
    ToolReviewConfig(
        model="review-model",
        instruction="Request approval before sending private customer data.",
        shell_instruction="Request approval for destructive shell operations.",
        timeout_seconds=30,
        on_error="approval_required",
    )
)
# Pass permissions and review in HarnessBuilder.build(..., capabilities=(...)).
# Your Host's Run Model resolver resolves the logical "review-model" selection.
```

The packaged system prompt stays separate from custom `instruction`. A shell-specific instruction replaces the general custom instruction for shell calls. Custom text is escaped inside `<custom-instruction>`; the tool schema, arguments, and task are request data, not reviewer instructions. Requests redact sensitive fields, omit environment variable values, and include bounded task and passive Environment context. Reviews run after structural validation but before custom validation, resource lookup, or dispatch. Timeout always denies, even after earlier approval; other failures follow `on_error`.

To implement a trusted reviewer without another model request:

```python
from a13n_harness import AgentContext
from a13n_harness.capabilities import (
    ToolReviewAssessment,
    ToolReviewRequest,
    ToolReviewResult,
)


class ExportReviewer:
    async def review(
        self, request: ToolReviewRequest, *, context: AgentContext
    ) -> ToolReviewResult:
        return ToolReviewResult(
            assessment=ToolReviewAssessment(
                decision="approval_required",
                reason="Confirm the export destination before sending data.",
            ),
        )


review = ToolReviewCapability(
    reviewers={"tool/reporting/*": ExportReviewer()},
)
```

A custom reviewer can return provider usage receipts in `ToolReviewResult.usage` or preserve proven receipts in `ToolReviewError`. The shared gate records them in the existing ledger with source `tool.review` and tool/call IDs; the shell compatibility adapter retains source `shell.review`. Completed `HarnessExtensionEvent(kind="tool")` events with `payload.type="tool_review_result"` expose the redacted result, including assessment and usage. Errors expose a safe code and effective decision with `result=null`. Do not account the event receipts a second time. Missing reviewers produce neither a review call nor a result event.

### Read human approval provenance

`ToolApprovalContext` is available through the read-only `AgentContext.tool_approval` accessor. It is `None` outside the active call, isolated between concurrent calls, and exposes immutable `tool_id`, `tool_call_id`, and `approved_sources`:

- `permission`: approval requested by configured permissions or managed policy;
- `reviewer`: approval requested by review;
- `tool`: approval requested by the tool itself.

Automatic `allow` decisions never populate this set. A tool needing its own confirmation checks its own source:

```python
from pydantic_ai import RunContext
from pydantic_ai.exceptions import ApprovalRequired


def export_report(ctx: RunContext[AgentContext]) -> str:
    approval = ctx.deps.tool_approval
    if approval is None or "tool" not in approval.approved_sources:
        raise ApprovalRequired(metadata={"reason": "Confirm report export"})
    return "Export confirmed"
```

Earlier approvals survive another suspension of the same call; approving the reviewer does not approve a later tool or managed-policy decision. The Host still uses native deferred requests/results and authenticated feedback. Changes to identity, schema, arguments, or approval-bound resources invalidate prior evidence. Review and current policy run again on resume, so a fresh denial still blocks execution.

Provider-native tools and external client tools are not local permission-gate calls. Select Host Web search if `web.search` needs a non-allow permission; an active provider-native search tool with that policy fails explicitly instead of claiming enforcement.

## Author and authorize a tool

This complete offline example wraps a read-only function in managed metadata and supplies a fresh Run policy. `TestModel` exercises the tool without an API key:

```python
import asyncio

from a13n_harness import HarnessBuilder, RunBindings
from a13n_harness.tools import (
    HarnessTool,
    HarnessToolMetadata,
    InvocationPolicyCapability,
    InvocationPolicyDecision,
    ToolOutputPolicy,
)
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.capabilities import Capability
from pydantic_ai.models.test import TestModel


def application_status() -> str:
    """Read the application's status."""
    return "ready"


async def read_policy(invocation, metadata, *, context):
    if metadata.effects <= {"read"}:
        return InvocationPolicyDecision.allow()
    return InvocationPolicyDecision.deny("Only reads are allowed in this Run.")


async def main():
    tool = HarnessTool(
        application_status,
        harness_metadata=HarnessToolMetadata(
            tool_id="example.application-status",
            effects=frozenset({"read"}),
            credential_audiences=(),
            idempotency="read_only",
            output_policy=ToolOutputPolicy(
                max_inline_bytes=4096,
                max_output_bytes=4096,
                overflow="fail",
            ),
        ),
    )
    executable = HarnessBuilder().build(
        AgentSpec(),
        model=TestModel(),
        output_type=str,
        capabilities=(Capability(tools=[tool], id="application-tools"),),
    )
    result = await executable.run(
        "Read application status",
        bindings=RunBindings.embedded(
            capabilities=(InvocationPolicyCapability(evaluator=read_policy),)
        ),
    )
    assert result.status == "completed"
    print(result.output_or_raise())


asyncio.run(main())
```

The definition owns the tool's stable semantics. The fresh Run binding owns the policy collaborator. Saved state restores neither policy authority nor credentials.

## Describe the real effect

`HarnessTool` is a thin native `Tool` helper; it accepts normal tool options plus a required `HarnessToolMetadata`. Metadata contains:

| Field                    | Responsibility                                                                 |
| ------------------------ | ------------------------------------------------------------------------------ |
| `tool_id`                | Stable tool identity, distinct from a model-facing name                        |
| `effects`                | Nonempty set of `read`, `write`, `delete`, `execute`, `external_communication` |
| `credential_audiences`   | Exact audiences for credentials needed at dispatch; at most 16                 |
| `idempotency`            | `none`, `read_only`, or supported `provider_key` semantics                     |
| `output_policy`          | Finite inline/total output bounds and overflow/redaction behavior              |
| `resource_resolver`      | Optional async resolution of validated arguments to canonical resources        |
| `superseded_by_tool_ids` | Explicit replacement identities used in surface resolution                     |
| `shell_review`           | Whether the tool participates in the shell-review mechanism; default false     |

Do not claim `read_only` for an operation that writes or sends externally just to obtain retries. Metadata is trusted Host code, not model-authored JSON. Supplying the reserved metadata key twice or incomplete metadata fails validation.

`ToolResourceResolver(arguments, *, context)` returns `CanonicalResource` values with `namespace`, `kind`, `identifier`, and optional `approval_revision`. Resolve identifiers under current Provider/Host authority; a path string or URL supplied by the model is not automatically the resource's canonical identity.

## Evaluate current authority

`InvocationPolicyEvaluator` receives a `ToolInvocationContext`, tool metadata, and the current `AgentContext`. The invocation contains stable correlation, current instance/Run identity, normalized arguments and digest, canonical resources, optional idempotency key, and deadline. Return one of:

- `InvocationPolicyDecision.allow()`.
- `InvocationPolicyDecision.deny(reason)`.
- `InvocationPolicyDecision.require_approval(reason, metadata=...)`.

Reasons are bounded nonblank text when supplied; approval metadata is finite JSON bounded to 16 KiB. The Host should expose only appropriate safe information. A policy decision is about this prepared invocation, not a permanent grant to future Runs.

`InvocationPolicyCapability` requires an evaluator. Its optional collaborators are `credential_broker`, `grant_broker`, and `approval_verifier`; `strict_managed_tools` defaults to false and `max_dispatch_retries` defaults to 1 (range 0–3). Attach it through fresh Run bindings, not a serialized definition. Without an attached restrictive policy, do not assume managed metadata alone denies an operation. Strict managed mode rejects tools outside the managed boundary; it does not retroactively isolate Python code already running in the Host.

## Credentials and grants

After authorization, a `CredentialBroker.acquire(audience, invocation, *, context)` can return a `CredentialLease`. The lease carries an opaque value and optional async close callback. First-party adapters access invocation-scoped values through `current_invocation_scope()`; values must not be copied into model arguments, observations, or continuation state.

An `InvocationGrantBroker.issue(...)` can supply an `InvocationGrantRef`: an opaque grant ID, audience, claims digest, and timezone-aware expiry. The Host/provider owns issuance and verification. A serialized reference is not sufficient authority by itself.

Credential leases close at the invocation boundary. Closing is idempotent. The Host still owns separately created clients, secret storage, refresh policy, and transport cleanup.

## Approvals and uncertain outcomes

`ApprovalVerifier.verify(invocation, approval_metadata, *, context)` lets the Host verify optional saved approval metadata against current authority. Standard Environment approvals bind to logical paths, not automatically to a backing target generation. Replacing a target invalidates runtime references but does not by itself revoke path-based approval. Enforce exact-target restrictions with current policy or a verifier when your application requires them.

A root can suspend for approval and later resume through [deferred feedback](state-and-resume.md). Children do not create durable deferred work. Never treat an old approval or a tool-call ID as authorization without the current prepared invocation.

Dispatch retries are bounded and require the supported certainty/idempotency conditions. `max_dispatch_retries` is not permission to replay an unknown external write. Transport timeout, cancellation, or cleanup failure can leave side effects uncertain; use the provider's receipt/reconciliation contract.

## Bound tool output

`ToolOutputPolicy` requires `max_inline_bytes` (512–262,144) and `max_output_bytes` (positive, at most 512 MiB and not below the inline bound). `overflow` is `spill` by default, or `truncate` / `fail`; `redact` defaults to true.

A spill needs the supported Environment output path and current access. It does not grant additional file authority. Truncation or references are not complete inline results. Redaction at this managed output boundary is not a universal promise that arbitrary logs, custom callbacks, or unmanaged tools are secret-free.

See [Environment tools](environments.md) for canonical resource and model-visible operation integration, and [Host embedding](hosting.md) for durable command ownership outside the process-local boundary.

# Configure an Agent with the assistant

The configuration assistant discusses an Agent's instructions, Models and tools, then saves a draft for you to review. **Saving a draft does not change the Agent.** Applying the reviewed draft is a separate user action.

## Start a conversation

In Console, choose **Agents → Create agent → Configure with assistant** to create an Agent. To change an existing Agent, open its details and choose **Configure with assistant**. From **Version history**, select a version and choose **Configure from this version** to start from historical configuration.

You need a User identity and permission to create an Agent, or read and create Revisions of the selected Agent. Configuration conversations belong to their User owner; sharing a Workspace or having an administrator role does not grant access to another user's conversations.

The readiness check requires an enabled, configured Model Provider and a compatible Model. It does not call the Model or consume inference quota. If setup is missing, Console links to the existing Model settings. For a manually entered Model, set **Tool calling support** according to its actual capabilities. Unknown capabilities do not qualify it as a fallback assistant Model.

The assistant uses deployment-bundled, read-only configuration knowledge. You do not need to create a Sandbox to discuss or edit configuration. Model execution uses your authorized resources and records ordinary Run usage.

## Review and apply

1. Describe the Agent's purpose, expected results and required tools. The assistant can select existing authorized resources; it cannot create credentials or roles.
2. Inspect the current draft. Review its complete configuration and differences against the original source and current target. A historical source is distinct from the version that applying the draft would replace.
3. Use **Edit draft** for direct changes, or **Validate candidate** to refresh dependency checks. Validation is not proof of successful execution.
4. Choose **Review and apply**. Review or edit the assistant's optional version note, which stays separate from the Agent description. The review retains the exact draft version, digest and target version. Candidate execution is currently unsupported; applying requires your explicit reason for proceeding without execution verification.
5. Apply the reviewed content. The receipt identifies the resulting Agent and immutable Revision. A no-change application reuses the existing Revision.

If another edit advances the target, application fails without overwriting it. Review the base, candidate and current target differences, then edit the complete candidate and choose **Use edited candidate and rebase**. Rebase preserves the original source while updating the concurrency baseline. Review again before applying. A lost response can be retried with the same command identity or reconciled from the retained receipt.

## Continue or compare approaches

Each Session owns one long-lived draft. Application keeps it open, advances its version once, and moves its base to the resulting Agent Revision. The first create application binds that same draft to the new Agent; later applications update it. The original source remains unchanged. Application history retains each reviewed version and its receipt, including after further edits.

Use **Fork conversation** after a completed Run to continue the discussion in another Thread. Every Thread in the Session edits the same draft, with version checks preventing stale writes. Start a new conversation for an independent candidate. While a Run is active or waiting for an answer, finish or stop that work before submitting a new configuration message.

The system assistant is intentionally absent from Agent management and ordinary invocation selectors. Its owner's Runs, results, usage and configuration history remain available through the conversation.

## Assistant token budget

Each new ordinary configuration message defaults to a cumulative budget of **2,000,000 input plus output tokens** and at most 40 Model requests. Repeated history sent to the Model counts again on each request. This is an execution budget, not the Model's context-window size.

Operators can override the default in their Service TOML configuration:

```toml
[configuration_assistant]
total_tokens_limit = 2000000
```

Alternatively, set `A13N_SERVICE_CONFIGURATION_ASSISTANT_TOTAL_TOKENS_LIMIT=2000000`. The environment variable takes precedence over TOML. Values must be positive integers; omission uses the bundled default. Restart the accepting Control or all-in-one process after changing the setting. It applies to subsequently accepted ordinary messages; existing Runs, retries and responses to pending questions retain their accepted limits.

## Deployment

Deploy compatible Control and Worker builds together. The assistant has a real, hidden Agent identity and no Agent Revisions. Each new ordinary input freezes the deployed assistant definition and selected Model into the Run's effective configuration. Recovery, retry and answers to pending questions retain the accepted configuration. Read-only built-in Skill files come from the current deployment; they are not version-locked or retained as historical bundles. The Service image protects the Skill and application directories from its non-root runtime user; only runtime data directories are writable.

The schema migration adds protected Session scope, draft associations and retained application receipts. Downgrade refuses to remove these protections while configuration data exists. Preserve the database when rolling back a deployment with recoverable configuration work.

# Environment credentials

Use `list_egress_credentials` and `get_egress_credential` for nonsecret metadata, hosts, state, ownership and versions. Use `configure_egress_credential` for creation, metadata updates, rotation, revocation or synchronization. All writes stay in the trusted Console form and existing HTTP APIs; no internal or external MCP credential-write tools exist. The current Console user needs write and run permissions. Never collect token values through chat, tool arguments, traces or shell. Organization credentials and organization administration are outside this workflow.

Allowed hosts are exact DNS names and immutable after creation. To change hosts, create a new credential and deliberately update its environment-template references. App-managed credentials (`app_id`) cannot be edited, rotated or revoked here. Preserve each original `expected_version`; sync does not take an ETag. Revocation removes the Service token permanently; it does not revoke the upstream token.

After a saved action, read `list_credential_sessions` or `list_environment_credential_sessions`. Their summary covers all matching bindings, independently of the current page. Preserve `applied`, `pending`, `retry`, `on_next_use`, `on_create` and `unknown` distinctions. Active Sessions update asynchronously; idle Sessions apply changes on next use. Sync only requests synchronization and does not connect idle Sessions. Never infer complete application from 202/saved or from one page.

The client returns only status, credential ID and available versions. Cancelled means no write request was sent; unknown requires reconciliation before any fresh request. After a conflict obtain a fresh read and review. Without a supported client, direct the user to Credentials.

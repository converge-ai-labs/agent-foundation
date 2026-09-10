# Manage Service resources

Service resources are not interchangeable configuration files. Some have mutable versions, some have immutable revisions, and some represent external targets whose physical lifecycle is asynchronous. Start with [HTTP contracts](http-contracts.md) before writing a resource-management client.

## Ownership and lifecycle map

| Resource family                                     | Ownership / versioning                                                                  | Main guide                                                             |
| --------------------------------------------------- | --------------------------------------------------------------------------------------- | ---------------------------------------------------------------------- |
| Organizations, Workspaces, membership, keys         | Current identity authority and resource preconditions                                   | [Identity](identity.md)                                                |
| Agents                                              | Managed identity plus immutable configuration revisions                                 | [Agents and Runs](agents-and-runs.md)                                  |
| Model Providers and Models                          | Organization/Workspace configuration; mutable ETag-protected resources, not revisions   | [Models](models.md)                                                    |
| Search Providers                                    | Scoped configured accounts, current credentials and references                          | [Search](search.md)                                                    |
| Application Accounts and targets                    | Workspace reception and object-specific routing                                         | [External tools](external-tools.md)                                    |
| Connector Providers / connections / MCP connections | Scoped provider accounts; Workspace connections and bounded external setup              | [External tools](external-tools.md)                                    |
| Environment Providers / Templates / targets         | Configuration, immutable Template revisions, and Workspace runtime targets are distinct | [Environment management](#environment-providers-templates-and-targets) |
| Skills / Skill revisions / uploads                  | Upload receipt, mutable resource head, immutable package revision                       | [Skills](#skills-and-upload-receipts)                                  |
| Assets                                              | Immutable content publication plus logical deletion                                     | [Assets](#assets)                                                      |
| Hook subscriptions                                  | Versioned delivery intent and retained delivery/replay evidence                         | [Hooks](#hook-subscriptions)                                           |
| Events and traces                                   | Observation with different retention/authorization contracts                            | [Streams and events](streams-and-events.md)                            |

Organization-owned configuration can be used by authorized child Workspaces. Actual connections, Environment targets, and Runs remain Workspace-owned. A null `workspace_id` in the relevant configured resource denotes Organization ownership; it does not make a resource public.

The [Native API reference](api-reference.md) includes every implemented management operation and its current request/response schema. Absence of an operation is meaningful: do not infer hard delete, copy, or rotate from another resource's API.

## Environment Providers, Templates, and targets

1. Read the Environment Provider type catalog for supported configuration and credential schemas.
2. Create an Organization- or Workspace-scoped configured Provider using that schema.
3. Author a Template that selects the Provider and the intended preparation, operation, and retention policy. Template revisions preserve authored history.
4. Allocate/select an actual Workspace Environment for a Thread or Run.
5. Inspect its current state and command receipts before stop/delete or recovery actions.

Provider configuration is desired data; credentials and live adapters are reconstructed under Host authority. Template revision, logical Environment identity, physical target generation, and a Run's process references are different identifiers.

Preparation can occur for the Run or on first use under the selected Template policy. Logical allocation alone does not guarantee a target exists or consume prepared-target capacity. First preparation reserves capacity; explicit limits do not evict unrelated targets. [Background tasks](background-tasks.md#environment-capacity) explains capacity and maintenance.

Stop and delete have distinct retention/lifecycle intentions. A command receipt records what the Service knows; it is not proof that an unavailable Provider performed a mutation. Reconnection to an existing generation and replacement of a lost target also differ. Never automatically replay an uncertain command merely because a process handle disappeared.

The underlying [Environment SDK](../a13n-environment/index.md) owns Provider operations and non-destructive adapter close. Service adds durable ownership, scheduling, capacity, and target retention. A mount root or provider label alone does not establish isolation.

### Hosted preparation and recovery

Templates support preparation at `on_run` or lazily at `on_use`. A later Run can select another Environment, while retry and waiting continuation keep their accepted selection. Provider-supported recovery occurs before dispatch. Remote Envd requires a fresh adapter after failed preparation; an HTTP Session can remain busy after an abandoned connection.

Rebuilding a missing managed target preserves the logical Environment ID but creates a new backing generation. Lost temporary files and processes are not restored. Service publishes the current target and generation before notifying the Agent of a rebuild or connection refresh. Reconnection to the same E2B target preserves the current scope's process observations; target replacement invalidates old runtime references. An uncertain dispatched command is never automatically replayed.

Standard Harness deferred approvals bind resolved paths, not backing generations. Replacing a target does not itself invalidate those approvals. Hosts requiring exact-target restrictions enforce them through current invocation policy or an approval verifier; see [deferred resume](../a13n-harness/state-and-resume.md#structured-suspension).

### Host-local placement

Host-local Providers record `host_id` when configured. Docker also records `docker_host`, so an existing Provider keeps using the same daemon even when a worker's environment variables change. Run workers and maintenance must execute on that host with access to the same protected bootstrap storage. Register an existing target once and reuse its Environment ID; another Provider record does not create a separate owner for the target.

Capacity reservations and idle retention belong to [worker maintenance](background-tasks.md#environment-capacity), not to the Provider constructor.

## Skills and upload receipts

Skill acquisition supports a Workspace ZIP upload receipt or a GitHub source. Creating or revising a Skill publishes normalized immutable package content; an upload by itself is not an enrolled Agent Skill.

### Publish a ZIP

Package `SKILL.md` and its supporting files into `skill.zip`, then stage it:

```bash
curl --fail-with-body "$SERVICE_URL/api/v1/workspaces/$WORKSPACE/skill-uploads" \
  -H "Authorization: Bearer $A13N_API_KEY" \
  -H 'Content-Type: application/zip' \
  -H 'Idempotency-Key: docs-upload-skill-001' \
  --data-binary @skill.zip
```

Read the receipt's `upload_id`, manifest, digest, and expiry. Create a Skill by POSTing the following body to `/api/v1/workspaces/{workspace}/skills`, with a new idempotency key and the actual receipt ID:

```json
{
  "name": "Documentation review",
  "source": {
    "kind": "zip_upload",
    "upload_id": "sku_0123456789abcdef"
  }
}
```

The publication receipt contains the Skill and revision. Keep those returned IDs for Agent selection; the example ID is not a real upload. Package normalization rejects malformed/unsafe paths, unsupported archive entries, and conflicting names rather than extracting arbitrary files.

| Package bound                           | Limit            |
| --------------------------------------- | ---------------- |
| Uploaded archive / total file bytes     | 64 MiB each      |
| Archive members / normalized files      | 8,192 / 4,096    |
| One file / `SKILL.md`                   | 16 MiB / 256 KiB |
| Skill description                       | 16 KiB           |
| Path depth / path bytes / segment bytes | 32 / 1,024 / 255 |

ZIP uses stored or deflated compression. A suitable single wrapper directory is normalized away. Use valid Skill front matter rather than relying on an arbitrary Markdown filename.

### Publish from GitHub

A `source` with `kind: "github"` accepts `repository_url`, optional `ref`, `subdirectory`, `expected_commit_sha`, and an authorized `credential_secret_id` for protected access. A 40-character expected commit guards the source resolution you intend. Acquisition involves external I/O; a mutable branch name is not an immutable package identity. The resulting revision records the resolved source and normalized content. This is not a public arbitrary Git credential or Secret creation API.

Use the Skill API to create/list/read/update/delete resources, inspect references, list/read immutable revisions, and download revision content. Agent acceptance resolves the selected revision/head into its captured graph. Later head edits do not rewrite an already accepted graph, while current access policy still applies.

An upload receipt can expire while referenced published content remains. Deleting a mutable head is not permission to remove bytes required by retained revisions, Runs, or audit. Do not treat the upload cache as the canonical Skill store.

Service Skills are not Harness UI Content Plugin repositories. Their ZIP/receipt/revision API differs from installing editable Git content in the terminal product.

## Assets

An Asset publishes immutable Workspace content. The upload accepts one `application/octet-stream` body, filename, optional media type, and an idempotency key. The default maximum size is 100 MiB, enforced while streaming; configure `assets.max_size_bytes` to change the bound.

For example, upload a local text file as a binary request and record the returned Asset ID:

```bash
curl --fail-with-body \
  "$SERVICE_URL/api/v1/workspaces/$WORKSPACE/assets?filename=notes.txt&media_type=text%2Fplain" \
  -H "Authorization: Bearer $A13N_API_KEY" \
  -H 'Content-Type: application/octet-stream' \
  -H 'Idempotency-Key: docs-upload-asset-001' \
  --data-binary @notes.txt
```

Download `/api/v1/assets/{asset_id}/content` with the same authorized credential; metadata is at `/api/v1/assets/{asset_id}`. The content response includes length, disposition, and digest ETag. It is not a JSON representation or multipart upload. URL-encode query values and preserve exact binary bytes.

A distinct publication receives a new Asset ID. Within the command's 24-hour replay horizon, the same canonical upload/key returns the original publication rather than creating another Asset. Metadata and content endpoints do not expose internal object keys or public object-store URLs.

Delete tombstones logical access immediately and records asynchronous object cleanup. Cleanup failure does not restore access. Retained cleanup/audit/replay evidence and physical bytes have different lifetimes; inspect authoritative state rather than treating an attempted object deletion as completion.

Use Assets as supported binary input references. An Agent input can otherwise name a supported URL or an Environment-relative source with its binding. Delivery modes and modality policy determine whether bytes, URL, or an Environment path reach the model; an Asset reference does not grant new modality support or broaden file permissions.

## Hook subscriptions

Workspace Hook subscriptions support create/list/get, supported PATCH/PUT changes, deletion, and delivery redrive. Run submission can also carry its supported inline Hook subscription contract. Follow the exact version/idempotency fields rather than assuming every Hook operation uses identical headers.

A Hook is delivery configuration, not synchronous execution of the callback inside the acceptance transaction. Delivery, retries, retention, and redrive use durable evidence and bounded policies. A consumer should tolerate the owning delivery contract and correlate events; a network timeout is not proof the recipient did not process the request.

History collection retains configuration required by waiting continuation, outstanding delivery, redrive, replay, and audit. Minimum retention age does not force deletion while those references remain. Endpoint policy and delivery authentication remain deployment/resource concerns; a callback URL is not trusted merely because it was saved.

## Identity settings and images

In addition to login, keys, and roles, the Native API includes:

- User and Organization profile changes.
- Workspace member/key/permission views.
- Organization and Workspace security audit, and current User security activity.
- User avatar and Organization/Workspace icon upload, deletion, and reads.
- Password reset and email-change start/complete flows.

These are separate from generic Asset publication. Read each operation's required boundary, content type, and preconditions. A browser image URL does not bypass the resource's access policy. Recovery links and tokens are credentials, not ordinary telemetry fields.

## Secrets and installed integrations

Credential-bearing Provider/connection requests use write-only inputs and encrypted storage backed by operator-supplied keys. Agent `secret_requirements` and submitted `secret_bindings` are separate execution contracts. Do not invent a generic Native Secret CRUD endpoint simply because internal Secret storage exists.

Installed Plugin and adapter keys select trusted code already present in the Service artifact. They do not accept an arbitrary module import or install a Python wheel from a resource request. [Configuration](configuration.md) owns artifact selection, while [external tools](external-tools.md) owns live Connector/MCP selection.

## Trace queries

Workspace trace search/detail are independent of durable lifecycle-event reads and of telemetry export. They require a configured installed query adapter **and** a trusted RunAttempt-backed access authorizer. The default composition does not supply the authorizer, so configuring a backend alone remains fail-closed.

Backend correlation metadata is not authorization evidence. Local Langfuse ingestion or its own UI can operate independently of the Service Trace Query API. See [observation boundaries](streams-and-events.md#traces-and-usage).

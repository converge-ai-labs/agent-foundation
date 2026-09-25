# Tenancy: who is asking, and what may they do

`tenancy/` owns organizations, workspaces, principals, their credentials, grants and invitations, authentication, authorization and the execution authority that accepted work carries. `infra/audit.py` and `infra/crypto.py` hold the audit record and the credential encryption every package uses. Tenancy knows nothing about agents or runs; it only knows that a row carries an `organization_id` and usually a `workspace_id`.

Route paths and request/response shapes belong to [10: API](10-api.md); this chapter owns who may call them and what the tenancy operations do. The process wiring of the authenticator, roles and grant sources is in [09: assembly](09-runtime.md#assembly).

## Nouns

- An **organization** is the administration boundary. Organizations are created only by [bootstrap](#bootstrap); no API creates or deletes one. A deployment normally has one, and every table allows many.
- A **workspace** belongs to one organization and is the resource and work boundary. Sessions, threads and runs belong to exactly one workspace; provider resources and models can be shared by every workspace of their organization ([04](04-resources.md)).
- A **principal** is a user or a service account. Users are people identified by an email address and are not owned by any organization. Service accounts are program identities with an immutable home workspace.
- A **credential** proves that a request comes from a principal. There are three kinds: a password (one per user, verified, never looked up), an API key (long-lived, named, confined to one workspace) and a token (a login session or a one-use password-reset or email-change link: short-lived and looked up by hash).
- A **grant** gives a principal a role at an organization or at one of its workspaces. A **role** is a named set of verbs.
- An **invitation** is a pending grant for an email address, accepted through a one-use link.

## Tables

```
organizations
  id  key  name  settings  image NULL  version  created_at  updated_at
  UNIQUE (key)

workspaces
  id  organization_id  key  name  settings  image NULL  archived_at NULL  version  created_at  updated_at
  UNIQUE (organization_id, key)

principals
  id  kind  name  description NULL  email NULL  home_workspace_id NULL  status  image NULL
  version  created_at  updated_at
  kind   IN ('user', 'service_account')
  status IN ('active', 'disabled')
  UNIQUE (email)
  CHECK ((kind = 'user') = (email IS NOT NULL))
  CHECK ((kind = 'service_account') = (home_workspace_id IS NOT NULL))
  CHECK ((kind = 'service_account') = (description IS NOT NULL))
  -- trigger: id, kind, home_workspace_id and created_at never change

passwords
  principal_id  hash  updated_at
  PRIMARY KEY (principal_id)

api_keys
  id  organization_id  workspace_id  principal_id  name  secret_hash  expires_at NULL
  revoked_at NULL  last_used_at NULL  version  created_by_id  created_at  updated_at
  UNIQUE (secret_hash)
  -- trigger: id, organization_id, workspace_id, created_by_id and created_at never change

tokens
  id  principal_id  kind  secret_hash  data NULL  expires_at  revoked_at NULL  created_at
  kind IN ('session', 'password_reset', 'email_change')
  UNIQUE (secret_hash)

grants
  id  organization_id  workspace_id NULL  principal_id  role  created_by_id  created_at
  UNIQUE (principal_id, organization_id, COALESCE(workspace_id, ''))
  -- trigger: rows are never updated

invitations
  id  organization_id  workspace_id NULL  email  role  token_hash  invited_by_id
  principal_id NULL  expires_at  accepted_at NULL  revoked_at NULL  version  created_at  updated_at
  UNIQUE (token_hash)
  CHECK (accepted_at IS NULL OR revoked_at IS NULL)
  CHECK ((accepted_at IS NULL) = (principal_id IS NULL))

audit_events
  id  organization_id NULL  workspace_id NULL  actor_id NULL  action  target_kind  target_id
  outcome  details  occurred_at
  outcome IN ('ok', 'denied', 'failed')
  CHECK (organization_id IS NOT NULL OR workspace_id IS NULL)
  -- trigger: rows are never updated or deleted
```

Notes on the shape:

- Rows with a `version` are mutable; a database trigger increments `version` and `updated_at` on every update, and the strong ETag of their API views is derived from identity and version ([10](10-api.md#preconditions)). Recording `api_keys.last_used_at` is exempt: it keeps the key's version and ETag.
- Email addresses are compared in lowercase everywhere: login, invitations, reset, email change and the operator commands.
- `passwords` holds one Argon2id hash per user, replaced in place, never expired and never looked up by hash. It shares no table with anything the [expiry sweep](#expiry) deletes.
- `tokens.data` holds the one value a kind needs: `{"new_email": ...}` for `email_change`, nothing otherwise.
- `grants.workspace_id IS NULL` is an organization-scope grant; it applies to every workspace of the organization. `role` is validated text, not a database CHECK, because distributions add roles ([Roles and grant sources](#roles-and-grant-sources)). A role change replaces the row.
- `workspaces.settings` holds workspace-wide defaults that are not resources; today only the media-understanding model defaults under `media`, owned by [04](04-resources.md). `organizations.settings` is reserved; no operation reads or writes it.
- `image` holds the reference to an organization's or workspace's icon or a user's avatar ([Images](#images)); service accounts have none. `description` is a service account's free text (at most 2048 characters, default empty).
- Organization and workspace keys match `^[a-z0-9][a-z0-9_-]{0,127}$`.
- `api_keys`, `grants`, `invitations` and `audit_events` reference their workspace by the pair `(organization_id, workspace_id)` ([Tenant integrity](#tenant-integrity)).
- `audit_events.organization_id` is NULL only for account-wide events (a user's own account and login sessions), which belong to no tenant.
- Principals, passwords, API keys and accepted invitations are never deleted; disabling and revocation are status columns, so history keeps every identity it names. Grants are deleted when an administrator removes them, and the [expiry sweep](#expiry) deletes dead tokens and dead unaccepted invitations.

## Authentication

The local authenticator accepts two request credentials:

| Credential    | Presented as                                                                                          | Live while                                              |
| ------------- | ----------------------------------------------------------------------------------------------------- | ------------------------------------------------------- |
| API key       | `Authorization: Bearer a13n_…`; looked up by its SHA-256 `secret_hash`                                | `revoked_at IS NULL` and `expires_at` is NULL or future |
| Login session | cookie `__Host-a13n_session` (Secure, HttpOnly, SameSite=Strict, Path=/): a `tokens` row of `session` | not revoked and not expired                             |

Browsers accept a `Secure` cookie, and with it the `__Host-` and `__Secure-` prefixes, only over HTTPS. When `server.public_url` is plain HTTP, the login-session cookie is therefore `a13n_session` without `Secure`, and a connection's browser-flow cookie ([04](04-resources.md#connections)) drops its `__Secure-` prefix and `Secure` likewise.

When an `Authorization` header is present the cookie is ignored; a scheme other than `Bearer` or a secret longer than 512 characters is `unauthenticated`. A request without either credential is `unauthenticated` on every protected route. Authentication reads the credential and then the principal's current status and grants; a disabled principal is `unauthenticated`. The resulting principal value carries its grants and its confinement: an API key's workspace, and always the home workspace for a service account. Authenticated responses are never cached ([10](10-api.md#authentication)).

Login sessions expire after `auth.session_seconds` and roll forward: each authenticated cookie request resets the cookie lifetime, and the server extends `expires_at` when more than a minute of it has elapsed. `api_keys.last_used_at` is written at most once per minute per key.

**Browser protection.** A cookie-authenticated request with a method other than GET, HEAD or OPTIONS must carry `X-CSRF-Token` equal to the session's CSRF token, and an `Origin` header, when present, must be a public origin: the origin of `server.public_url` and, when its host is `localhost` or `127.0.0.1`, the same origin under the other of those names, which reaches the same server. The request's own Host is never trusted as a public origin. The CSRF token is an HMAC-SHA256 keyed by the session secret, stable for the session, derivable from the cookie and stored nowhere. Login, invitation acceptance and the session-restore read return it. The public account flows (login, bootstrap, password-reset request and confirmation, email-change confirmation, invitation acceptance) check `Origin` the same way. A failed check is `forbidden`. Bearer requests need neither check.

**Login.** Password login verifies the user's `passwords` row with Argon2id and creates a `session` token (audited `login_session.create`). An unknown address, a disabled account and a wrong password all return the same `unauthenticated` response, and an unknown address still costs one hash. The password is verified outside the transaction; the session is issued only if the stored hash is unchanged under the row lock. Password hashing is limited to two concurrent hashes per process. Logout ends the current login session and clears the cookie; it requires a login session ([account operations](#authorization)).

**Rate limits.** Credential-guessing flows count attempts in fixed windows of `auth.login_window_seconds` and refuse past `auth.login_limit` with `rate_limited` and `retry_after_seconds`:

| Flow                                                                                      | Counted per                |
| ----------------------------------------------------------------------------------------- | -------------------------- |
| login                                                                                     | client address, email      |
| bootstrap                                                                                 | client address             |
| password-reset request                                                                    | client address, email      |
| password-reset confirmation, email-change confirmation                                    | client address             |
| invitation acceptance                                                                     | client address, invitation |
| each check of the caller's current password (email change, password change, self-disable) | client address, principal  |
| the connection authorization callback ([04](04-resources.md#connections))                 | client address             |

Each flow counts in its own budget. The client address is the peer address, or the forwarded client address when the peer is one of `server.trusted_proxies`. Counters are held in Redis and are best-effort: while Redis is unreachable a check is skipped and logged ([09](09-runtime.md#redis)).

**Replacing authentication.** A distribution may install one authenticator in place of the local one:

```python
# Conceptual protocol; `Principal` is tenancy's detached principal value.
class Authenticated:
    principal: Principal     # validated identity, current grants and confinement
    credential_id: str
    kind: str                # the local authenticator issues "session" and "key"

class Authenticator(Protocol):
    async def authenticate(self, request: Request, response: Response) -> Authenticated | None: ...
        # None: no credential presented; `response` is the route's own, such as to renew a login cookie
    async def recheck(self, session: AsyncSession, credential: Authenticated) -> None: ...  # read-only liveness
    async def logout(self, request: Request, response: Response, credential: Authenticated) -> None: ...
```

Long-lived requests such as thread streams call `recheck` and reload current status and grants on their refresh interval ([07](07-facts-and-delivery.md#the-thread-stream)). Membership, grant, API-key, service-account and account management keep working under a replacement, because they are tenancy functions that take a principal value. The local account routes stay installed; session restore (`GET /auth/session`) requires a local login session (403 `forbidden` otherwise).

## Authorization

A request is authorized by one rule over detached values:

```python
Verb = Literal["read", "run", "write", "admin"]

def authorize(principal: Principal, resource: Scoped, verb: Verb, *, authority: ExecutionAuthority | None = None) -> None:
    """Raise `forbidden` (details: verb) unless the principal's grants cover the resource with the verb."""
```

`Scoped` is anything with `organization_id` and an optional `workspace_id`: a row, a view, or a scope literal for collection reads and creates. A resource with `workspace_id IS NULL` is shared by the organization; the organization itself is such a target.

The built-in roles:

| Role    | Verbs                   |
| ------- | ----------------------- |
| viewer  | read                    |
| runner  | read, run               |
| builder | read, run, write        |
| admin   | read, run, write, admin |

The verbs a principal holds on a target are computed as follows:

1. **Confinement.** A confined principal (an API key, or any service account) holds nothing outside its workspace: nothing in another organization and nothing in another workspace of its own organization.
2. **Grants.** Only grants in the target's organization count, and their verbs are unioned. On a workspace target, organization-scope grants and grants at that workspace contribute their role's verbs. On an organization-shared target, organization-scope grants contribute their role's verbs and workspace grants contribute at most `read` and `run`: sharing lets every workspace use a resource, but changing it changes it for everyone.
3. **Confined credentials on shared targets.** A confined principal keeps at most `read` and `run` on organization-shared targets, whatever its principal's grants. A workspace API key is therefore never an administrator credential for its organization, even when it belongs to an organization administrator.

What each verb covers, by example (the owning chapters name the verb of each operation):

| Verb  | Covers                                                                                                                                                                                                            |
| ----- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| read  | listing and reading everything in scope, including other principals' sessions, threads and runs; run items, thread streams, usage and traces; memory files and their history; secret metadata, never a value      |
| run   | submitting input, steering, interrupting, forking, resuming; creating sessions and threads; thread environments and memory mounts; editing and restoring memory files                                             |
| write | creating, updating and retiring resources: agents and revisions, skills, templates, providers and models, connections and their authorization, secrets, assets, memories and the purge of a memory file's history |
| admin | grants, invitations, service accounts and their keys, the workspace's API keys, workspace settings, webhook subscriptions, audit reads; at organization scope also workspaces and the organization                |

`admin` covers people, keys and delivery configuration. A builder can configure external models and tools; the roles do not promise data-loss prevention against a builder or against an authorized run, and deployment network policy constrains outbound destinations independently of roles ([08](08-providers.md)). Private secrets and private environments add an owner check to the workspace verb ([04](04-resources.md#secrets), [06](06-environments.md)).

**Path resolution conceals other tenants.** An organization path resolves only for a principal holding a grant in it. A workspace path resolves by ID to a workspace of one of the principal's organizations, or by key among the workspaces the principal can read (a confined principal: only its own); a key that matches readable workspaces in several organizations is `conflict` with reason `ambiguous_key`. Anything else is `not_found`, so a path never reveals another organization or its workspaces. Inside its own organization a principal can learn by ID that a workspace exists and be refused with `forbidden`. Globally unique IDs are never access control: lists, content reads, events, traces and replay lookups authorize their scope before resolving a supplied ID.

**Archived workspaces** refuse every verb but `read` with `disabled` (details: kind, id). Removing access is offboarding and stays allowed: administrators can still delete grants at an archived workspace, revoke its invitations, disable or retire its service accounts and revoke API keys confined to it, and a writer can revoke its connections' credentials ([04](04-resources.md#connections)). Resource rows of an archived workspace refuse every change the same way ([04](04-resources.md#rules-every-kind-follows)). Organization and workspace views carry `permissions`, the caller's verbs at that scope (only `read` on an archived workspace); clients shape their UI from it, and the server still authorizes every operation.

**Account operations** (profile and avatar changes, email and password change, login sessions, logout, self-disable, the account's audit trail), every [API-key issuance](#api-keys), every [invitation](#invitations) creation or resend and every browser authorization of a connection ([04](04-resources.md#connections)) require a user's own login session, or a replacement authenticator's unconfined user credential. An API key can read its principal's profile but never changes the account behind it or mints anything that would outlive it; each of these operations refuses it with 403 `forbidden`.

### Roles and grant sources

Roles are data. The role registry is fixed at assembly: the four built-ins plus the roles a distribution declares ([09](09-runtime.md#extension-points)). A role name matches `^[a-z][a-z0-9_]{0,31}$` and maps to a non-empty subset of the four verbs; an invalid definition or a duplicate name fails assembly. Grant and invitation creation accept only registered roles (`invalid_argument`, reason `unknown_role`). A stored or sourced grant naming a role the registry does not define fails closed: its principal is refused with `forbidden` on every request until the grant is removed.

A distribution may add grant sources:

```python
class RoleGrant:                  # conceptual
    organization_id: str
    workspace_id: str | None
    role: str

class GrantSource(Protocol):
    async def grants_for(self, principal: Principal) -> Sequence[RoleGrant]: ...
```

Sourced grants are unioned with stored ones whenever a principal loads, subject to the same confinement and role registry. A source is called inside short transactions, so it answers from its own bounded cache refreshed outside any transaction, never from the network, and fails closed (raises or returns nothing) when that cache is stale.

Grants are loaded once per request, and again wherever an operation revalidates them: inside every administrative operation and invitation acceptance, on each stream refresh and on each worker authority check.

### Administration

Every administrative operation runs in one `administering` transaction bound to an organization or workspace path:

- **Changes** take the organization row lock (`FOR NO KEY UPDATE`) first, then re-read the actor's principal `FOR KEY SHARE` and its grants without row locks, since the organization lock serializes every grant change there, and require `admin` at the path scope. A concurrent status change (a disable, including a service account retired when it loses its last grant), which locks the principal `FOR UPDATE`, or grant removal either commits first and is seen, or waits for the change. A membership change locks its member only `FOR NO KEY UPDATE`, so an authority check never waits for one, and administrators changing each other's grants in different organizations cannot deadlock. An archived workspace refuses the change with `disabled`, except the offboarding changes under [Authorization](#authorization). Locks are taken in the order organization, then principal, then grant.
- **Reads** (grant, member, invitation, service-account, key and audit lists; subscription reads) check the actor's current grants without locks, include archived workspaces and never wait for a concurrent change.
- **Denials** return `forbidden` (details: `verb: admin`) and record an `audit_events` row with `outcome = 'denied'` and `details = {verb, credential_workspace_id}` in its own transaction of at most two seconds, after the refused transaction ends, so the refusal never rolls back its only evidence. A failure to record the denial is logged and does not change the response.

## Execution authority

Accepted work runs under a persisted delegation, not under a login:

```python
class ExecutionAuthority:       # conceptual; stored as JSON on inbox entries and runs
    principal_id: str
    organization_id: str
    workspace_id: str
    verbs: frozenset[Verb]      # the ceiling: the caller's verbs in the workspace at acceptance
```

Accepting work requires `run` in the workspace and records the caller's verbs there, already narrowed by credential confinement. Successor and child runs inherit their parent's authority unchanged ([05](05-runs.md)). Every later use authorizes against both the ceiling and the principal's current status and grants, confined to the run's workspace: the ceiling must name the verb, the target must lie in its workspace or be shared by its organization (then only `read` and `run`), and the principal must currently hold the verb. Revoking grants or disabling the principal narrows or stops execution; a grant added later never widens it. An archived workspace stops execution: acceptance refuses it with `disabled`, so a queued or new entry fails in place, and attempt planning and every authority renewal treat it as lost authority, so a running run fails with `authority_revoked` within `worker.authority_seconds` ([05](05-runs.md#claim-heartbeat-and-authority)). Service tools and resource resolution during execution use this authority and the run's principal, never an identity of the worker.

Credential expiry, logout and API-key revocation stop further requests, not accepted work. Accepted work is stopped by interrupt, by disabling the principal, by removing its grants or by archiving the workspace. The worker rechecks authority every `worker.authority_seconds`, and a run whose principal lost it fails with `authority_revoked` ([05](05-runs.md#claim-heartbeat-and-authority)); calls already dispatched still complete.

## Tenant integrity

Every row that has both `organization_id` and `workspace_id` references its workspace by the pair `(organization_id, workspace_id)` → `workspaces (organization_id, id)`; a metadata test enforces this for the whole composed schema. A row therefore cannot name a workspace of another organization, even when an application query omits a predicate. References between workspace-owned rows include the workspace in their foreign keys, and references inside one owner also include that owner (a revision includes its head, a run its thread).

References to organization-shared rows (providers and models) use the pair `(organization_id, <referenced>_id)`, so the database guarantees the same organization. That the referenced row is shared or belongs to the referencing row's own workspace is checked by the owning service when the reference is written ([04](04-resources.md)). Scope never changes in place: triggers keep identity, scope and authorship of resource rows immutable. NULL is never a wildcard in generic queries.

## Flows

### Bootstrap

`a13n-service bootstrap --email EMAIL [--password-stdin]` reads the password from the first line of standard input or prompts for it twice; the password is never a command-line argument. In one transaction under a transaction-level advisory lock, and only if no organization exists, it creates the organization and a workspace (both with key `default`), the first user with that password, and an organization-scope `admin` grant, audited as `organization.bootstrap` in the scope of the new organization and workspace. It prints `{organization_id, workspace_id, principal_id}` as JSON. It exits 3 and changes nothing when an organization already exists, and exits 1 for an invalid address or a password shorter than 12 characters. Like every operator command it requires the database schema at this build's head ([09](09-runtime.md#schema-migrations)).

`POST /auth/bootstrap` with `{email, password}` does the same over HTTP and is public, so that Console can offer it to its first visitor: whoever reaches an uninitialized Service first becomes its administrator, and an operator exposing a new deployment to others runs the command first. It checks `Origin` and its rate limit, refuses with `already_exists` (kind `organization`) before hashing the password once an organization exists, and signs the new administrator in like login. `GET /auth/configuration` reports `initialized`, whether an organization exists.

### Organizations and workspaces

An organization administrator renames the organization, changes its key, creates workspaces and archives them. A workspace administrator renames the workspace and changes its key. An organization key is unique in the deployment and a workspace key within its organization (`already_exists`). Links that name an old key stop resolving; everything else refers to organizations and workspaces by ID. Archiving is permanent and leaves the workspace listed with `archived_at`; archiving an archived workspace is `conflict` (reason `archived`). Archiving revokes the workspace's unaccepted invitations in the same transaction, under the organization lock that acceptance also takes, and its audit details carry `revoked_invitations`. The organization list contains the organizations in which the caller holds a grant (a confined principal: only its own). The workspace list contains the workspaces the caller can read, archived ones included, across its organizations or within one requested organization.

### Grants

An administrator of an organization or workspace grants a role at exactly that scope to a principal already in the organization: a principal holding a grant there, or a service account homed in one of its workspaces. Any other principal ID, existing or not, is `not_found`, so no identity outside the organization is revealed; new people join through invitations. A principal holds at most one grant per exact scope (`already_exists`). A service account can be granted only at its home workspace (`invalid_argument`).

A grant row is never edited. Changing a principal's role at a scope replaces its grant in one transaction: the old row is removed under the rule below and a new row is added whose audit event records `replaces`, so the principal is never observed without a grant and never retired. The response carries the new grant ID; the old ID is then `not_found`, which makes the grant ID itself the precondition (grant changes take no `If-Match`). Requesting the grant's current role returns it unchanged. The same replacement serves the grant role change, a service account's role change and invitation acceptance.

Removing an organization-scope grant whose role includes `admin`, including by replacing it with a role without `admin`, is refused with `conflict` (reason `last_organization_admin`) unless another active principal holds such a grant. Removing a service account's last grant retires the account: it is disabled and its keys are revoked, audited as `service_account.disable` with reason `no_grants`; the principal row stays for history.

An organization's **members** are the principals holding a grant in it and the service accounts homed in its workspaces. Organization administrators list them, optionally by kind, as summaries `{id, kind, name, email, status, image_url}`, for example to choose whom to grant a workspace role.

### Invitations

An administrator invites an email address to a role at an organization or workspace; at most one pending, unexpired invitation exists per scope and address (`already_exists`: resend it instead). The link token is random and stored only as `token_hash`; the invitation expires after `auth.invitation_seconds`. Creating and resending require the administrator's login session ([account operations](#authorization)), since a link minted with an API key would outlive the key. Resending replaces the token and renews the expiry, and the previous link stops working. Revoking ends a pending invitation. Resend and revoke refuse settled invitations (`conflict`, reason `accepted` or `revoked`).

Acceptance is public and proves possession of the link. A wrong token or unknown invitation is `not_found`. An existing user proves their own password (`unauthenticated` otherwise, or when the account is disabled); an unknown address creates a user with the supplied password (at least 12 characters) and optional name. Under the organization lock, acceptance then requires:

- the invitation is neither accepted nor revoked (`conflict`, reason `accepted` or `revoked`) and not expired (`conflict`, reason `expired`, until the [expiry sweep](#expiry) removes it; afterwards the link is `not_found`);
- the inviter still holds `admin` at the invitation's scope under current grants (`conflict`, reason `inviter_lost_authority`);
- the address was not registered concurrently (`conflict`, reason `email_registered`).

The invited role replaces any grant the principal already holds at that exact scope. The grant is recorded as the invitee's act, with `invitation_id` and `invited_by_id` in its audit details. Acceptance stamps `accepted_at` and `principal_id` and starts a login session.

### Service accounts

A workspace administrator creates a service account with a name, a description (default empty) and a role (default `runner`), granted at the workspace in the same transaction. Updating it changes its name or description, replaces its role (or grants one again to a retired account), or sets its status; re-enabling an account without a grant is `conflict` (reason `no_grant`). Deleting it retires it: all grants are removed, all keys revoked and the account disabled, and it stays listed as disabled. Service-account changes take effect synchronously.

### API keys

Every key is confined to exactly one workspace; there are no organization-scope or global keys and no key-scope discriminator. A key's permissions are its principal's current grants intersected with that workspace, so a key can only narrow what its principal may do. The secret is returned once, at issuance, as `a13n_` followed by a random URL-safe string; only its hash is stored. `created_by_id` records the issuer; the key still represents its principal.

One issuance function serves every path. It resolves one workspace and requires that:

- the calling credential is a user's login session ([account operations](#authorization)): an API key never issues keys, not even an administrator's key for its own workspace, because a key minted by a key would outlive its parent's expiry and revocation;
- the workspace is not archived (`disabled`), and archiving waits for an issuance under way;
- the target principal is active (`disabled` otherwise) and currently holds at least one verb in the workspace (`forbidden` otherwise);
- an optional `expires_at` lies in the future (`invalid_argument`).

A user issues keys for themselves with an explicit ID of a workspace the user can read. A workspace administrator issues keys for a service account homed in that workspace, taking the workspace from the path. Listing one's own keys returns only the calling credential's workspace when it is confined. Workspace administrators list and revoke every key confined to their workspace, including users' keys, never a key confined elsewhere. Revocation is final (`conflict`, reason `revoked`, when repeated) and leaves the key listed with `revoked_at`.

### Account management

A user renames themselves, changes their email address or password, lists and ends their login sessions, and resets a forgotten password:

- **Email change** requires the current password and email delivery (`unavailable`, dependency `mail`, without it). The new address takes effect only when the one-use link sent to it is confirmed; whether the address is taken is decided only at confirmation (`already_exists`). Confirmation ends every login session and outstanding link of the account.
- **Password change** requires the current password and ends every other login session and outstanding link, keeping the current session.
- **Password reset** accepts an address and returns the same empty success whether or not an active user owns it; without email delivery nothing is sent. Confirming the link sets the new password and ends every login session and outstanding link.
- Reset and email-change links expire after `auth.link_seconds`. A new link supersedes any outstanding link of the same kind. A link is consumed under a row lock, so it works once. An invalid, used or expired link is `invalid_argument` (reason `invalid_or_expired`).

Password changes do not revoke API keys; keys are revoked individually or stop working when the account is disabled.

### Disabling

- A user disables their own account with their current password. It is refused with `conflict` (reason `last_organization_admin`) while the user is the last active holder of an organization-scope admin role in any organization, and with reason `concurrent_change` when such a grant is added meanwhile. No route re-enables a user.
- The deployment operator runs `a13n-service user disable --email` or `user enable --email`, outside any tenant's authority. The command prints `{id, status}` as JSON; a repeat changes and records nothing; an unknown address exits 1. It is audited with `actor_id` NULL and `details.authority = "operator"`.
- A workspace administrator disables or re-enables a service account of that workspace ([Service accounts](#service-accounts)).

Disabling a user also ends every login session and outstanding one-use link of the account, so enabling never revives one issued before. Disabling keeps grants and keys, so enabling restores them. From the commit on, every credential of the principal fails authentication and login is refused; accepted work fails at its next [authority check](#execution-authority). Workspace administrators cannot disable a user.

### Identity mail

Invitation, password-reset and email-change mail is staged in the outbox (kind `email`) in the transaction that needs it and sent over SMTP (`auth.mail`) by the [outbox delivery](07-facts-and-delivery.md#outbox). `auth.mail.timeout` bounds each send as a whole: at the deadline the connection is shut down, so a slow server can neither hold a delivery past its outbox claim nor complete it after a retry. The body is encrypted for the outbox row, and no link is ever logged. Links point at `server.public_url` and carry the token in the URL fragment, which browsers do not send to servers. Account mail (reset, email change) belongs to no organization. Without `auth.mail.smtp_host`, an invitation returns its link once to the inviting administrator (`delivery: manual`), reset requests send nothing and email change is unavailable; the public authentication configuration reports whether email delivery exists. Configuring SMTP requires an active encryption key ([09](09-runtime.md#settings)).

### Expiry

The `expire_credentials` sweep runs every `auth.expiry_scan_seconds` and deletes, in batches of `control.sweep_batch` per table, tokens that expired or were revoked and unaccepted invitations that expired or were revoked. It never deletes principals, passwords, keys, grants or accepted invitations. Scheduling is in [09](09-runtime.md#sweeps).

## Credential encryption

Every encrypted column (provider credentials and extra request headers; external target tokens; connection credentials, OAuth tokens, client secrets and pending authorization flows; secrets; subscription signing secrets; email outbox payloads and webhook outbox targets) uses one key ring, configured by `encryption.keys` (key ID to base64-encoded 32-byte key) and `encryption.active_key_id`, or instead by `encryption.key_file`: a file holding one such key, active under the ID `key_file`, which startup generates when the file is missing. It creates a private draft with mode `600` and links it into place, so concurrent first starts all read the one key that won:

- `protect(plaintext, location)` encrypts with AES-256-GCM under the active key and a fresh random 96-bit nonce, and returns the envelope `{key_id, nonce, ciphertext}`. Plaintext is at most 65536 bytes (`invalid_argument`). Without an active key it is `unavailable`.
- The location `(organization_id, table, column, row_id)` is authenticated data: an envelope decrypts only at the exact row and column it was written for. `organization_id` is NULL only for account-wide values such as account mail. Copying a value to another row, such as a subscription's signing secret into a webhook outbox row, reveals it and protects it again for the new location.
- `reveal(envelope, location)` fails with `unavailable` for an unknown key ID, another location or tampered bytes.
- Rotation adds a key and makes it active. Existing envelopes stay readable while their key ID remains in the ring; nothing re-encrypts them automatically.

Encrypted values are write-only at the API and never enter checkpoints, display, audit details or errors. Random high-entropy secrets (API keys, tokens, invitation tokens, lease tokens) are stored as SHA-256 `secret_hash` digests; passwords use Argon2id instead.

## Audit

`record(session, scope, *, actor_id, action, target_kind, target_id, outcome, details)` appends one `audit_events` row in the caller's transaction, so an event commits exactly when its change does. The scope is the target's own organization and workspace (the bootstrap event also names the first workspace); exactly the account-wide targets (`user`, `login_session`) have none. `details` is a JSON object of at most 8192 bytes and never holds credentials. `action` is a dotted name owned by the recording package, such as `grant.create`, `credential.revoke`, `invitation.accept`, `user.password.reset` or `agent.revision.set_default`. `actor_id` is NULL for operator commands and for actions no principal performs, such as a reset request or an expired connection operation.

Every tenancy change is audited in its own transaction, and a request that changes nothing records nothing; resource packages record their own mutations with the same function ([04](04-resources.md#rules-every-kind-follows)). Denied administrative operations are recorded as described in [Administration](#administration). Audit rows are immutable by trigger and never deleted; there is no retention purge.

Organization administrators page every event recorded in the organization, workspace events included; workspace administrators page that workspace's events. Pages are newest first ([10](10-api.md#collections)), and each event names its actor. Account-wide events belong to no tenant and appear in no tenant listing.

A user reads their own trail with their login session (an [account operation](#authorization)): every event they acted in, in any tenant, and every event targeting their account, including account-wide ones such as password changes, reset requests and the operator's disable or enable, newest first.

## Images

Users have avatars, and organizations and workspaces have icons; agents use the same mechanism ([04](04-resources.md#agents)). An image is sent as the raw request body and replaced or removed as a whole:

- Only PNG, JPEG and WebP are accepted, recognized by their signature bytes whatever the request declares; anything else is `invalid_argument` (field `image`, reason `unsupported_type`). An image is at most the smaller of `objects.upload_bytes` and `objects.max_bytes` (`payload_too_large`), and storing one spends the principal's [upload budget](04-resources.md#uploads-and-assets).
- A change passes its authorization, including an archived workspace's refusal, and its `If-Match` before any bytes are stored. The bytes are stored create-only at their SHA-256 digest under the owner's prefix ([07](07-facts-and-delivery.md#objects)) and the owner's `image` column keeps the reference. Images are not re-encoded, so metadata such as EXIF stays. Replaced or removed bytes are not reclaimed.
- Views carry `image_url`, the image route with `?v=<digest>`, so the URL changes whenever the image does; it is null without an image.
- Reads serve the stored type inline with the stored-content headers ([10](10-api.md#representations)). An owner without an image is `not_found`.

| Image             | Changed by                                                                       | Read by                                                                                                        |
| ----------------- | -------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| User avatar       | The user, with a login session and the profile `If-Match`; audited `user.update` | The user, and anyone with `read` in an organization where the user holds a grant; anyone else gets `not_found` |
| Organization icon | An organization administrator with the organization `If-Match`                   | Anyone who can read the organization                                                                           |
| Workspace icon    | A workspace administrator with the workspace `If-Match`                          | Anyone who can read the workspace                                                                              |

## Invariants

- A principal's verbs on a target never exceed the union of its grants in the target's organization; a confined principal holds nothing outside its workspace and only `read`/`run` on organization-shared targets.
- Every API key names exactly one workspace of its organization; every issuance requires a user's login session, never an API key, and checks the target principal's current grants in that workspace. An API key never mints a key, an invitation or a browser authorization.
- Grant removal and self-disable never leave an organization without an active principal holding an organization-scope admin role.
- A service account is granted and keyed only in its home workspace; without grants it is disabled and has no live key.
- Administrative changes check `admin` on the actor's current grants inside the changing transaction, under the organization lock; every such denial leaves a `denied` audit row.
- An archived workspace has no pending invitation, and a disabled user has no live login session or one-use link.
- Execution authority never widens after acceptance, and every use of it rechecks the principal's current status and grants and that the workspace is not archived.
- No row can reference a workspace of another organization; organization-shared references stay within their organization.
- Audit rows, grant rows and principal identities are immutable; a one-use link works at most once.

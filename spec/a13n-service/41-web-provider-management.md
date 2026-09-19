# Web Provider Management

## Design Position

Service supplies Provider-backed Web search and scrape plus credential-free built-in fetch and download. A Web Provider is a reusable Organization- or Workspace-owned account resource. Agent configuration independently selects compatible accounts for search and scrape; fetch and download never select or borrow a Provider account.

Web Providers are not Model Providers, Connectors, generic credential containers, or Agent-installed plugins. Provider credentials and administrative availability remain live resources. Agent Revisions and accepted Runs contain only stable references and bounded operation configuration.

## Boundaries

| Concern                                                     | Owner                                                                                               |
| ----------------------------------------------------------- | --------------------------------------------------------------------------------------------------- |
| Web Provider types, resources, selections, and live use     | This document                                                                                       |
| Agent Revisions, effective configuration, and Run overrides | [Agent Management](28-agent-management.md)                                                          |
| Scope, product permissions, and security audit              | [IAM](33-identity-and-access-management.md)                                                         |
| Resource-owned encryption and credential generations        | [Secret Management](27-secret-management.md#protection-boundary)                                    |
| Four Web tools and provider-neutral contracts               | [Harness Web resources](../a13n-harness/09-context-and-memory.md#media-documents-and-web-resources) |
| Attempt lifetime and fresh root/child bindings              | [Service–Harness integration](14-harness-runtime-integration.md)                                    |

Connectors and remote MCP connections retain their own schemas, credentials, and tool identities. A Web Provider exposes no arbitrary external tools.

## Trusted Provider Types

The Service process owns one finite code-reviewed catalog assembled before readiness from built-ins and Harness Web definitions in deployment-selected `a13n_harness.providers.plugins` manifests. Selection names installed metadata rather than an import target; package installation alone grants no trust. Manifests expose inert definitions and typed operation callbacks, not live clients. Service projects their metadata into the management API; embedded Harness and Service use the same vendor operation. Every implementation still enters this Web-specific management and operation path:

```python
class WebProviderDefinition:
    type: str
    display_name: str
    configuration_schema: JsonObject
    credential_schema: JsonObject
    authentication: Authentication
    setup_url: str | None
    setup_label: str | None
    operations: tuple[Literal["search", "scrape"], ...]
    supports_restricted_scrape: bool
```

The shared [authentication declaration](../a13n-harness/16b-model-provider-definitions.md#authentication) determines required, optional, or forbidden credentials from validated configuration, including defaults. PATCH validation applies to the resulting configuration and credential presence.

The operation list describes only capabilities implemented by this integration. `supports_restricted_scrape` is true only when an adapter can enforce requested-content restrictions throughout a remote scrape. It is not inferred from an input or returned URL check.

| Type         | Search | Scrape | Credential | Fixed API                      |
| ------------ | ------ | ------ | ---------- | ------------------------------ |
| `brave`      | Yes    | No     | API key    | Brave Web Search               |
| `exa`        | Yes    | Yes    | API key    | Exa Search and Contents        |
| `duckduckgo` | Yes    | No     | None       | DuckDuckGo HTML search         |
| `parallel`   | Yes    | Yes    | API key    | Parallel Search and Extract    |
| `tavily`     | Yes    | Yes    | API key    | Tavily Search and Extract      |
| `firecrawl`  | Yes    | Yes    | API key    | Firecrawl Search and Scrape    |
| `jina`       | Yes    | Yes    | API key    | Jina Search and Reader         |
| `perplexity` | Yes    | No     | API key    | Perplexity Search              |
| `serpapi`    | Yes    | No     | API key    | SerpApi Google organic results |

All built-ins accept an empty configuration object. Keyed types require a write-only `{ "api_key": string }` credential object; DuckDuckGo accepts no credential. DuckDuckGo's Instant Answer JSON API does not provide general web search results, so its adapter parses HTML search results. Jina search requires a key; this integration uses the same saved key for Reader. Requests use fixed official HTTPS endpoints and reject caller-supplied endpoints, headers, proxies, crawl controls, or credential references. All built-in remote scrapers reject restricted scrape because their upstream fetches cannot enforce those domains throughout. Exa Contents receives exactly the requested URL without subpage crawling. Its `text.maxCharacters` request bound is one character above the effective output budget so local truncation remains observable. Every adapter independently enforces the UTF-8 output limit and a finite wire limit. Search produces result records rather than answers or a second Model call. Scrape produces provider-processed text and does not promise raw HTML. Selected external types supply their own bounded typed configuration and credential object schemas and explicit operation flags.

## Web Provider Resource

```python
class WebProvider:
    id: WebProviderId
    organization_id: OrganizationId
    workspace_id: WorkspaceId | None
    type: str
    name: str
    configuration: JsonObject
    credential_configured: bool
    enabled: bool
    created_by: PrincipalRef
    updated_by: PrincipalRef
    created_at: datetime
    updated_at: datetime
```

`WebProviderId` uses the `wprov` prefix. Identity, ownership, and type are immutable. Organization Providers are visible in eligible Workspaces; Workspace Providers are local. Sibling-Workspace and cross-Organization references are concealed. Names are normalized and unique within the owning scope.

Create accepts type, name, configuration, enabled state, and a separate write-only credential object when required by the selected implementation. Reads expose only `credential_configured`. PATCH can replace name, configuration, enabled state, or credential under a strong ETag. Omission retains a credential; keyed types reject null or blank API keys; credential-free types reject supplied credentials. Disablement retains encrypted material and references. Credential rotation advances the resource without rewriting Agent Revisions.

All operations use short database sessions and the shared resource-owned protection primitive. Credentials never enter logs, model context, Agent configuration, Run payloads, portable Harness state, or ordinary responses. Scope cleanup destroys encrypted material while retained audit and execution identities keep their historical meaning.

## Agent Web Selection

Web operations are owned by the keyed `AgentConfig.toolsets.web` selection.

```python
class SearchToolConfiguration(DomainRestrictions):
    provider_id: WebProviderId | None
    max_results: int = 5

class ScrapeToolConfiguration(DomainRestrictions):
    provider_id: WebProviderId | None
    max_content_bytes: int = 512 * 1024

class FetchToolConfiguration(DomainRestrictions):
    max_content_bytes: int = 256 * 1024

class DownloadToolConfiguration(DomainRestrictions):
    pass
```

The Web Toolset owns `search`, `scrape`, `fetch`, and `download` Tool selections. Its group and children default disabled. Search and scrape choose accounts independently. They may explicitly reference the same account or different accounts, but never share mutable authenticated bindings. Fetch and download have no Provider ID, backend name, credential, or Provider authorization dependency.

Authoring validates visible ownership, enabled state, credential presence for keyed types, and operation compatibility for each selected Provider. Search-only types are rejected for scrape. Restricted built-in remote scrape is rejected with `web_scrape_domain_restrictions_unsupported`. Built-in-only selections require no Provider account. No validation request is sent to a vendor.

Run overrides replace the complete `web` Toolset entry when supplied; omission inherits and `enabled=false` disables it while retaining child settings. Accepted Runs freeze normalized values and stable Provider IDs, not credentials or Provider configuration. Recovery and Retry retain the accepted values; current account eligibility is rechecked at execution.

## Shared Domain Contract

Every operation owns independent `allow_domains` and `deny_domains` lists. These are the only public or internal domain fields.

- An empty allow list is unrestricted. Deny always wins.
- `example.com` matches the apex and any subdomain at a DNS-label boundary; it does not match `example.com.evil.test`.
- Entries normalize case, IDNA, and trailing dots and deduplicate after normalization.
- URLs, ports, credentials, paths, IP literals, malformed hostnames, and wildcard syntax are invalid.
- Each list contains at most 256 entries.

Search always filters returned result URLs locally, preserves provider order, and may return fewer or zero results. Equivalent native filters may be sent upstream only as an optimization; they do not control a vendor's internal crawl or index.

Scrape checks its input URL before Provider dispatch. Restricted remote scrape is available only when its adapter explicitly guarantees enforcement throughout the operation. No built-in remote scraper does, so restricted selection fails before dispatch; Service does not probe with built-in HTTP, inspect only the final URL, or fall back to fetch.

Built-in fetch and download check their own input and every redirect before DNS or network I/O. They retain endpoint validation, public-address enforcement, address pinning, original Host/TLS identity, bounded redirects, and per-hop authorization. One operation's rules never apply to a sibling. These controls do not regulate shell, plugin, Connector, client-tool, or arbitrary MCP egress.

## Four-Tool Execution Contract

| Tool       | Stable ID      | Implementation                                   | Environment          |
| ---------- | -------------- | ------------------------------------------------ | -------------------- |
| `search`   | `web.search`   | Selected Provider search port                    | Not required         |
| `scrape`   | `web.scrape`   | Selected Provider scrape port                    | Not required         |
| `fetch`    | `web.fetch`    | Credential-free direct HTTP text fetch           | Not required         |
| `download` | `web.download` | Credential-free HTTP stream to Environment files | Required at the call |

The tools are independent. Enabling one neither exposes nor authorizes another. There is no scrape/fetch fallback or Provider-backed fetch mode. Missing Environment does not block Agent save, Run acceptance, Harness entry, search, scrape, or fetch. Download reports the affected-call Environment/file-access failure when no usable Environment is mounted.

Search accepts a bounded query and result count and returns ordered title/URL/snippet records with optional Provider usage. Scrape accepts one HTTP(S) URL and content budget and returns bounded `content`, original `source_url`, provider `canonical_url`, optional `title`, `truncated`, and optional usage. Textual fetch directly reads supported HTTP response content without promising extraction, rendering, or anti-bot handling. Binary fetch returns `web_fetch_content_unsupported` and directs the caller to download or a suitable media/document tool. Download alone writes a file.

Model arguments contain no Provider IDs, backend selectors, credentials, endpoints, or routing overrides. Model-visible descriptions distinguish extraction, direct text retrieval, and file persistence.

## Live Authority, Retry, and Cleanup

Before each Provider dispatch and disclosure, Service rechecks the Attempt fence, executing Agent authority, exact tool identity, Provider visibility, enabled state, operation support, and current credential generation. It acquires encrypted material in a short session, closes the session, then decrypts for one bounded request. Rotation affects the next acquisition; disablement prevents later dispatch or disclosure after observed. No database state crosses external I/O.

Fetch and download recheck Attempt and Agent authority for their exact tool ID on every hop and body boundary. They never read or authorize a Web Provider. Downloads additionally use current Environment file permissions at the call.

The reusable Harness scrape boundary includes target-policy authorization and the callback in the request deadline, and caps returned UTF-8 content at the smaller of request and configured byte limits. Trimming preserves valid UTF-8 and existing truncation metadata. The transport validates the actual outgoing destination before dispatch, excluding generated query values to support vendor query credentials; configurable endpoint validation retains its sensitive-query restrictions.

One Provider operation has a 30-second total deadline. Service permits at most two dispatches to the same account only after an explicit rate-limit or temporary-unavailable response and sufficient retry budget. The public Provider extension boundary represents that evidence with `WebProviderResponseError`; an ordinary safe `WebProviderError` code does not authorize replay. Transport failures, timeouts, invalid responses, cancellation, and authorization failures are not replayed. A saved-account test performs one supported Provider dispatch and is separate from Run execution and usage accounting.

Provider receipts are recorded only when valid and reported; unknown cost remains unknown. Safe failure codes contain no credential, raw upstream response, private transport diagnostic, query, or extracted content. Clients and sensitive snapshots close on success, failure, cancellation, and Attempt exit.

## Management API and IAM

| Operation             | Route under `/api/v1`                                       |
| --------------------- | ----------------------------------------------------------- |
| List/read types       | `GET /web-provider-types`, `GET /web-provider-types/{type}` |
| Workspace accounts    | `/workspaces/{workspace}/web-providers`                     |
| Organization accounts | `/organizations/{organization}/web-providers`               |
| Read/update           | `GET`, `PATCH .../{provider_id}`                            |
| Test saved account    | `POST .../{provider_id}/test` with `{}`                     |
| Inspect references    | `GET .../{provider_id}/references`                          |

The API uses standard pagination, strong ETags, idempotency behavior, safe errors, and ID-or-key scope resolution. `web_provider.read` authorizes visible reads; `web_provider.manage` authorizes owning-scope create/update/test. Agent authoring additionally requires read access when selecting or changing a Provider. Execution authority comes from the accepted Agent graph and exact tool call, never management permission.

Console renders ordinary configuration and credential inputs from the selected definition’s schemas, including schema defaults, numeric bounds, choices, and nested objects. Credentials remain write-only; an untouched edit retains the saved credential. Web credential removal is not supported. No vendor-name branch chooses `api_key` or discards declared configuration.

## Invariants

1. Web Provider resources and `wprov` IDs replace the former Search Provider resource; legitimate search operation names remain.
2. Search and scrape select compatible accounts independently; fetch and download are built-in and credential-free.
3. Search, scrape, and fetch work without an Environment; only download needs Environment file access at its call.
4. Domain restrictions have one normalized allow/deny contract, local search filtering, and deny precedence.
5. Restricted built-in remote scrape and scrape on a search-only type fail before Provider dispatch, with no fallback.
6. Every credential acquisition and disclosure applies current authority without holding database state across external I/O.
7. Revision, Run, child, retry, continuation, and recovery paths retain each node's accepted selection and create fresh live bindings.
8. APIs and Native SDKs preserve typed Web selection, omission/null/replacement, ETags, redaction, and safe failures.

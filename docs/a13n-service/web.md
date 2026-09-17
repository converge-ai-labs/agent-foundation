# Configure Web Providers

Web access has four independent Agent tools:

- `search` returns title, URL, and snippet results through a selected Web Provider.
- `scrape` extracts readable text for one URL through a selected scrape-capable Web Provider.
- `fetch` directly reads a supported textual HTTP response without a Provider account.
- `download` saves an HTTP response to Environment files without a Provider account.

Search, scrape, and fetch work without an Environment. Download requires a usable Environment only when called. Fetch never uses Provider credentials, and Service never falls back between scrape and fetch.

## Save a Provider

Open **Resources → Providers → Web** and select the Workspace or Organization scope. Choose a Provider type and save it. Enter an API key for keyed types; DuckDuckGo needs no credential. The key cannot be read back; entering another key on edit rotates it. Organization Providers are visible to eligible Workspaces, while Workspace Providers remain local.

An explicit **Test provider** sends one search for `Agent Foundation` and can consume quota. Creating, testing, selecting, and disabling a Provider are separate operations. Inspect **References** before disabling an account.

Built-in capabilities:

| Provider   | Search | Scrape | Credential |
| ---------- | ------ | ------ | ---------- |
| Brave      | Yes    | No     | API key    |
| Exa        | Yes    | Yes    | API key    |
| DuckDuckGo | Yes    | No     | None       |
| Parallel   | Yes    | Yes    | API key    |
| Tavily     | Yes    | Yes    | API key    |
| Firecrawl  | Yes    | Yes    | API key    |
| Jina       | Yes    | Yes    | API key    |
| Perplexity | Yes    | No     | API key    |
| SerpApi    | Yes    | No     | API key    |

DuckDuckGo uses HTML search results; its public Instant Answer JSON API does not return ordinary web search results. Jina search requires an API key, and this integration uses that key for Reader as well. Every built-in remote scraper rejects restricted scrape because it cannot guarantee domain enforcement throughout the upstream operation. Search-only Providers cannot be selected for scrape.

## Configure an Agent

Agent configuration stores Web operations in the built-in `web` Toolset:

```json
{
  "toolsets": {
    "web": {
      "enabled": true,
      "tools": {
        "search": {
          "enabled": true,
          "permission": "inherit",
          "config": {
            "provider_id": "wprov_search",
            "max_results": 5,
            "allow_domains": ["example.com"],
            "deny_domains": ["private.example.com"]
          }
        },
        "scrape": {
          "enabled": true,
          "permission": "inherit",
          "config": {"provider_id": "wprov_exa", "max_content_bytes": 524288}
        },
        "fetch": {
          "enabled": true,
          "permission": "inherit",
          "config": {"max_content_bytes": 262144}
        },
        "download": {"enabled": true, "permission": "inherit", "config": {}}
      }
    }
  }
}
```

Each operation is optional. Search and scrape choose accounts independently; using the same Exa account for both is explicit. Fetch and download accept no Provider or backend selector.

`max_results` defaults to 5 and accepts 1–10. Content budgets are byte limits. Each operation can define its own `allow_domains` and `deny_domains`. Empty allow means unrestricted, deny wins, and `example.com` includes both the apex and its subdomains. Names normalize case, IDNA, and a trailing dot. Do not use URLs, paths, ports, IP addresses, credentials, or wildcards.

Search always filters returned URLs locally and can return fewer or zero results. Fetch supports textual responses; binary content produces `web_fetch_content_unsupported`, so use download or a suitable media/document tool. Scrape returns provider-processed `content`, source and canonical URLs, optional title, and truncation information; it does not crawl subpages by default.

Each Toolset entry in a Run override is a whole-entry replacement:

| Value                            | Meaning                            |
| -------------------------------- | ---------------------------------- |
| Omit `toolsets.web`              | Inherit the accepted Web selection |
| `toolsets.web.enabled: false`    | Disable all four Web tools         |
| Supply a complete `toolsets.web` | Replace the complete Web selection |

Each child Agent uses its own accepted selection. Accepted Runs retain Provider IDs and parameters across recovery, while current availability and credentials are rechecked for every Provider operation.

## Native API

| Operation              | Route under `/api/v1`                                       |
| ---------------------- | ----------------------------------------------------------- |
| List/read types        | `GET /web-provider-types`, `GET /web-provider-types/{type}` |
| Workspace Providers    | `/workspaces/{workspace}/web-providers`                     |
| Organization Providers | `/organizations/{organization}/web-providers`               |
| Read/update            | `GET`, `PATCH .../{provider_id}`                            |
| Test                   | `POST .../{provider_id}/test` with `{}`                     |
| References             | `GET .../{provider_id}/references`                          |

Create with `type` and `name`; enabled defaults to true. All built-ins use an empty configuration. Keyed types require a write-only `credential` with `api_key`; omit `credential` for DuckDuckGo. Installed external types define their own configuration and credential object schemas in the type catalog, including nested objects and explicit nulls. Responses include a strong `ETag`; send it as `If-Match` for updates. Omission preserves a credential; each Provider schema decides which fields are required.

## SDK example

Python, Go, Rust, and TypeScript expose Web Provider management and typed Toolset selections. The backend also exposes a Toolset catalog and side-effect-free candidate validation at `GET /workspaces/{workspace}/toolsets` and `POST /workspaces/{workspace}/toolsets/validate`. The current Console has no general Toolset authoring UI yet.

```python
import os

from a13n import Client, CreateWebProviderRequest
async def create_provider():
    async with Client(os.environ["A13N_URL"], os.environ["A13N_TOKEN"]) as client:
        workspace = await client.workspace()
        saved = await workspace.create_web_provider(
            CreateWebProviderRequest(
                type="exa",
                name="Research Web",
                credential={"api_key": os.environ["EXA_API_KEY"]},
            )
        )
        return saved.value.id, saved.etag
```

API-key clients bind their Workspace once through `/auth/context`. Close clients when finished. Write-only request types redact credentials from ordinary diagnostics.

The handwritten clients do not keep a built-in Provider allowlist. Read the selected type from `web_provider_types`, then pass its exact catalog key and schema-defined credential object. For example, an installed type can use `type="acme_web"` with a nested credential such as `{"oauth": {"client_id": "...", "client_secret": "..."}}`; the entire object remains redacted outside authorized request serialization.

## Failures and retries

Provider adapters use fixed official HTTPS destinations, bounded inputs and responses, and safe errors. Service retries at most once, against the same selected account, only for an explicit rate limit or temporary unavailable response when the deadline permits. It does not retry uncertain transport failures, switch accounts, or fall back to another tool.

Every Provider dispatch and disclosure rechecks current Attempt, Agent, tool, and Provider authority. Fetch and download recheck only their exact built-in tool authority and never depend on a Provider. Redirects are checked before DNS/network I/O and retain address pinning. Timeouts and cancellation can leave vendor charging uncertain; unknown usage remains unknown.

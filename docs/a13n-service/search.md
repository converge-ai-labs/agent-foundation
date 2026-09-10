# Configure Web Search

Give a Service-hosted Agent web search by saving a Search Provider and selecting it in the Agent configuration. The initial providers are Brave Search and Exa. Search uses the saved provider directly and requires a Run Environment. It does not require a Connector or installed plugin.

## Set up in Console

1. Open **Resources → Providers → Search** and select the Workspace or Organization scope. Organization administrators can manage shared providers in the same page.
2. Choose **Add search provider**. Select Brave or Exa, follow the provider's **Get an API key** link, and enter your provider name and API key.
3. Save the provider. The API key cannot be read back; an edit form leaves it blank. To rotate it later, enter a replacement key and save.
4. Optionally choose **Test provider**. This sends one search for `Agent Foundation` and can consume provider quota. A successful test describes that request only.
5. Open your Agent's **Web search** section, select the provider, and save the Agent. You can also create an provider directly from this section; creation selects it in the draft and does not save the Agent.

Saving an provider, testing it, and saving an Agent are separate operations. Closing an unsaved provider form discards its key. After an uncertain create response, Console lists existing providers for reconciliation before another attempt; stored keys cannot be compared.

Workspace providers belong to that Workspace. Organization providers are visible to eligible Workspaces and can be tested there, while organization administrators edit them through the Organization scope in Providers. Viewers can inspect providers; Builders and Admins can manage local providers. Inspect **References** before disabling an provider to see visible current and retained Agent revisions that select it.

## Select an provider

The Agent configuration stores a reference and bounded behavior:

```json
{
  "search": {
    "provider_id": "sprov_example",
    "max_results": 5,
    "include_domains": ["example.com"]
  }
}
```

Merge this field into the Agent's full configuration. `max_results` defaults to 5 and accepts 1–10. `include_domains` defaults to an empty list and accepts up to 20 distinct DNS hostnames. Use hostnames without a URL scheme, path, port, wildcard, or IP address. Matching includes the exact domain and its DNS subdomains. Filters can produce fewer results, including zero; Service does not issue extra requests to fill the requested count.

The model receives a `search(query, num=None)` tool with provider-neutral title, URL, and snippet results. The same Web capability exposes `fetch` for reading pages and `download` for saving files through the bound Environment. Select an Environment for the Run, or configure an Agent default Environment template; a missing Environment fails with `environment_required`. Downloads respect its file permissions. Scrape and model-native search remain disabled.

In a Run's `config_override`, the three forms differ:

| Value                                        | Meaning                                                       |
| -------------------------------------------- | ------------------------------------------------------------- |
| Omit `search`                                | Inherit the accepted selection                                |
| `"search": null`                             | Disable first-party search                                    |
| `"search": {"provider_id": "sprov_example"}` | Replace the selection; omitted search parameters use defaults |

Each subagent uses its own accepted selection. It does not inherit its parent's provider merely because it was delegated work. Accepted Runs retain their selected provider ID and parameters across recovery. Provider availability and credentials remain live: rotating a key affects subsequent credential acquisition without rewriting Agent revisions or accepted Runs.

## Use the Native API

Both ownership scopes expose the same provider operations:

| Operation               | Workspace route (under `/api/v1`)                                            |
| ----------------------- | ---------------------------------------------------------------------------- |
| List types              | `GET /search-provider-types`                                                 |
| Read one type           | `GET /search-provider-types/{type}`                                          |
| Create / list providers | `POST` / `GET /workspaces/{workspace}/search-providers`                      |
| Read / update provider  | `GET` / `PATCH /workspaces/{workspace}/search-providers/{provider_id}`       |
| Test saved provider     | `POST /workspaces/{workspace}/search-providers/{provider_id}/test` with `{}` |
| Inspect references      | `GET /workspaces/{workspace}/search-providers/{provider_id}/references`      |

Workspace and Organization path references accept either an immutable ID or the current URL key. Search Provider references remain immutable provider IDs.

For organization ownership, replace `workspaces/{workspace}` with `organizations/{organization}`. List responses contain `items` and `next_cursor`; pass a returned cursor to read the next page. Provider lists accept exact `type` and `enabled` filters. Reference lists include only Agent revisions the caller can read.

Create with `type`, `name`, and a write-only `credential` string; `configuration` is currently `{}` and `enabled` defaults to `true`. API keys must be nonblank and at most 4,096 UTF-8 bytes. Provider type cannot be changed. Read/create/update responses include an `ETag`. Send the latest strong ETag as `If-Match` when updating; a missing precondition returns 428, and a stale one returns 412. Omitting `credential` preserves the key; sending a replacement rotates it. Null credentials are rejected.

Provider names are unique after normalization within their owning scope. If a create response is lost, reconcile the provider collection before creating again. Neither a saved-provider test nor a credential replacement is safe to repeat automatically after an uncertain response.

## SDK example

The Python, Go, Rust, and TypeScript SDK source projects expose Search Provider operations. Python, Go, and Rust currently implement this surface only; their Agent configuration wrappers type `search` and preserve other Service-owned fields without validating them locally. The TypeScript SDK uses the complete generated Native schema.

```python
import os

from a13n import Client, CreateSearchProviderRequest
from pydantic import SecretStr

async def create_provider():
    async with Client(os.environ["A13N_URL"], os.environ["A13N_TOKEN"]) as client:
        workspace = await client.workspace()
        saved = await workspace.create_search_provider(
            CreateSearchProviderRequest(
                type="brave",
                name="Research search",
                credential=SecretStr(os.environ["SEARCH_API_KEY"]),
            ),
        )
        # Save this reference in the Agent through Console or the Native API.
        return {"provider_id": saved.value.id}, saved.etag
```

API Key clients bind their Workspace once through `/auth/context`: use `await client.workspace()` in Python, `client.workspace().await?` in Rust, `client.Workspace(ctx)` in Go, or `await client.workspaceHttp()` in TypeScript. Bound operations share the parent transport and shutdown; they do not ask for a Workspace argument. Organization-bound browser clients retain explicit scopes.

Python preserves omission with `AgentRunOverride().to_wire()`, disabling with `AgentRunOverride(search=None).to_wire()`, and replacement with `AgentRunOverride(search=SearchSelection(provider_id=provider_id)).to_wire()`. Use `to_wire()` when sending these configuration wrappers.

Go uses `Optional[SearchSelection]{}` for omission, `Null[SearchSelection]()` for disabling, and `Some(selection)` for replacement. Rust uses `Optional::Omitted`, `Optional::Null`, and `Optional::Value(selection)`. TypeScript uses an omitted property, `null`, and an object. Close the client when finished; local close cancels delivery and does not interrupt a server Run.

## Failures and operational behavior

Brave and Exa use fixed official HTTPS destinations. Provider configuration cannot supply an alternative endpoint, proxy, or arbitrary headers. Secrets stay out of Agent configuration, model context, and portable Harness state.

Each search has a 30-second total deadline and bounded response size. Service can retry once for an explicit upstream rate limit or temporary unavailability, using the same provider after a fresh authorization check. It does not automatically retry a transport failure or timeout, switch providers, or fall back to native search. A test provider request never retries automatically.

Provider authentication, quota, rate limit, invalid query, timeout, and response failures return safe `web_search_*` or `web_timeout` codes. Losing execution authority or disabling the selected provider is fatal to the authorized runtime operation. Service checks eligibility before dispatch and again before disclosing results; it cannot retract a query already sent upstream.

Timeouts and cancellation can leave provider charging uncertain. Recovery may repeat an uncheckpointed search. Unknown usage and cost remain unknown, and provider tests do not invent Run usage records.

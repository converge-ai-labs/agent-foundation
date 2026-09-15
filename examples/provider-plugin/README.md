# Deployment Provider plugin example

This independently installable package registers Web, native Pydantic AI Model, and Memory Providers through one selected deployment entry point. It uses the supported `a13n_service.provider_plugins` extension surface and the shared `a13n_harness.memory_plugins.MemoryBackendPlugin` contract. The Memory implementation reuses the native Mem0 OSS adapter, not an example-only storage mirror.

Build and install the package into the same pinned Service image, then select its entry-point name:

```toml
[provider_plugins]
enabled = ["acme"]
```

The distribution name (`a13n-provider-acme-example`), selected entry-point name (`acme`), and registered Provider types (`acme_web`, `acme_model`, `acme.memory`) are intentionally distinct. Its callable uses `@provider_plugin(api_version=1)` to declare the extension contract it was authored against; the literal must not be derived from the installed Service version. Registration creates only immutable metadata and factories. Account validation and operation-scoped runtime construction happen later through the existing Web, Model, and Memory management/runtime paths.

Web runtimes raise `WebProviderResponseError` only after receiving an explicit rate-limit or temporary-unavailable Provider response that can safely authorize Service's bounded retry. Transport loss, timeouts, invalid responses, and unexpected implementation errors must not be converted to that type; their dispatch outcome can be unknown and Service will not replay them.

## Memory: the same plugin in Service and embedded Harness

`AcmeMemoryPlugin` subclasses `Mem0OSSPlugin` only to give the deployment its own key, `acme.memory`. `register()` passes that object directly to `registry.memory.register()`. There is no second Service factory or behavior plugin. An embedded host can put the same object in `MemoryBackendCatalog((AcmeMemoryPlugin(),))` and own its `open(configuration, credential)` context manager. Configuration and credential validation are pure; backend construction and cleanup belong to the operation's host.

After installing and selecting `acme`, create a Workspace Memory Provider through `POST /api/v1/workspaces/{workspace}/memory-providers`:

```json
{
  "type": "acme.memory",
  "name": "Acme memories",
  "configuration": {"base_url": "http://127.0.0.1:18888"},
  "credential": {"api_key": "local-mem0-api-key"},
  "enabled": true
}
```

Use the returned `memprov_...` ID in the Agent revision's `config.memory.provider_id`. The credential is write-only and encrypted by Service; neither it nor a live client enters the Agent snapshot. To connect a different storage target, create another Provider rather than editing its immutable configuration. See the [runnable local workflow](../../dev/mem0/README.md) for startup, Agent configuration, and content operations. The OSS adapter uses only native public APIs, explicit non-inference writes, and bounded listing; installing this package does not configure a remote server.

Run the installed entry-point conformance tests, including inert Memory adapter construction, without vendor credentials:

```bash
uv sync --project examples/provider-plugin --locked
uv run --project examples/provider-plugin --locked pytest examples/provider-plugin/tests
```

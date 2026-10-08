# Model Provider Definitions

## Design Position

`a13n_harness.providers.model` owns reusable Model connection inputs, native Provider construction, calling-API bindings, and bounded vendor connection probes. Its runtime result is a native Pydantic AI Model. Service composes that result with account authority and accepted execution state; an embedding application can use the same definition without Service.

## Definitions

A `ModelProviderDefinition` extends the [shared Provider core](22-provider-subsystem.md#shared-core) with supported native calling APIs, endpoint derivation, reserved headers, and a meaningful native Provider constructor. The constructor receives `ModelConnection` with typed configuration, optional typed credential, endpoint, and extra headers. Inputs contain no hosted resource identity, ORM value, or execution snapshot. The definition can also declare a bounded connection probe. The `supports_connection_probe` projection is derived from the operation itself. Console offers the Provider connection action only when that capability is present; testing a saved Model remains a separate real Model request. Probes refuse redirects even when the caller supplies a redirect-enabled client, cap response size at 4 MiB, and enforce a 10-second total deadline covering endpoint validation and streaming. Unsupported probes fail explicitly; they do not invent a Model selection or require enumeration to create a Model.

`ProviderConfiguration` supplies optional base URL and host-bound session-affinity header configuration. Vendor subclasses add only their actual connection fields. Secret values use secret types, remain absent from representations, and are revealed only at the native SDK or wire boundary. Credentials may contain nested objects and non-string values. They are not serialized JSON hidden in string fields.

A Host composes Model definitions in code and selects them through one [`ProviderCatalog`](22-provider-subsystem.md#catalogs), which rejects a duplicate type. Definition and schema loading is inert; vendor SDKs load when their operation needs them.

## Model Discovery

A definition may declare a `model_discovery` operation; `supports_model_discovery` is derived from its presence, independently of connection probes, OAuth, calling APIs, and public `catalog_providers`. `discover_models()` validates the connection and endpoint and invokes that operation with typed connection inputs, the Host credential source when required, and the Host-owned HTTP client. It does not construct a Model or require an arbitrary upstream model name. Hosts own authorization, credential acquisition, request deadlines, and response limits.

Discovery returns ordered `DiscoveredModel` choices containing the unchanged upstream `model_name` and `display_name`. These are current provider/account observations, not public catalog metadata or saved Model configuration. They carry no inferred price, capabilities, or catalog identity and are not a Model creation whitelist. Unsupported discovery fails explicitly. Discovery neither replaces user-owned Model configuration nor makes existing Models depend on successful enumeration.

The built-in `openai_chatgpt` definition discovers through the official Sign in with ChatGPT plan-usage `GET https://api.openai.com/v1/models` endpoint using the same Host access-token source as inference. It includes only entries with `visibility=list`, preserves server order and original slugs, and verifies the account did not change during discovery. Vendor failures are normalized without exposing upstream bodies. The existing standalone ChatGPT discovery helper remains available to other Hosts.

## Authentication

Model Providers use the shared [`Authentication` declaration](22-provider-subsystem.md#authentication) for static credential presence. A definition may instead declare a `ModelOAuth` runtime-source capability. Such a definition requires an authorized Host token source at construction and never accepts user-supplied access tokens. It may separately declare a server-side OAuth client-authentication credential through the same `Authentication` schema. The default public ChatGPT client forbids that credential and can be saved before authorization; `client_secret_basic` requires a client secret. This does not weaken API-key Providers' presence rules. A local provider may omit setup help. Console applies the declared defaults and conditions without switching on a vendor name: ordinary choices, numbers, and nested fields retain their declared JSON types, while secrets remain write-only.

## Native Construction and Lifetime

`definition.build()` validates configuration, credentials, supported API, configured endpoints, additional vendor endpoints, and the constructed Provider's actual endpoint. An embedding host supplies its endpoint policy and HTTP client. Construction failure cleans up acquired native resources. The caller owns an explicitly supplied client and enters the returned native Model for its operation lifetime.

The fixed native API bindings retain Responses, Chat Completions, Anthropic Messages, Google Generate Content, Bedrock Converse and Mantle, OpenRouter, Ollama, and TypeSafe System One behavior. TypeSafe is a normal Model Provider with API-key credentials, native `TypeSafeModel` construction, and `typesafe.system_one` API binding; it is not a reviewer-specific client. Native Model profiles retain their text-output capability, so being a Model does not imply support for conversational text output. Vendor profiles and HTTP dialects remain native SDK responsibilities. Service checks an agent's native model settings against a schema derived per calling API ([provider type descriptions](../a13n-service/08-providers.md#provider-type-descriptions)); settings are request inputs, not Model construction inputs.

Fireworks AI (`fireworks`) and Together AI (`together`) are built-in API-key Providers using `openai.chat_completions`, their native vendor profiles, and optional base URL overrides. Their model catalog channels are `fireworks-ai` and `togetherai`, respectively. Both declare a bounded bearer-authenticated `/models` connection probe.

Cerebras (`cerebras`), SambaNova (`sambanova`), and Vercel AI Gateway (`vercel`) use native OpenAI-compatible Provider profiles with `openai.chat_completions`. xAI / Grok (`xai`) uses HTTP Chat Completions and Grok profiles without native server-side tools; the existing `xai:` embedding route still selects the native gRPC SDK, while `grok:` selects this HTTP definition. All four accept API keys and optional base URL overrides. Vercel has no connection probe because its model list is public; a saved Model test verifies inference access. The other three expose bounded bearer-authenticated model-list probes.

`build_api_key_model()` supports embedding route names and the additional reviewed native SDK transports used by Harness UI. Shared provider families use these same definitions. Route-selected SDK dialects such as Z.AI remain native. The resulting Model owns its HTTP client and supports context re-entry. A host-selected custom URL can address its local network; Service instead supplies its deployment endpoint policy and checks each actual outbound destination.

[Model Authentication](16a-model-authentication.md) owns OAuth sources and exchange behavior. Harness-specific Codex Thread affinity and Run turn state live under `a13n_harness.models.codex`, above provider construction. The `openai_chatgpt` definition declares only Responses and a fixed public OpenAI endpoint, with Host-acquired tokens and its focused native Responses subclass. Its configuration exposes optional client ID and registered redirect URI plus the provisioned token-endpoint authentication method (`none` or `client_secret_basic`); a confidential client's secret uses a separate credential schema. Native Provider construction and the Model dialect remain separate from this catalog glue, so a Host or upstream SDK can reuse them without Service or Harness UI persistence. Hosts own authorization presentation, pending state, readiness, encryption, and refresh coordination. Service's workspace-shared Provider authorization is defined in its resource contract.

## Invariants

1. Direct and Service construction use the same definition and native implementation.
2. Metadata loading creates no client and imports no Service or unrelated vendor SDK.
3. Authentication presence has one meaning across every Provider domain, server validation, and Console.
4. Host resource authority, encryption, and current credential acquisition remain outside definitions.
5. Model construction preserves supported native API behavior and releases owned resources on failure.

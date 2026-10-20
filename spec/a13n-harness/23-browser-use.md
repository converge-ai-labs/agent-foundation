# Browser Use Capability

## Design Position

`BrowserUseCapability` supplies native browser tools over an embedding Host's explicitly supplied browser backend. Harness owns tool definitions, instructions, managed permission metadata, observation rendering and compact element references. The Host owns browser creation, connectivity, lifetime, user access and human takeover. The Capability neither launches browsers nor implements a remote desktop or a provider platform.

## Observation and Actions

Navigation, observation, screenshots, click, ordinary text fill, supported keys and condition-based text waiting use the existing native FunctionToolset and Harness approval path. Website content is untrusted external data. Mutations have no automatic replay guarantee; read-only observations and bounded output use canonical tool metadata.

Each Run has fresh Capability reference state. An observation returns a bounded page description, backend observation revision and element identities. Harness exposes compact references such as `e1`; those references resolve only through that Run's latest observation. Navigation clears references. The backend validates the revision and actual element identity before dispatch, rejecting stale or detached targets instead of inferring a replacement from an index or label.

The backend rechecks current Host authority for every call. During human control all model-facing browser operations are unavailable, including page observations and screenshots. A browser tool requested during human control waits for explicit handback without collecting page content or polling the model. On release it returns an instruction to observe afresh; the pending operation is not replayed. Other explicit browser failures become tool results without being treated as successful actions. After handback, the Host requires a fresh observation before another mutation and invalidates prior element identities.

A restrictive `RunConfiguration.allowed_hosts` is rejected before browser tools become available. The browser owns opaque subresource, redirect and background traffic and cannot enforce that per-request policy. Hosts use Environment or deployment network isolation for the browser; an unrestricted Run does not itself grant access to a backend.

The Capability borrows its backend and never closes the Host browser when a Run ends. [Harness UI Host Browser](../a13n-harness-ui/11-browser.md) owns the built-in Linux runtime and remote-desktop contract.

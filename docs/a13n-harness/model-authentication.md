---
title: Model authentication
description: Authenticate Harness models with API keys or OAuth subscription flows (Codex, ChatGPT, Grok, GitHub Copilot) and manage client lifetimes.
---

Native providers handle standard API-key authentication. For subscription-backed models, use the OAuth flows in `a13n_harness.providers.model.oauth` and return the authenticated Model from your [Run resolver](models.md#model-selection).

## Subscription OAuth flows

| Subscription   | Flow / helper                                   | Interactive Host guide                                                                                |
| -------------- | ----------------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| Codex          | `CodexRequestModel` + OAuth credential source   | [Models and authentication](../a13n-harness-ui/models-and-authentication.md)                          |
| GitHub Copilot | `GitHubCopilotOAuthFlow` (device authorization) | [Copilot login](../a13n-harness-ui/models-and-authentication.md#github-copilot-subscription)          |
| ChatGPT / Grok | Native OAuth flow + saved grant                 | [Subscription login](../a13n-harness-ui/models-and-authentication.md#subscription-login-and-api-keys) |

## Session affinity and HTTP transport

- Pass the active `thread_id` directly to `CodexRequestModel`; it derives [UUID v5 affinity](models.md#model-request-affinity) automatically for native session headers.
- Keep `httpx.AsyncClient` lifetimes owned by the Host, and configure [outbound HTTP proxies](models.md#outbound-http-proxies) and [TLS verification](models.md#outbound-tls-verification) at the transport layer.

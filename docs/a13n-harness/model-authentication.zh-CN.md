---
title: 模型认证
description: 在 Harness 中使用 API 密钥或 OAuth 订阅流程（Codex、ChatGPT、Grok、GitHub Copilot）进行模型认证并管理客户端生命周期。
---

原生提供方负责标准 API 密钥认证。对于订阅制模型，请使用 `a13n_harness.providers.model.oauth` 中对应的认证流程，并在[运行解析器](models.md#model-selection)中返回已认证的 Model 实例。

## 订阅 OAuth 流程

| 订阅服务       | 流程 / 辅助类                        | 交互式宿主指南                                                                              |
| -------------- | ------------------------------------ | ------------------------------------------------------------------------------------------- |
| Codex          | `CodexRequestModel` + OAuth 凭据源   | [模型与认证](../a13n-harness-ui/models-and-authentication.md)                               |
| GitHub Copilot | `GitHubCopilotOAuthFlow`（设备授权） | [Copilot 登录](../a13n-harness-ui/models-and-authentication.md#github-copilot-subscription) |
| ChatGPT / Grok | 原生 OAuth 流程 + 已保存授权         | [订阅登录](../a13n-harness-ui/models-and-authentication.md#subscription-login-and-api-keys) |

## 会话亲和性与 HTTP 传输

- 直接向 `CodexRequestModel` 传入当前 `thread_id`，它会自动派生 [UUID v5 亲和键](models.md#model-request-affinity)用于原生会话请求头。
- `httpx.AsyncClient` 的生命周期由宿主管理，并在传输层配置[出站 HTTP 代理](models.md#outbound-http-proxies)与 [TLS 验证](models.md#outbound-tls-verification)。

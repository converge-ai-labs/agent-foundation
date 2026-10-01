---
title: 资源基础
description: 工作空间资源的共同规则：生命周期、修订版本、标识符和 provider。
---

资源是租户为 agent 配置的内容，包括 provider 和模型、agent 和 skill、连接、环境模板、记忆、订阅和资产。本页介绍它们的共同规则。各类资源的细节请参阅[模型](models.md)、[工具与连接](tools.md)、[skill](skills.md)、[环境](environments.md)、[记忆](memory.md)以及[文件与 webhook](files-and-webhooks.md)。

## 工作空间

每个资源只属于一个工作空间，且不能迁移。集合位于 `/api/v1` 下，例如 `/api/v1/model-providers` 或 `/api/v1/agents`，作用于请求凭据选定的工作空间（参阅 [HTTP 约定](http.md#workspace)）。资源只能引用同一工作空间中的其他资源，例如模型引用本工作空间中的 provider；工作空间外的资源返回 `404`，如同不存在。创建和修改 provider 与模型需要工作空间中的 `write` 权限。Console 中的 provider 位于 **Workspace settings → Providers**。

## 生命周期

资源遵循两种生命周期之一。

**实时** 资源（provider、模型、连接、环境模板、订阅）直接修改。每次修改增加该记录的 `version`；运行在需要资源时使用当前状态：禁用 provider 或连接会立即阻止新的使用。

**版本化** 资源（agent 和 skill）有主记录和不可变的编号修订版本。每次修改新增一个修订版本；主记录的 `default_revision_id` 决定新任务使用哪个版本，运行则固定使用启动时的精确修订版本。主记录通过归档停用，不直接删除。发布内容与当前默认修订版本相同时，不创建新版本：请求以 `201` 再次返回该修订版本，主记录的版本、ETag 和审计记录均不变，无论是否设置 `make_default: false`。

运行所依赖的资源不会被直接永久删除：

| 类型           | 停用方式                                                             |
| -------------- | -------------------------------------------------------------------- |
| Provider、模型 | 使用 `PATCH` 并传入 `{"enabled": false}`。                           |
| 连接、环境模板 | 使用 `PATCH {"enabled": false}`。连接凭据通过 `POST …/revoke` 移除。 |
| Agent、skill   | 使用 `POST …/archive`；`POST …/unarchive` 可取消归档。               |
| 资产           | `DELETE` 停用资产；历史仍可读取内容。                                |
| 订阅、环境     | `DELETE`。                                                           |

## 通用约定

- **Key 与 ID。** 只有模型使用 `key` 标识：长度 1–128，由小写字母、数字、`-` 和 `.` 组成，并以字母或数字开头（`^[a-z0-9][a-z0-9.-]{0,127}$`）。Key 在工作空间中唯一且永不改变；路径、agent 配置和 ETag（`"{key}:{version}"`）都用它引用模型，模型视图没有 `id`。其他资源，包括 agent、skill、记忆、环境模板和修订版本，都使用带类型前缀的 ID，例如 agent 的 `ap_…` 和 skill 的 `sk_…`。
- **版本。** 读取返回 `ETag`；修改需通过 `If-Match` 提供它。参阅 [HTTP 约定](http.md#concurrency-control)。
- **标签。** Agent 和 skill 最多包含 32 个 `labels`。列表支持 `?label=key:value`（可重复，必须全部匹配）、`q`（名称或描述中不区分大小写的子串）和 `archived=true|false`。
- **作者。** 视图包含 `created_by_id`、`updated_by_id`、`created_at` 和 `updated_at`。
- **审计。** 资源修改会写入[审计记录](identity.md#audit)。没有实际变化的更新不增加版本，也不记录事件。

## Provider

Provider 是外部服务的一个已配置账号，分为五类：

| 类别          | 类型                                                                                                                                                                                                                    | 用途                                            |
| ------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------- |
| `model`       | `openai`, `anthropic`, `google_gemini`, `google_vertex`, `azure_openai`, `aws_bedrock`, `openrouter`, `fireworks`, `together`, `ollama`, `alibaba_model_studio`, `deepseek`, `moonshot`, `minimax`, `zhipu`, `typesafe` | [模型](models.md)                               |
| `web`         | `duckduckgo`, `brave`, `exa`, `parallel`, `tavily`, `firecrawl`, `jina`, `perplexity`, `serpapi`, `tinyfish`                                                                                                            | [Web 工具集](tools.md#web-search-and-scrape)    |
| `environment` | `docker`, `e2b`, `daytona`, `modal`, `vercel`, `sprites`, `runloop`（部署启用开发模式时还包括 `local`）                                                                                                                 | [环境](environments.md)                         |
| `connector`   | `composio`                                                                                                                                                                                                              | [Connector 连接](tools.md#composio-connections) |
| `memory`      | `mem0_platform`, `mem0_oss`                                                                                                                                                                                             | [记录型记忆](memory.md#record-memories)         |

`GET /api/v1/provider-types/{kind}` 描述各已安装类型：`configuration_schema` 和 `credential_schema`（JSON Schema）、是否需要凭据，以及获取凭据的 `setup_url`。模型类型还提供模型 API 和各 API 的设置 schema；web 类型列出支持的操作；环境类型描述环境 schema，以及是否支持托管实例、停止和销毁。Console 使用该端点构建 provider 表单。

在所属类别的集合中创建 provider，例如 `/api/v1/model-providers`，提供类型、名称、配置和凭据：

```sh
curl -X POST "$A13N_URL/api/v1/model-providers" \
  -H "Authorization: Bearer $A13N_API_KEY" -H "Content-Type: application/json" \
  -d '{"type": "openai", "name": "OpenAI", "config": {}, "credential": {"api_key": "sk-..."}}'
```

- **凭据只写不读。** 凭据使用部署的密钥环加密，永不返回；视图只显示 `credential_configured`。`PATCH` 中提供 `credential` 值会替换凭据，`null` 会移除，不提供则保留。
- **凭据绑定其配置。** 存有凭据时，通过 `PATCH` 修改 `config` 必须同时替换或移除凭据（以及模型 provider 存储的全部额外请求头），否则返回 `invalid_argument`。这样可以避免将凭据发送到原先未授权的端点。
- **端点遵循部署的出站策略。** Base URL 和其他端点必须通过[端点策略](configuration.md#outbound-requests)；私有地址和明文 HTTP 端点需要运维人员显式允许。
- **禁用：** 使用 `PATCH {"enabled": false}`。已禁用的 provider 以 `disabled` 拒绝新使用；环境 provider 被禁用后，其已有环境仍会得到维护。
- **测试：** 使用 `POST …/{provider_id}/test`（需要 `run` 权限）。它使用保存的配置发送一次低成本探测，返回 `succeeded`、`failed`（附带消息）或 `unsupported`。Web provider 及 `google_vertex`、`aws_bedrock`、`typesafe`、`local` 类型没有测试。环境 provider 的测试仅做读取：检查 Docker Engine 或 Envd 守护进程身份，不创建资源。记忆 provider 的测试列出一个未被任何记忆使用的命名空间中的一页数据。

Provider 没有删除操作；请将其禁用。

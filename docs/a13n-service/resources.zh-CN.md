---
title: 资源基础
description: 选择合适的资源，携带版本更新，并了解运行会固定哪些内容。
---

创建项目笔记 agent 时，先连接 provider 账号，保存该 provider 下的模型，再在 agent 中选择模型。需要业务系统工具时添加连接，需要处理文件时添加环境模板。这些保存的对象就是**工作空间资源**。本页介绍如何识别、修改和停用它们。

具体创建步骤见[模型](models.md)、[工具与连接](tools.md)、[Skill](skills.md)、[环境](environments.md)、[记忆](memory.md)或[文件与 webhook](files-and-webhooks.md)。

## 工作空间

配置资源前先选定工作空间。每个资源只属于一个工作空间，且不能迁移；它只能引用同一工作空间的资源。例如，模型选择该工作空间中的模型 provider。请求其他工作空间中的资源会返回 `404`。

API 集合位于 `/api/v1` 下，例如 `/api/v1/agents`。凭据如何选择工作空间，见 [HTTP 约定](http.md#workspace)。创建和修改资源需要 `write` 权限；webhook 订阅需要 `admin` 权限。在 Console 中，通过**工作区设置 → 供应商**管理 provider 账号。

## 生命周期

根据修改对象选择操作：

| 修改                                            | 结果                                                                           |
| ----------------------------------------------- | ------------------------------------------------------------------------------ |
| 修改 provider、模型、连接、环境模板、订阅或记忆 | 直接修改实时资源。带版本字段发生变化时，`version` 增加；后续使用读取当前资源。 |
| 修改 agent 或 skill 配置                        | Service 发布不可变的编号修订版本。主记录标识资源，并选择默认修订版本。         |
| 修改 agent 或 skill 的名称、描述或标签          | 只修改主记录；配置修订版本保持不变。                                           |

运行固定使用接受时的修订版本。修改 agent 默认版本会影响后续未固定版本的运行，不改变已接受的运行。实时资源仍按当前状态使用：禁用 provider 或连接会阻止新的使用。发布与当前默认版本相同的配置时，即使设置了 `make_default: false`，也会以 `201` 返回原版本，不修改主记录。

不再需要资源时，使用对应的停用操作：

| 资源           | 操作与结果                                                                      |
| -------------- | ------------------------------------------------------------------------------- |
| Provider、模型 | `PATCH {"enabled": false}` 阻止新的使用。未使用的 provider 还可以删除，见下文。 |
| 连接、环境模板 | `PATCH {"enabled": false}` 阻止新的使用。`POST …/revoke` 移除连接凭据。         |
| Agent、skill   | `POST …/archive` 保留主记录和版本历史；`POST …/unarchive` 恢复可用状态。        |
| 资产           | `DELETE` 停用资产，但保留供历史读取的内容。                                     |
| 订阅、环境     | 使用该类型的 `DELETE` 操作。                                                    |
| 独立记忆       | `DELETE` 移除挂载、文件和历史，或清空记录命名空间。参阅[记忆](memory.md)。      |

## 通用约定

在模型路径和 agent 配置中使用**模型 key**。Key 不可修改，且在工作空间内唯一：长度 1–128，由小写字母、数字、`-` 和 `.` 组成，以字母或数字开头（`^[a-z0-9][a-z0-9.-]{0,127}$`）。模型视图没有 `id`。其他资源使用带类型前缀的 ID，例如 agent 的 `ap_…` 和 skill 的 `sk_…`；修订版本也有各自的 ID。

通过 API 更新时，先读取资源，再将返回的 `ETag` 放入 `If-Match`。以下用已有模型 provider 的重命名练习这个流程：

1. 在装有 `curl` 的终端中，将 `A13N_URL` 设为 Service 源地址，`A13N_API_KEY` 设为所属主体具有 `write` 权限的工作空间密钥。将 `PROVIDER_ID` 设为你准备重命名的模型 provider ID。

2. 读取 provider 及其响应头：

   ```sh
   curl --fail-with-body -sS -i "$A13N_URL/api/v1/model-providers/$PROVIDER_ID" \
     -H "Authorization: Bearer $A13N_API_KEY"
   ```

3. 将 shell 变量 `ETAG` 设为返回的 ETag 值，保留其中的双引号。发送新名称：

   ```sh
   curl --fail-with-body -sS -i -X PATCH "$A13N_URL/api/v1/model-providers/$PROVIDER_ID" \
     -H "Authorization: Bearer $A13N_API_KEY" \
     -H "If-Match: $ETAG" -H "Content-Type: application/json" \
     -d '{"name": "Project models"}'
   ```

4. 响应应为 `200` 并包含新名称。再次读取，确认保存结果；下次编辑使用新的 ETag。旧 ETag 会返回 `412`，应先读取并检查当前资源再重试。省略 `If-Match` 会返回 `428`。

重命名不会改变凭据和配置。没有实际变化的更新不增加版本，也不产生审计事件。参阅[并发控制](http.md#concurrency-control)。

Agent、skill 和环境模板可使用最多 32 个 `labels` 来组织资源。通过 `?label=key:value` 筛选，最多八个条件需全部匹配。Agent 和 skill 列表还接受 `q`，用于不区分大小写的名称或描述搜索，以及 `archived=true|false`。视图包含创建者、修改者 ID 和时间戳；[审计记录](identity.md#audit)记录资源变更。

## Provider

Provider 保存一个配置好的后端账号。按工作需要选择类型：

| 类别          | 用途                                             |
| ------------- | ------------------------------------------------ |
| `model`       | [模型推理](models.md)                            |
| `web`         | [Web 搜索和抓取](tools.md#web-search-and-scrape) |
| `environment` | [执行环境](environments.md)                      |
| `connector`   | [Connector 连接](tools.md#composio-connections)  |
| `memory`      | [记录型记忆](memory.md#record-memories)          |

1. 打开**工作区设置 → 供应商**，选择类别，添加所需类型的 provider。
2. 填写配置和凭据。Console 根据 `GET /api/v1/provider-types/{kind}` 构建表单：响应包含配置和凭据的 JSON Schema、凭据要求和设置链接。模型类型还描述 API 和参数；web 类型列出操作；环境类型描述配方和生命周期支持。
3. 保存 provider，在创建依赖它的模型、连接、模板或记忆时使用其 ID。API 客户端向 `/{kind}-providers` 提交 `type`、`name`、`config` 和该类型要求的凭据。
4. 支持时先运行 provider 测试，再验证实际流程。`POST …/{provider_id}/test` 需要 `run` 权限，返回 `succeeded`、`failed` 或 `unsupported`。非计费探测不能证明 agent 任务一定成功。

`unsupported` 表示该类型没有探测功能，应通过运行验证。Web 类型没有探测；环境类型中仅 Docker 支持测试，只 ping 引擎，不创建资源。模型类型使用其声明的连接探测；记忆 provider 会列出一个未被任何记忆使用的命名空间中的一页数据。

轮换凭据或更换端点时，注意以下更新规则：

- 凭据加密保存，只写不读。视图显示 `credential_configured`，不返回秘密值。`PATCH` 中提交 `credential` 会替换凭据，`null` 会移除，省略则保留。
- 修改 `config`（包括 Base URL）会保留未提交的凭据和模型 provider 额外请求头。空 `extra_headers` 也保留已存值；将指定请求头设为 `null` 才会移除。新配置必须允许保留的凭据和请求头名称。
- 端点必须通过部署的[出站策略](configuration.md#outbound-requests)。明文 HTTP 需要显式允许源地址。该策略允许私有 IP 地址；网络限制应在部署边界实施。

使用 `PATCH {"enabled": false}` 禁用 provider，阻止新的使用；已有环境仍会继续维护。要移除未使用的 provider，打开编辑对话框，选择**删除**并确认。这会永久移除本地配置和凭据，不会删除上游账号或原生资源。资源、保留配置或待完成清理中的引用会以 `provider_in_use` 阻止删除；此时可改为禁用。[环境 provider](environments.md#providers)会在删除前显示模板和环境引用。

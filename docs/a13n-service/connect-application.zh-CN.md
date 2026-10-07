---
title: 连接你的应用
description: 通过 Service SDK 开始对话、读取回答并发送后续消息。
---

你需要 Service URL、工作空间 API 密钥，以及一个已连接可用模型的 agent。部署请从 [Service 快速入门](get-started.md)开始，配置 agent 请参阅 [Console 指南](use-platform.md)。

## 配置凭据

在 **工作区设置 → 我的 API 密钥** 中创建 API 密钥。将密钥和 Service URL 保存在服务端配置中。密钥在所属工作空间中使用其主体的当前权限，主体是密钥所属的用户或服务账号。你需要该工作空间中的 **runner** 或更高角色；创建 agent 需要 **builder** 或 **admin**。团队应用请使用专用[服务账号](identity.md#service-accounts)。

## 从 SDK 开始

选择所用语言的快速入门：[Python](https://github.com/converge-ai-labs/a13n-sdk-python/blob/main/README.md)、[TypeScript](https://github.com/converge-ai-labs/a13n-sdk-typescript/blob/main/README.md)、[Go](https://github.com/converge-ai-labs/a13n-sdk-go/blob/main/README.md) 或 [Rust](https://github.com/converge-ai-labs/a13n-sdk-rust/blob/main/README.md)。各仓库维护安装方式和可运行示例。请确认 SDK 支持的 Service 协议与部署版本匹配，并阅读所安装版本的文档；`main` 可能领先于已发布的软件包。

1. 使用 Service URL 和 API 密钥初始化客户端。
2. 按 ID 选择一个已有 agent。在 Console 中查找 ID，或按 [Console 指南](use-platform.md#create-your-own-agent)创建 agent。
3. 用第一条消息启动 agent，例如“总结这些项目笔记”。为本次提交提供幂等 key；响应丢失后重试同一条消息时，复用该 key。
4. 等待结果并处理状态。完成结果包含回答；等待、失败和取消是不同的结果。

Python 高层流程使用 `start` 和 `result`；具体调用和资源清理请按其[快速入门](https://github.com/converge-ai-labs/a13n-sdk-python/blob/main/README.md)操作。其他 SDK 遵循各自语言的惯例。你不需要单独创建会话，也不需要轮询每次运行。SDK 会跟踪输入何时被消费，包括排队等待的时间。只需要最终结果时，可以不读取流式输出。

## 发送后续消息

保存返回的线程引用，通过 SDK 的高层发送操作向同一线程发送下一条消息。例如，在“总结这些项目笔记”之后追问“我应该先做哪个任务？”。为新消息使用新的幂等 key。已有历史会保留。

关闭客户端或断开连接只会停止本地观察，不会停止 agent 的工作。停止执行需要单独发起明确操作。流式输出、资源清理和恢复请参阅 SDK 的[应用指南](sdks.md#choose-a-client)。

## 处理等待结果

当 agent 提问、需要审批或调用客户端工具时，结果为等待。展示待处理请求，收集明确回答，再通过 SDK 的等待处理流程提交。`/resume` 端点要求完整的响应批次；`/answers` 每次保存一个答复，收齐后恢复执行。不要自动批准或拒绝尚未回答的请求。恢复后，工作会在后继运行中继续。

失败时请检查结构化错误，并按[故障排查指南](monitoring.md#troubleshoot-a-request-or-run)处理。需要了解底层协议时，请参阅[等待、审批和问题](agents-and-runs.md#waits-approvals-and-questions)。

## 直接通过 HTTP 集成

实现协议客户端或排查请求时，可以使用此流程。普通应用集成和脚本操作请优先使用 [SDK 和远程 CLI](sdks.md)。

导出相同的 Service URL 和工作空间 API 密钥：

```sh
export A13N_URL=http://127.0.0.1:8080 A13N_API_KEY=a13n_...
```

### 选择 agent

在 Console 中找到 agent ID，或列出当前密钥可用的 agent：

```sh
curl "$A13N_URL/api/v1/agents" -H "Authorization: Bearer $A13N_API_KEY"
```

要创建 agent，在 **模型** 或 `GET /api/v1/models` 中查找模型 key，然后替换下面的 `your-model-key`：

```sh
MODEL_KEY=your-model-key
curl -X POST "$A13N_URL/api/v1/agents" \
  -H "Authorization: Bearer $A13N_API_KEY" -H "Content-Type: application/json" \
  -d "{\"name\": \"Helper\", \"config\": {\"model\": \"$MODEL_KEY\", \"instructions\": \"Answer briefly.\"}}"
```

### 开始对话

将 `AGENT_ID` 设为选定 agent 的 `id`，并为这条消息生成一个幂等 key：

```sh
AGENT_ID=ap_replace_with_the_returned_id
MESSAGE_KEY=$(uuidgen)
```

提交第一条消息。如果响应丢失后需要重试，使用相同的 `MESSAGE_KEY` 重复该请求。不同消息应使用新 key。

```sh
curl -X POST "$A13N_URL/api/v1/threads" \
  -H "Authorization: Bearer $A13N_API_KEY" -H "Content-Type: application/json" \
  -H "Idempotency-Key: $MESSAGE_KEY" \
  -d "{\"agent_id\": \"$AGENT_ID\", \"payload\": {\"content\": [{\"type\": \"text\", \"text\": \"What is a13n?\"}]}}"
```

### 读取结果

响应包含 `thread`、`entry`，也可能包含 `run`。如果 `run` 为 null，请通过[线程事件流](agents-and-runs.md#follow-a-thread-stream)观察消息接收情况。否则，将 `RUN_ID` 设为返回运行的 `id` 并读取状态：

```sh
RUN_ID=run_replace_with_the_returned_id
curl "$A13N_URL/api/v1/runs/$RUN_ID" -H "Authorization: Bearer $A13N_API_KEY"
```

重复此请求，直到运行状态为 `completed`、`waiting`、`failed` 或 `cancelled`。完成运行的回答位于 `output`。等待中的运行需要[回答、审批或客户端工具结果](agents-and-runs.md#waits-approvals-and-questions)；失败运行的结果请按[故障排查指南](monitoring.md#troubleshoot-a-request-or-run)检查。

也可以订阅线程事件流或 [webhook](files-and-webhooks.md#webhooks)，无需轮询。客户端断开连接后，运行会继续。向 `POST …/threads/{thread_id}/inbox` 发送后续消息来继续该线程。

认证、分页、条件写入和错误处理请参阅 [HTTP 约定](http.md)；请求和响应 schema 请参阅 [API 参考](api-reference/index.md)。请核对 [SDK 支持的协议约定](sdks.md#keep-version-ownership-clear)是否匹配部署的 Service 版本。

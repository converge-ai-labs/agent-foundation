---
title: 连接你的应用
description: 使用工作空间 API 密钥，从应用调用运行中的 Service 上的 agent。
---

你需要 Service URL、工作空间 API 密钥，以及一个已连接可用模型的 agent。部署请从 [Service 快速入门](get-started.md)开始，配置 agent 请参阅 [Console 指南](use-platform.md)。

应用集成请使用 [Service SDK 或远程 CLI](sdks.md)。本指南通过 curl 展示等价的 HTTP 流程；客户端专属配置和流式辅助功能由各 SDK 仓库提供。

## 配置凭据

在 **工作区设置 → 我的 API 密钥** 中创建 API 密钥，并将它与 Service URL 一起导出：

```sh
export A13N_URL=http://127.0.0.1:8080 A13N_API_KEY=a13n_...
```

密钥在所属工作空间中使用其主体的当前权限，主体是密钥所属的用户或服务账号。你需要该工作空间中的 **runner** 或更高角色；创建 agent 需要 **builder** 或 **admin** 。团队应用请使用专用[服务账号](identity.md#service-accounts)。API 密钥应保存在服务端配置中。

## 选择 agent

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

## 开始对话

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

## 读取结果

响应包含 `thread`、`entry`，也可能包含 `run`。如果 `run` 为 null，请通过[线程事件流](agents-and-runs.md#follow-a-thread-stream)观察消息接收情况。否则，将 `RUN_ID` 设为返回运行的 `id` 并读取状态：

```sh
RUN_ID=run_replace_with_the_returned_id
curl "$A13N_URL/api/v1/runs/$RUN_ID" -H "Authorization: Bearer $A13N_API_KEY"
```

重复此请求，直到运行状态为 `completed`、`waiting`、`failed` 或 `cancelled`。完成运行的回答位于 `output`。等待中的运行需要[回答、审批或客户端工具结果](agents-and-runs.md#waits-approvals-and-questions)；失败运行的结果请按[故障排查指南](monitoring.md#troubleshoot-a-request-or-run)检查。

也可以订阅线程事件流或 [webhook](files-and-webhooks.md#webhooks)，无需轮询。客户端断开连接后，运行会继续。向 `POST …/threads/{thread_id}/inbox` 发送后续消息来继续该线程。

认证、分页、条件写入和错误处理请参阅 [HTTP 约定](http.md)；请求和响应 schema 请参阅 [API 参考](api-reference/index.md)。请核对 [SDK 支持的协议约定](sdks.md#keep-version-ownership-clear)是否匹配部署的 Service 版本。

---
title: 连接你的应用
description: 连接服务端客户端、提交消息、读取结果，并继续对话。
---

将应用后端连接到一个已在 Console 中测试通过的 agent。你需要 Service URL、agent ID 和工作空间 API 密钥。如果还没有部署，先按[快速入门](get-started.md)启动 Service，再按 [Console 指南](use-platform.md)配置并测试 agent。

## 配置凭据

1. 在 Console 中打开 agent 菜单，选择 **接入应用**。
2. 将 **服务地址** 和 **Agent ID** 复制到服务端配置中。
3. 使用已保存的个人 API 密钥，或选择 **创建密钥**。新密钥的秘密值只显示一次，请在关闭窗口前复制。
4. 将密钥保存为服务端环境变量 `A13N_API_KEY`。需要交给 AI 编程助手的接入指引时，选择 **复制给 agent**。

侧边栏的 **API 密钥** 页面用于管理个人密钥。密钥在所属工作空间中使用其主体的当前权限，主体是密钥所属的用户或服务账号。运行已有 agent 需要 **runner** 或更高角色；创建 agent 需要 **builder** 或 **admin**。团队应用请使用专用[服务账号](identity.md#service-accounts)。API 密钥保存在服务端，不要放入浏览器代码。

## 从 SDK 开始

选择所用语言的快速入门：[Python](https://github.com/converge-ai-labs/a13n-sdk-python/blob/main/README.md)、[TypeScript](https://github.com/converge-ai-labs/a13n-sdk-typescript/blob/main/README.md)、[Go](https://github.com/converge-ai-labs/a13n-sdk-go/blob/main/README.md) 或 [Rust](https://github.com/converge-ai-labs/a13n-sdk-rust/blob/main/README.md)。各仓库维护安装方式、方法签名和可运行示例。请阅读所安装版本的文档，并确认其支持的 Service 协议与部署版本匹配；`main` 可能领先于已发布的软件包。

按以下顺序实现首次交互：

1. 使用 Service URL 和 API 密钥初始化客户端。
2. 使用 agent ID 和消息开始对话，例如“总结这些项目笔记”。为本次提交提供幂等 key。如果响应丢失，使用相同 key 重试同一次提交。
3. 保存返回的对话和提交引用。应用需要将结果关联到这条消息，并跟踪消息排队期间的状态。
4. 读取结果。完成后展示 `output`，等待时收集响应，执行失败时展示结构化错误。

使用 SDK 的应用工作流跟踪提交过程。[下方 HTTP 示例](#direct-http-integration)展示了底层回执和读取步骤。新对话会自动创建所属会话；只需要最终结果时，可以不读取流式输出。

## 发送后续消息

将下一条消息发送到同一线程，以继续使用已有历史。例如，在“总结这些项目笔记”之后追问“我应该先做哪个任务？”。保留线程引用，为新提交使用新的幂等 key，并单独跟踪它的结果。

关闭客户端只会停止本地观察，Service 会继续工作。需要停止执行时，使用明确的取消操作。流式输出、资源清理和恢复的具体调用由 [SDK 应用指南](sdks.md#choose-a-client)说明。

## 处理等待结果

等待中的运行需要先完成相应操作才能继续。对于问题、审批或客户端工具调用：

1. 读取运行的 `pending` 请求，将每个问题或决策展示给负责的用户，或执行应用的客户端工具。
2. 在应用中收集并保存响应。每个响应都关联到对应的 `tool_call_id` 和类别：`approvals` 或 `calls`。
3. 使用新的幂等 key，将完整批次提交到 `POST /api/v1/runs/{run_id}/resume`。两个类别映射都必须提供，即使其中一个为空。每个待处理请求都需要明确响应；未回答的请求不能自动视为批准或拒绝。
4. 跟踪返回的后继运行。原运行继续保留等待状态，记录这次暂停。

发送另一条普通消息不会回答待处理请求，新消息会留在队列中。响应结构和桌面控制等待的处理请参阅[等待、审批和问题](agents-and-runs.md#waits-approvals-and-questions)。运行失败时，检查 `failure` 并按[故障排查指南](monitoring.md#troubleshoot-a-request-or-run)处理。

## 直接通过 HTTP 集成

可以先用以下步骤了解协议，再实现客户端。在同一个 shell 中依次执行命令，需要安装 `curl`、`jq` 和 `uuidgen`。将 URL 和 API 密钥替换为部署的实际值：

```sh
export A13N_URL=http://127.0.0.1:8080
export A13N_API_KEY=a13n_replace_with_your_key
```

### 选择 agent

使用 Console 中的 ID，或列出当前密钥可用的 agent：

```sh
curl --fail-with-body -sS "$A13N_URL/api/v1/agents" \
  -H "Authorization: Bearer $A13N_API_KEY"
```

需要创建 agent 时，在 **模型** 或 `GET /api/v1/models` 中找到一个已启用模型的 key。替换 `your-model-key` 后发送：

```sh
MODEL_KEY=your-model-key
jq -n --arg model "$MODEL_KEY" \
  '{name: "Helper", config: {model: $model, instructions: "Answer briefly."}}' > agent.json
curl --fail-with-body -sS -X POST "$A13N_URL/api/v1/agents" \
  -H "Authorization: Bearer $A13N_API_KEY" -H "Content-Type: application/json" \
  --data-binary @agent.json
```

### 开始对话

将 `AGENT_ID` 设为选定 agent 的 `id`。准备一条消息及其幂等 key：

```sh
AGENT_ID=ap_replace_with_the_returned_id
MESSAGE_KEY=$(uuidgen)
jq -n --arg agent "$AGENT_ID" \
  '{agent_id: $agent, payload: {content: [{type: "text", text: "What is a13n?"}]}}' > message.json
```

提交消息并保存回执。如果响应丢失，使用相同请求体和 `MESSAGE_KEY` 重复此请求；此次重试不要生成新 key。

```sh
curl --fail-with-body -sS -X POST "$A13N_URL/api/v1/threads" \
  -H "Authorization: Bearer $A13N_API_KEY" -H "Content-Type: application/json" \
  -H "Idempotency-Key: $MESSAGE_KEY" \
  --data-binary @message.json > submitted.json
jq '{thread_id: .thread.id, session_id: .thread.session_id, entry_id: .entry.id, run_id: .run.id}' submitted.json
```

回执包含 `thread` 和 `entry`；条目已经分配给运行时，还会包含 `run`。`run: null` 表示消息已保存，但尚未分配运行。保存 ID，供后续读取使用：

```sh
THREAD_ID=$(jq -r '.thread.id' submitted.json)
SESSION_ID=$(jq -r '.thread.session_id' submitted.json)
ENTRY_ID=$(jq -r '.entry.id' submitted.json)
```

### 读取结果

读取这条消息的条目，原始回执中 `run: null` 时也使用此步骤：

```sh
curl --fail-with-body -sS "$A13N_URL/api/v1/threads/$THREAD_ID/inbox/$ENTRY_ID" \
  -H "Authorization: Bearer $A13N_API_KEY" > entry.json
jq '{status, assigned_run_id, failure}' entry.json
```

| 条目状态    | 下一步                                                                |
| ----------- | --------------------------------------------------------------------- |
| `pending`   | 稍后重新读取。线程处于等待状态时，先回答待处理请求。                  |
| `assigned`  | 读取 `assigned_run_id` 对应的运行以观察执行；运行结束后重新读取条目。 |
| `consumed`  | 运行已将此消息纳入保存的状态。读取该运行的结果。                      |
| `failed`    | 检查条目的 `failure`；消息未完成纳入过程。                            |
| `withdrawn` | 停止等待此条目的回答。                                                |

`assigned_run_id` 有值后，读取对应运行：

```sh
RUN_ID=$(jq -r '.assigned_run_id' entry.json)
curl --fail-with-body -sS "$A13N_URL/api/v1/runs/$RUN_ID" \
  -H "Authorization: Bearer $A13N_API_KEY" > run.json
jq '{status, output, pending, failure}' run.json
```

运行状态为 `accepted` 或 `running` 时，重复读取运行。`completed` 运行的回答位于 `output`；`waiting` 需要按上方流程提交响应；`failed` 和 `cancelled` 表示执行结束，但没有完成的回答。

将完成运行作为这条消息的结果展示前，重新读取条目，确认它已被该运行消费，状态为 `consumed`。仅分配给运行还不够：运行可能未纳入某条已分配消息便结束，使该条目回到 `pending`。此时继续跟踪条目，不要将线程的最新运行直接当作它的回答。

收到第一个回答后，向同一线程发送追问：

```sh
MESSAGE_KEY=$(uuidgen)
jq -n --arg agent "$AGENT_ID" \
  '{agent_id: $agent, payload: {content: [{type: "text", text: "Which component should I start with?"}]}}' > message.json
curl --fail-with-body -sS -X POST "$A13N_URL/api/v1/threads/$THREAD_ID/inbox" \
  -H "Authorization: Bearer $A13N_API_KEY" -H "Content-Type: application/json" \
  -H "Idempotency-Key: $MESSAGE_KEY" \
  --data-binary @message.json > submitted.json
ENTRY_ID=$(jq -r '.entry.id' submitted.json)
```

对新的 `ENTRY_ID` 重复条目和运行读取步骤。需要实时更新时，使用 `SESSION_ID` 跟踪[会话事件流](agents-and-runs.md#follow-a-session-stream)，或订阅 [webhook](files-and-webhooks.md#webhooks)。保留提交 ID，重连后即可继续观察同一项工作。

分页、条件写入和错误处理请参阅 [HTTP 约定](http.md)；完整请求和响应 schema 请参阅 [API 参考](api-reference/index.md)。

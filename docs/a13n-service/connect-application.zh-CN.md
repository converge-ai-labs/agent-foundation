---
title: 接入业务应用
description: 使用工作区 API 密钥、官方多语言 SDK 或带幂等键与审批恢复机制的 HTTP API 将托管 Service 智能体集成到后端服务。
---

准备 **Service URL**、**Agent ID** 与工作区 **API 密钥**（需具备 `runner` 及以上权限；生产应用建议使用独立[服务账号](identity.md#service-accounts)，并将密钥保留在服务端），即可将后端服务接入已在 Console 验证通过的智能体。

## 配置凭据

在 Console 打开目标智能体菜单并点击 **Connect to app**，复制 **Service URL** 与 **Agent ID**，创建或选用工作区 API 密钥，并将其保存为服务端环境变量 `A13N_API_KEY`。

## 使用官方 SDK 快速接入

使用 Python 3.13 运行（`uv run --python 3.13 --with 'a13n==0.0.98' call_agent.py`；TypeScript、Go 与 Rust 示例见 [SDK 指南](sdks.md#choose-a-client)）：

```python title="call_agent.py"
import asyncio
import os
from a13n import Client


async def main() -> None:
    async with Client(os.environ["A13N_URL"], os.environ["A13N_API_KEY"]) as client:
        agent = client.agents(os.environ["AGENT_ID"])
        message = await agent.start("Name two colors.")
        result = await message.result()
        print(result.output if result.status == "completed" else result.status)


asyncio.run(main())
```

保留 `message.thread.id` 以便在同一线程继续追问或在 Console（**Sessions**）中排查。

## 处理等待中的结果

当 `result.status == "waiting"` 时，读取 `pending.approvals` 与 `pending.calls`，在应用内收集用户审批/回答或执行客户端工具，随后携带全新 `Idempotency-Key` 向 `POST /api/v1/runs/{run_id}/resume` 同时提交两个字典并跟进返回的继任运行（详见[等待、审批与提问](agents-and-runs.md#waits-approvals-and-questions)）。

## 直接通过 HTTP 集成

携带唯一的 `Idempotency-Key` 提交消息（网络丢包重试时复用同一键与请求体），随后轮询收件箱条目及其分配的运行：

```sh
export A13N_URL=http://127.0.0.1:8080
export A13N_API_KEY=a13n_replace_with_your_key
AGENT_ID=ap_replace_with_your_agent_id
MESSAGE_KEY=$(uuidgen)

curl --fail-with-body -sS -X POST "$A13N_URL/api/v1/threads" \
  -H "Authorization: Bearer $A13N_API_KEY" -H "Content-Type: application/json" \
  -H "Idempotency-Key: $MESSAGE_KEY" \
  -d "{\"agent_id\":\"$AGENT_ID\",\"payload\":{\"content\":[{\"type\":\"text\",\"text\":\"Name two colors.\"}]}}" \
  > submitted.json

THREAD_ID=$(jq -r '.thread.id' submitted.json)
SESSION_ID=$(jq -r '.thread.session_id' submitted.json)
ENTRY_ID=$(jq -r '.entry.id' submitted.json)

curl --fail-with-body -sS "$A13N_URL/api/v1/threads/$THREAD_ID/inbox/$ENTRY_ID" \
  -H "Authorization: Bearer $A13N_API_KEY" > entry.json
RUN_ID=$(jq -r '.assigned_run_id' entry.json)

curl --fail-with-body -sS "$A13N_URL/api/v1/runs/$RUN_ID" \
  -H "Authorization: Bearer $A13N_API_KEY" | jq '{status, output, pending, failure}'
```

确认该收件箱条目已被 `RUN_ID` 标记为 `consumed`；后续追问使用新 `Idempotency-Key` 发送至 `POST /api/v1/threads/$THREAD_ID/inbox`。如需实时流式输出而非轮询，可订阅[会话 SSE 流](stream-and-read-results.md#follow-a-session-stream)（`$SESSION_ID`）或 [Webhook](webhooks.md)。

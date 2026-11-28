---
title: 实时会话 API（Alpha）
description: 通过 WebRTC 或 WebSocket PCM，在 Service 中接入 OpenAI、Azure OpenAI、Gemini Live 或 xAI 的原生语音会话。
---

Service 实时会话让客户端连接到使用原生实时模型的 Agent。语音和文本共用同一段会话，并可使用 Agent 的工具和对话历史。接口处于 **Alpha** 阶段；客户端应兼容新增字段和事件类型。

## 开始会话

为 Agent 选择使用实时调用 API 的[模型](models.md)作为主模型。在应用后端使用具备 `run` 权限的[工作区 API 密钥](connect-application.md)，先查询能力，再申请一次性票据：

```bash
curl -fsS "$A13N_URL/api/v1/realtime?agent_id=$AGENT_ID" \
  -H "Authorization: Bearer $A13N_API_KEY"

curl -fsS "$A13N_URL/api/v1/realtime/sessions" \
  -H "Authorization: Bearer $A13N_API_KEY" -H 'Content-Type: application/json' \
  -d "{\"agent_id\":\"$AGENT_ID\",\"transport\":\"websocket\",\"draft\":\"Say hello.\"}"
```

申请前检查 `can_start` 和 `transports`。查询结果也会返回 PCM 采样率、配置的会话时长上限，以及模型是否支持手动打断。当前 Alpha 输入协议尚未接入图片附件，`accepts_images` 为 `false`，`supported_image_types` 为空。

后端只把票据响应交给音频客户端。客户端应在 `expires_in`（30 秒）内连接 Service 同源的 `websocket_path`，按顺序将协议名和票据作为 WebSocket 子协议传入：

```js
const endpoint = new URL(ticket.websocket_path, serviceOrigin);
endpoint.protocol = endpoint.protocol === "https:" ? "wss:" : "ws:";
const socket = new WebSocket(endpoint, [ticket.subprotocol, ticket.ticket]);
socket.binaryType = "arraybuffer";
socket.onmessage = ({ data }) => {
  if (typeof data !== "string") return; // 按 audio_output_hz 播放 PCM。
  const event = JSON.parse(data);
  if (event.type === "ready") {
    socket.send(JSON.stringify({ type: "text", text: "你能做什么？" }));
  }
};
```

`accepted` 返回新 Run 的标识。收到 `ready` 后再发送文本或音频。发送 `{"type":"stop"}` 挂断；`sealed` 确认 Run 最终状态。若未收到 `sealed`，请先查询 Run 状态，再决定是否在同一 Thread 中发起新的会话。

## 选择提供方和传输方式

| 模型 API          | 提供方                | 语音传输方式          | PCM 输入 / 输出 | Service 验证方式       |
| ----------------- | --------------------- | --------------------- | --------------- | ---------------------- |
| `openai.realtime` | OpenAI Realtime       | WebRTC、WebSocket PCM | 24 / 24 kHz     | 上游适配器与确定性测试 |
| `openai.live`     | OpenAI GPT-Live       | WebRTC、WebSocket PCM | 24 / 24 kHz     | 上游适配器与确定性测试 |
| `azure.realtime`  | Azure OpenAI Realtime | WebRTC、WebSocket PCM | 24 / 24 kHz     | 上游适配器与确定性测试 |
| `google.realtime` | Google Gemini Live    | WebSocket PCM         | 16 / 24 kHz     | 上游适配器与确定性测试 |
| `xai.realtime`    | xAI Grok Voice        | WebSocket PCM         | 24 / 24 kHz     | 上游适配器与确定性测试 |

表中的验证使用模拟传输和适配器，未发起真实的提供方调用。部署前请用自己的凭据验证计划使用的模型和传输方式。

- **WebSocket PCM：** 使用二进制帧收发单声道、16 位有符号小端 PCM。按 `ready.audio_input_hz` 对采集音频重采样，按 `ready.audio_output_hz` 播放。兼容旧客户端的 `ready.audio_hz` 始终表示输出采样率。根据 `ready.supports_interruption` 决定是否提供手动打断。
- **WebRTC：** 创建仅含音频的 `RTCPeerConnection`。票据中的 `ice_servers` 可提供 STUN 或 TURN 配置，构造浏览器配置时省略空的用户名和凭据。以 `{"type":"offer","sdp":"…"}` 发送本地 SDP，再应用 `answer` 返回的远端 SDP。收到 `ready` 且媒体连接就绪后再开始音频收发。WebRTC 的媒体格式由浏览器协商，无需按 Service 返回的 PCM 采样率重采样。

提供方凭据保留在 Service。请使用官方端点：`api.openai.com`、`api.x.ai`、`generativelanguage.googleapis.com`，或 `*.openai.azure.com` 下的 Azure OpenAI `resource_endpoint`。在上游实时适配器支持控制 WebSocket 重定向前，实时连接不开放自定义网关、附加请求头或会话亲和请求头。

## 处理限额和失败

票据中的 `max_duration_seconds` 是实际生效的时长上限。省略请求字段可使用服务器上限，默认为 30 分钟；也可以在 30 秒和服务器上限之间指定时长。上限超过 60 秒时，Service 会在到期前一分钟发送一次 `limit_warning`。会话默认在 5 分钟内无对话活动时结束；语音、文本、工具执行和审批决定均算作活动。前台工具执行或等待审批期间暂停空闲检查。

收到 `ending` 后停止采集和播放，等待 Service 保存 Run。按 `sealed.reason`（如有）或错误帧中的 `error.code` 处理结果：

| 错误码或原因                                   | 客户端处理方式                                    |
| ---------------------------------------------- | ------------------------------------------------- |
| `duration_limit`、`idle_timeout`               | 会话正常结束。如用户希望继续，可以发起新会话。    |
| `usage_limit_exceeded`、`authority_revoked`    | 等待预算或访问权限调整后再试。                    |
| `invalid_argument`                             | 修正请求或帧后再试。                              |
| `realtime_transport_failed`、`worker_draining` | 检查 Run 和 Thread，退避后申请新票据。            |
| `realtime_unavailable`                         | 重新查询可用性，并检查 Service 或提供方是否就绪。 |

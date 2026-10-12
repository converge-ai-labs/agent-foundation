---
title: Live 模式
description: 通过连续语音对话与 agent 协作。
---

Live 模式为 WebUI 提供连续语音对话，并保留任务与决策记录。启动一次即可自然交谈，切换工作区页面时麦克风保持开启。停止语音不会停止已经运行的工作。

## 启用 Live

1. 添加使用 **Codex subscription** 认证的 Model，并登录所选账户。参阅[模型与认证](models-and-authentication.md)。
2. 打开 **Settings → General → Live**，启用 Live 并选择该 Model。
3. 选择音色，以及是否允许 Live 执行器使用文件工具，然后保存设置。
4. 在工作区顶部或 **Live** 页面点击 **Start voice**，并在浏览器询问时允许访问麦克风。

请使用支持麦克风采集和 WebRTC 的浏览器，通过 HTTPS 或 localhost 访问。所选账户和网络必须支持 Codex Live。普通 Model 请求成功并不代表语音功能可用。

初始设置和添加 Codex Model 时会提供默认勾选的 Live 选项。取消勾选即可保持未配置状态。已有 Live 设置会保留，升级不会自动启用 Live。

等效的根配置如下：

```yaml title="a13n-harness-ui.yaml"
live:
  enabled: true
  model: model-codex
  voice: cove
  files_enabled: true
  instructions: "Keep spoken replies concise."
```

Live 使用 Codex V3 音色，默认为 `cove`。V2 音色（例如 `marin`、`cedar`）不兼容；已有 Live 配置若使用这些音色，请将其替换。

使用已有的 Codex subscription Model ID。Live 不需要额外的 Model 或 Agent YAML 文件。语音模型使用运行时预设，所选 Model 负责执行委派任务。

## 交谈与工作

打开 Live 页面时，麦克风保持关闭。点击 **Start voice** 开始连续对话。工作区顶部的按钮可以启动同一语音对话，无需离开当前页面。在 Live 页面点击 **Stop voice**，或在顶部点击 **Stop**，即可释放麦克风和连接。

通过 **Open tasks & decisions** 查看持久化的 Live Thread。任务由普通 Harness 执行器运行，决策和审批在该 Thread 中处理。Live 可以协调独立根 Thread 和 Coordinator；受管 worker 保留原有的归属规则。

内置执行器具有笔记、任务、Thread 协作、已启用的全局记忆，以及可选的文件工具。工作区位于所选配置文件旁的 `live-workspace`。它不继承默认 Agent 的 shell、MCP 服务器、插件、Skills 或子 agent。专业工作应委派给已配置的 Thread。

页面导航会保留同一语音连接。工作区中持续显示简洁的 **Microphone on** 指示和 **Stop** 按钮。点击指示可打开完整 Live 页面，查看 **Connected** 状态，以及标注 **You** 或 **Assistant** 的最近字幕；停止后隐藏未完成的字幕。整页刷新或关闭标签页会结束语音，再次进入不会自动开启麦克风。同一 App 同时只允许一个浏览器持有语音连接。

## 结果与回报

委派任务会启动或引导 Live Thread。通话有效时，已选定的最终结果会返回语音。停止语音后任务继续运行，保存的结果仍可在 Thread 中查看。

其他独立根 Thread 和 Coordinator 可以调用 `send_thread_message(thread_id="live", message="...", kind="report")`，直接向当前语音对话回报。需要执行器继续处理时使用 `kind="task"`。离线时返回 `skipped`，原因为 `live_offline`，不会创建新 Run，也不会排队等待以后播放。

语音转录与任务历史分别保存。提供方接受文本不代表用户已经听到。任务保存的结果以来源 Thread 为准。

## 隐私与连接错误

音频在浏览器与 OpenAI 之间直接传输。订阅凭据保留在 Host。Host 处理信令、字幕、委派和选定的结果文本，不转发麦克风音频。

连接失败时，请查看错误，修正麦克风、网络或账户访问问题后重新启动。提供方 HTTP 错误会显示状态，不泄露响应正文或凭据。Live 不会静默重试任务、替换通话、更换账户或回退到 API key。添加更多工作前先处理待决事项；修改 Live 设置后需要重新启动语音。

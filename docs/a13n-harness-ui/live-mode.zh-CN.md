---
title: Live 模式
description: 启用语音、完成首次通话，并在 Live Thread 中跟进委派工作。
---

Live 模式让你在切换 WebUI 页面时通过语音与 Harness UI 交谈。语音和任务执行的生命周期独立：停止麦克风后，已启动任务继续运行。任务结果与决策保留在持久化的 Live Thread 中。

## 启用 Live

开始前，先配置并登录一个 [Codex subscription Model](models-and-authentication.md)。使用支持麦克风采集和 WebRTC 的浏览器，通过 HTTPS 或 localhost 打开 WebUI。语音还要求账户和网络能够访问 Codex Live。

1. 打开 **Settings → General → Live**。
2. 启用 Live，选择已有的 Codex subscription Model。
3. 首次测试保留默认音色 `cove`。选择是否允许 Live 执行器使用文件工具，然后保存。
4. 在工作区顶部找到 **Start voice**，或从侧栏打开 **Live**。保存设置会显示这些控件，不会启动麦克风。
5. 点击 **Start voice**，允许麦克风访问，等待 **Connected**。
6. 说一个简短请求，例如“告诉我你能协助协调哪些任务”。检查 **You**、**Assistant** 字幕及语音回复。
7. 点击顶部的 **Stop** 或 Live 页面的 **Stop voice**，确认麦克风指示消失。

使用文件配置时，运行 `a13n-harness-ui config path` 找到 `configuration_path`。将以下片段合并到该根 YAML，并把 `model-codex` 替换为已保存的 Model ID：

```yaml title="a13n-harness-ui.yaml — merge into the root"
live:
  enabled: true
  model: model-codex
  voice: cove
  files_enabled: true
  instructions: "Keep spoken replies concise."
```

针对同一配置根运行 `a13n-harness-ui config validate`，再按第 4–7 步测试。配置有效或普通 Model 请求成功不代表语音连接可用；首次通话会检查这条路径。修改 Live 设置后，重新启动语音。

所选 Model 提供身份验证并执行委派任务。语音使用运行时预设，因此无须创建单独的 Live Model 或 Agent 文件。Live 使用 Codex V3 音色；启动通话前替换旧配置中的 `marin` 或 `cedar`。

WebUI 首次设置为 Codex 提供默认勾选的 Live 选项，不需要时可取消。之后添加 Model 或升级都不会改变 Live 设置。已有的显式配置会保留，包括关闭状态。

## 交谈与工作

语音测试成功后，再尝试一个小型委派任务：

1. 启动语音，让 Live 记录一个任务，或与已配置的 Thread 协调工作。
2. 点击 **Open tasks & decisions**，查看 Live Thread 中执行器的工作，并回答待处理决策。
3. 返回其他 WebUI 页面。同一通话继续时，**Microphone on** 指示保持可见。
4. 执行完成后打开保存的任务结果。如果先停止语音，仍可回到 Live Thread 跟进工作；如需同时停止任务，使用该 Thread 的普通取消控件。

内置 Live 执行器具有笔记、任务、Thread 协作、已启用的全局记忆和可选文件工具。其工作区是所选配置文件旁的 `live-workspace`。它使用独立的受限工具集，不继承默认 Agent 的 shell、MCP、插件、Skills、CodeAct 或子 agent。专业工作应委派给已配置的 Thread。受管 worker 保留原有 Coordinator 归属。

打开 Live 页面时，麦克风保持关闭。WebUI 内部导航保留活动通话；整页刷新、关闭标签页或 Stop 会结束通话。返回后需再次点击 Start。同一 App 同时只能由一个浏览器持有语音连接。点击麦克风指示可打开完整 Live 页面与最近字幕；停止后隐藏未完成字幕。

## 结果与回报

语音委派会启动 Live 执行器，或引导兼容的活动工作。待处理决策保留在 Thread 中。通话期间，选定的成功最终结果会在保存后返回语音；工具过程输出和私有推理不会自动播报。

其他独立根 Thread 和 Coordinator 可向当前通话回报。下面是 Agent 工具调用，不是 shell 命令：

```python
send_thread_message(thread_id="live", message="The requested checks passed.", kind="report")
```

`kind="report"` 提供语音上下文，`kind="task"` 请求执行器继续工作。受管 worker 和子级执行不会获得该目标。通话离线时返回 `skipped`，原因为 `live_offline`；不会将任务或后续播报排队。

语音转录与任务历史分别保存。接受语音上下文不代表用户已听到。尤其在断线后，应打开来源 Thread 查看任务保存的结果。

## 隐私与连接错误

浏览器直接向 OpenAI 发送音频。订阅凭据保留在 Host；Host 处理信令、字幕、委派和选定的结果文本，不转发麦克风音频。

| 问题                        | 处理方式                                           |
| --------------------------- | -------------------------------------------------- |
| 没有 Live 入口或 Start 控件 | 在 General 设置中启用 Live 并保存。                |
| 麦克风无法启动              | 检查浏览器权限、HTTPS 或 localhost，以及采集支持。 |
| 其他浏览器持有语音连接      | 先停止原通话，再在此处启动。                       |
| 提供方或网络连接失败        | 阅读错误，修正访问或连接问题，再启动新通话。       |
| 任务需要决策                | 打开 tasks & decisions，在 Live Thread 中处理。    |
| 任务已完成但未收到语音结果  | 在对应 Thread 中阅读保存的结果。                   |

连接失败会结束该通话。Live 不重放任务、不切换账户，也不回退到 API key。提供方错误不包含响应正文和凭据。再次提交同一工作前，先检查已有任务状态。

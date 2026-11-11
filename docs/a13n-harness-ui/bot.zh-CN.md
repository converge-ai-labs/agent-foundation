---
title: Bot
description: 通过协调工作 Thread、默认每小时检查进展的个人助手，持续推进目标。
---

Bot 是 WebUI 侧边栏中的实例级助手。向 Bot 提出持续目标后，它可以查找或创建 Project、启动普通工作 Thread，并在后续 heartbeat 中检查已保存的结果。聊天页面复用现有 Thread 的阅读器、编辑器和原生面板。

## 启动和配置 Bot

1. 在侧边栏打开 **Bot**。Bot 默认关闭；在页面内选择已有 Model。Bot 使用内置指令，不需要选择 Agent。
2. 按需添加自定义指令，例如偏好的工作协调方式。自定义指令追加到内置指令之外，两者共存，不会替换内置内容。捕获的全局 `AGENTS.md` 指引也会生效。
3. 点击 **开启 Bot**。此操作只保存设置，不立即调用模型。首次 heartbeat 安排在一个间隔后，无需先发消息。之后可在 **设置 → 常规 → Bot** 中修改。

默认 heartbeat 间隔为 **一小时**。打开页面或保存设置不会发起模型请求。heartbeat 处理要求 WebUI 服务保持运行。持实例密钥的参与者共享 Bot；受限分享访客无法访问 Bot。[Live 模式](live-mode.md) 与其独立。

将以下字段合并到所选根 YAML 已有的 `webui` 映射中：

```yaml title="a13n-harness-ui.yaml — Bot"
webui:
  bot:
    enabled: true
    model: model-coordinator
    instructions: "Prefer coordinating work in project Threads."
    heartbeat_enabled: true
    interval_seconds: 3600
    connector_interval_seconds: 60
```

使用已有 Model ID。`model` 为 null 或省略时，仅沿用全局默认 Agent 的 Model，不继承该 Agent 的指令、子 Agent、Plugin 或 MCP 选择。没有有效 Model 时无法执行。Model 和指令变更应用于后续 Run。

heartbeat 间隔允许 60–86,400 秒，Connector 投递间隔允许 1–86,400 秒。自定义指令最多 1,048,576 个字符。编辑文件后，使用 `a13n-harness-ui config validate` 验证。

## 协调工作与记忆

Bot 的专属工作区是所选根 YAML 同级的 `bot/` 目录。默认配置下，路径为 `~/.a13n-harness-ui/bot/`。显式指定 `--config` 会改变其父目录。Bot 通过原生文件工具读取和维护协调材料。内置 Environment 能力启用文件工具，保持 Shell 和 Computer 工具关闭。内置指令建议将此目录用于协调材料，把实质工作交给普通工作 Thread。

将始终适用的工作区指引放在 `bot/AGENTS.md` 中。文件存在时，每个 Bot Run 都会通过有大小限制的文件上下文自动注入其内容，同时保留全局 `AGENTS.md` 指引。

Bot 通过 `bot/HEARTBEAT.md` 管理周期检查。每次 heartbeat 都会提醒 Bot 读取当前文件（如果存在），并按清单检查；文件内容按需读取，不会自动注入。Bot 可以创建和编辑该文件，记录检查项、优先级和跟进条件，并删除过时事项。你也可以直接编辑。文件缺失不会阻止 heartbeat。修改文件会改变后续检查内容，但不会触发 Run；heartbeat 的时间间隔仍由设置控制。

Bot 可使用 `create_project` 创建普通 Project 配置，并通过 `create_thread` 按正常的 Sidekick/全局 Agent 默认值启动工作。Project 创建、Thread 创建、Run 接受和任务完成是独立结果。内置指令要求 Bot 检查保存的结果后再报告成功。

启用 memory 时，Bot 正常获得全局记忆。它可以发现已配置的 Project，再调用 `read_project_memory` 按需读取 Project 记忆。这些读取有大小限制且为只读；各 Project 的记忆不会自动注入 Bot 上下文。协调笔记和任务保留在 Bot 自己的 Thread 中。

## Bot Notes

Agent 为每个普通根 Thread 管理一份简短的 Markdown 交接笔记。工作 Thread 使用 `read_bot_note()`、`write_bot_note(content)` 和 `clear_bot_note()` 访问自己的 slot；Bot 可以访问任意普通根 Thread 的 slot，并用 `list_bot_notes` 分页读取摘要。此功能没有人工管理表单。

笔记用于保存目标、引用、已确认的发现和下一步，最多 16 KiB UTF-8。空白内容会清除，内容相同则不修改，最后提交的操作生效。Bot 关闭时仍可使用笔记。归档保留笔记，fork 不复制，删除 Thread 会移除其 slot。写笔记不会唤醒 Bot；Bot 可在下一次合法输入时检查已保存结果、跟进并清除已处理事项。

## 消息与决策

Bot 聊天页面显示你的消息、所有普通最终文本回复，以及有意发布的主动消息。人类、Heartbeat 和 Connector 输入都遵循同一规则，无论输入是启动 Run 还是 steer。聊天不显示工具调用和中间文本。无需回复时，Bot 调用原生 `finish_silently` 输出工具，成功结束本轮而不发送消息。实时和已保存的聊天视图隐藏该工具的调用与结果，底层执行历史仍保留供检查。Bot 可以显式发布经过确认的结果、阻塞或问题；发布沿用现有未读和通知机制。

直接在 Bot 聊天中处理待回答的人类决策。Bot 只提供聊天视图，通知入口和旧的 Bot Thread 链接都会打开该聊天。heartbeat 和外部事件无法代答审批。服务器启用相应功能后，现有原生面板选择器提供文件和浏览器；Bot 不另做一套浏览器实现。

文本草稿保留在当前标签页中，刷新可能丢失尚未发送的草稿。提交结果不确定时，保留原命令并使用显式重试。重试沿用相同的输入 ID 和文本，不会再创建一份输入。Bot 空闲时，新消息会立即启动原生 Run；正在运行时，消息会通过原生 immediate steer 送入该确切的 Run，包括由 Heartbeat 或 Connector 启动的 Run。最终文本回复会显示在聊天中。Run 处于准备、取消或过期状态时可能拒绝输入；草稿会保留，可再次发送。拒绝不会将消息排队，也不会启动替代 Run。接受输入不代表完成。

## 暂停与恢复

使用暂停图标暂停自动处理。heartbeat 和所选 Connector 事件仍会收集，直接消息仍可使用。暂停不取消 Run，也不重置 heartbeat 时钟。单独关闭 Heartbeat 可停止 tick，同时保留 Connector 投递。

在设置中关闭 Bot 会停止新的 tick 和投递。历史、设置、Notes 和待处理输入全部保留；已接受的运行和其他 Thread 继续执行。聊天变为只读。重新开启后，后续正常投递机会可以处理保留的输入。待回答的人类决策需重新开启后才能继续。

在接受或执行过程中中断的输入会跨服务重启保留供检查，并暂停自动处理。旧版本排队的人类输入也会标记为中断，不再自动调度。Bot 不会自动重放这些输入，因为工具可能已经修改文件或外部系统。先检查显示的错误和工作 Thread 的结果，再明确发送后续要求。Bot 没有单独的 Run 时限；提供方失败和手动取消仍沿用原生执行生命周期。已废弃的 `webui.bot.run_timeout_seconds` 不再生效。现有文件沿用允许附加字段的策略，仍可正常加载；更新配置时请移除该字段。错过的 heartbeat 间隔会合并成一次机会，不逐次补跑。

## Connector 入站基础

Host 提供私有数据库存储，用于保存 Connector 设置、凭据和所选事件信封。heartbeat 和所选 Connector 事件共用自动 inbox，收集不受模型运行状态影响。周期性投递会公平合并待处理 tick 与跨 Connector 事件，并提交给 Bot 或向其确切的当前操作追加输入。原生执行接受输入后消费该批次，不等待模型确认或任务完成。待回答的人类决策会阻止投递。

在 **Settings → Connections → Connectors** 管理账户，也可以打开对话中的配置卡片。[Connector](connectors.md) 提供可信插件 SDK、结构化配置和规则表单、私有状态及原子事件入站。内置 GitHub provider 通过可复用的 Remote MCP 代码库提供服务工具；代码库不增加通用 MCP 配置入口。事件插件负责收集和匹配。Connector 凭据以明文保存在私有本地数据库中；请保护数据目录及其备份。外部事件内容提供上下文，不代表人类授权。

客户端集成参见 [Bot HTTP API](http-api.md#bot)，根设置参见[配置参考](configuration.md#webui-bot)。

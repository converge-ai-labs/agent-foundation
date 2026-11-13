---
title: Bot
description: 通过协调工作 Thread、默认每小时检查进展的个人助手，持续推进目标。
---

Bot 是 WebUI 侧边栏中的实例级助手。向 Bot 提出持续目标后，它可以查找或创建 Project、启动普通工作 Thread，并在后续 heartbeat 中检查已保存的结果。聊天页面复用现有 Thread 的阅读器、编辑器和原生面板。

## 启动和配置 Bot

1. 在侧边栏打开 **Bot**。Bot 默认关闭；页面内提供独立的 Agent 和 Model 选择，初始均为 **默认**。Agent 沿用全局默认 Agent，Model 沿用所选 Agent 的 Model。
2. 按需添加自定义指令，例如偏好的工作协调方式。自定义指令追加到内置指令之外，两者共存，不会替换内置内容。捕获的全局 `AGENTS.md` 指引也会生效。
3. 点击 **开启 Bot**。首次开启且 Model 可用时，服务器会代发一条可见的预设用户消息，让 Bot 了解并介绍工作区。这会正常调用模型，可能产生费用；你可以立即继续聊天。首次 heartbeat 安排在一个间隔后，无需先发消息。之后可在 **设置 → 常规 → Bot** 中修改。

默认 heartbeat 间隔为 **一小时**。打开页面不会发起模型请求。首次工作区引导独立于 Heartbeat 和浏览器页面，只尝试一次；已有聊天或尝试记录会阻止重启、重新开启时重复发送。失败的尝试仍可见，不会重复发送引导消息；后续 Heartbeat 可以检查失败的 Run 并继续。heartbeat 处理要求 WebUI 服务保持运行。持实例密钥的参与者共享 Bot；受限分享访客无法访问 Bot。[Live 模式](live-mode.md) 与其独立。

将以下字段合并到所选根 YAML 已有的 `webui` 映射中：

```yaml title="a13n-harness-ui.yaml — Bot"
webui:
  bot:
    enabled: true
    agent: null # 沿用 defaults.agent；填写已有 Agent ID 可选择其他执行设置
    model: null # 沿用所选 Agent 的 Model；填写已有 Model ID 可独立覆盖
    instructions: "Prefer coordinating work in project Threads."
    heartbeat_enabled: true
    interval_seconds: 3600
    connector_interval_seconds: 60
```

Bot 继承所选 Agent 的执行设置：Capability（包括 compaction、handoff）、工具和权限、子 Agent、Plugin 与 MCP 选择。角色指令替换为内置 Bot 策略和可选的 Bot 自定义指令。原生 Files、Shell 和 Computer 工具保持开启，并遵守所选执行策略和 Environment 限制。Browser 在启用时使用现有 Host Browser。结构化 `ask_user_question` 保持关闭；澄清通过普通聊天完成。`agent` 为 null 或省略时沿用 `defaults.agent`，填写已有 Agent ID 可选择其他执行基底。`model` 为 null 或省略时沿用所选 Agent 的 Model，填写已有 Model ID 可独立覆盖它。切换 Agent 会保留显式 Model 选择。没有有效 Agent 时，显式 Bot Model 使用最小内置执行设置。Agent 或 Model 引用无效时，配置更新会被拒绝，继续使用之前已接受的配置。没有有效 Model 时无法执行。变更应用于后续 Run，已捕获的 Run 保留原配置。

heartbeat 间隔允许 60–86,400 秒，Connector 投递间隔允许 1–86,400 秒。自定义指令最多 1,048,576 个字符。编辑文件后，使用 `a13n-harness-ui config validate` 验证。

## 协调工作与记忆

Bot 的专属工作区是所选根 YAML 同级的 `bot/` 目录。默认配置下，路径为 `~/.a13n-harness-ui/bot/`。显式指定 `--config` 会改变其父目录。该目录始终是 Bot 的默认工作目录，打开 Project Environment 后也不改变。Bot 通过原生工具直接完成短小、有明确边界的任务，将耗时的实现、研究或运维交给工作 Thread。此目录用于协调材料，不代表你的工作 Project。

Bot 可以直接访问所有已配置 Project 的 Environment，无需仅为访问文件或工具而创建 Thread：

1. 使用 `list_environment_targets` 发现当前 Run 捕获的目标。
2. 将所选目标 ID 传给 `open_environment_target`。
3. Files 使用返回的虚拟路径，Shell 或 Computer 使用明确的别名。Bot 的默认目录保持不变。

每个 Project 目标使用其配置的根目录、本地 profile、Device 绑定和默认 Environment。Project 的 Agent、MCP 选择和 Run Extension 不会替换 Bot 的执行策略。连接仅在使用目标时打开；单个目标不可用不会阻止其他工作。不同目标保留独立 Session，即使指向同一个目录。配置变更应用于后续 Run。`host-desktop` 目标在开启且可用时提供 Host Computer Use。Browser 仍由 Host 管理，与 Project 目标独立。现有工具权限和 Environment 限制继续生效；能够访问不代表获得执行有后果操作的授权。

将始终适用的工作区指引放在 `bot/AGENTS.md` 中。文件存在时，每个 Bot Run 都会通过有大小限制的文件上下文自动注入其内容，同时保留全局 `AGENTS.md` 指引。

Bot 通过 `bot/HEARTBEAT.md` 管理周期检查。每次 heartbeat 都会提醒 Bot 读取当前文件（如果存在），并按清单检查；文件内容按需读取，不会自动注入。Bot 可以创建和编辑该文件，记录检查项、优先级和跟进条件，并删除过时事项。你也可以直接编辑。文件缺失不会阻止 heartbeat。修改文件会改变后续检查内容，但不会触发 Run；heartbeat 的时间间隔仍由设置控制。

Bot 可使用 `create_project` 创建普通 Project 配置，并通过 `create_thread` 按正常的 Sidekick/全局 Agent 默认值启动工作。Project 创建、Thread 创建、Run 接受和任务完成是独立结果。内置指令要求 Bot 检查保存的结果后再报告成功。

启用 memory 时，Bot 通过现有 memory tools 使用全局记忆，以及根 YAML 同级 `memory/bot/` 下专属的 `bot` scope。`MEMORY.md` 保持为简洁的工作区地图，主题文件保存持久的 Project 理解、来源引用和不确定之处。Heartbeat 分批检查相关、有变化或尚不了解的 Project，不另建项目档案数据库或记忆提取链路。

Bot 先发现当前 Project ID，再通过 `read_project_memory` 从索引开始，按需读取相关文件。这些 Project 原始记忆只读、有大小限制，也不会自动注入。创建项目工作前，Bot 会核实 Project，并向 `create_thread` 传入明确的 Project ID。当前工作交接保存在 Bot Notes，全局记忆保留跨项目偏好。首次引导只授权了解工作区和维护记忆，不授权实现工作或修改配置。

## Bot Notes

Agent 为每个普通根 Thread 管理一份简短的 Markdown 交接笔记。Bot 开启时，工作 Thread 应尽早记录需要持续跟进的工作，更新重要进展和阻塞，并在交接或结束本轮前整理笔记。已完成或取消的工作应移除；没有剩余跟进时清空笔记。工作 Thread 使用 `read_bot_note()`、`write_bot_note(content)` 和 `clear_bot_note()` 访问自己的 slot。当前笔记和 Bot 的开启、暂停状态会自动出现在模型上下文中。Bot 可以访问普通根 Thread 的 slot，并用 `list_bot_notes` 分页读取摘要。

在 Bot 的辅助面板打开 **工作笔记**，可浏览 Thread 链接、Project、更新时间和 Markdown 预览，也可展开全文。普通 Thread 的 Notes 检查面板单独显示自己的 Bot Note，与私有工作笔记区分。两个视图均为只读。

笔记用于保存目标、引用、已确认的发现和下一步，最多 16 KiB UTF-8。空白内容会清除，内容相同则不修改，最后提交的操作生效。Bot 关闭时仍可使用笔记。归档保留笔记，fork 不复制，删除 Thread 会移除其 slot。写笔记不会唤醒 Bot。交接、结果、阻塞或问题需要关注时，工作 Thread 先更新笔记，再发送一条实质性 `send_thread_message(thread_id='thread_bot', kind='report', message=...)`。Host 保留来源 Thread 身份，并通知 Bot 现有的投递循环。Bot 关闭、暂停、不可用或等待人类决策时，报告保留待处理。受管 worker 通过 Coordinator 汇报。Bot 发出的 Thread 输入包含隐藏的回报地址指引；接收方除了回报，也保留已保存的最终结果。

## 消息与决策

底部信息栏右侧的上下文和费用始终显示，左侧的任务与执行检查入口在现有 **Debug** 模式显示。普通聊天的信息栏不变。上下文采用已捕获 Run 的 Model 容量和最近一次根请求的 token 数，不是累计用量或下次选择的 Model；容量缺失时显示未知。连接反馈、实际错误和审批仍在普通模式显示；通用 Host 中断提示保留在 Debug 诊断中。

配置语音输入后，可使用 **语音输入**。Bot 复用普通聊天的录音、转写设置和订阅账号选择。停止录音后，最终文本只插入当前草稿一次，不会自动发送。关闭 Debug 时，附件和语音输入仍可用，Bot 工作中也可使用。

Bot 聊天页面显示你的消息、助手文本、最终回复，以及有意发布的主动消息。工作日志沿用普通聊天的折叠方式，展示可渲染的推理、工具调用、结果和实时执行进展，刷新后也可查看。注入的隐藏提示和标记为 `display: false` 的内容仍然隐藏。对于人类输入，Bot 在耗时工作前简短回应，并提供有用的进展。每段已经结束的可见助手文本都会将 Bot 标为未读并发起消息通知，包括工具调用前的中间文本。最终结算不会重复发布相同消息。正在查看 `/bot` 时，Bot 不弹出应用内通知；其他会话的通知仍然显示。未读状态和系统 Push 的投递条件不变。工作日志、推理和流式 token 不单独产生通知。

Heartbeat 和其他自动工作直接使用工具，不添加开场白或例行进度旁白。Bot 只简洁报告重要结果、可处理的阻塞或需要你关注的决策；内部协调细节保留在笔记和任务中。无需回复时，Bot 调用原生 `finish_silently` 输出工具，成功结束本轮而不发布消息；已经显示的文本仍会保留。实时和已保存的聊天视图隐藏该工具的调用与结果，底层执行历史仍保留供检查。Bot 可以显式发布经过确认的结果、阻塞或问题。

直接在 Bot 聊天中处理待回答的人类决策。Bot 只提供聊天视图，通知入口和旧的 Bot Thread 链接都会打开该聊天。heartbeat 和外部事件无法代答审批。服务器启用相应功能后，现有原生面板选择器提供文件和浏览器；Bot 不另做一套浏览器实现。

Bot 使用普通共享输入框：受信任的参与者共同编辑同一份文本和附件选择，沿用在线状态、同步和服务端草稿保存。打开或刷新 `/bot` 会恢复共享草稿，不启动 Run。Send 和 Steer 等待同步后捕获输入，只在接受后清除已捕获的编辑；并发编辑和被拒绝的输入仍保留。提交结果不确定时，保留原命令并使用显式重试。重试沿用相同的输入 ID、有序内容和草稿捕获，不会再创建一份输入。Bot 仍不向限定范围的访客分享开放。Bot 空闲时，新消息会立即启动原生 Run；正在运行时，消息会通过原生 immediate steer 送入该确切的 Run，包括由 Heartbeat 或 Connector 启动的 Run。最终文本回复会显示在聊天中。Run 处于准备、取消或过期状态时可能拒绝输入；草稿会保留，可再次发送。拒绝不会将消息排队，也不会启动替代 Run。接受输入不代表完成。

## 暂停与恢复

使用暂停图标暂停自动处理。heartbeat、所选 Connector 事件和明确的 Thread 报告仍会收集，人类直接消息仍可使用。暂停不取消 Run，也不重置 heartbeat 时钟。单独关闭 Heartbeat 可停止 tick，同时保留 Connector 投递。

在设置中关闭 Bot 会停止新的 tick 和投递。历史、设置、Notes 和待处理输入全部保留；已接受的运行和其他 Thread 继续执行。聊天变为只读。重新开启后，后续正常投递机会可以处理保留的输入。待回答的人类决策需重新开启后才能继续。

Run 失败后会保留输入和错误供检查，但不会暂停自动处理。后续 Heartbeat 或新的自动输入会启动一次新的 Run，先检查失败再继续；不会重放失败输入或工具副作用。成功后清除当前错误，历史失败记录和显式暂停仍保留。接受或收尾结果不明、手动取消，以及服务器重启后发现的未完成输入，仍会暂停自动处理。旧版本排队的人类输入也会标记为中断，不再自动调度。Bot 不会自动重放这些输入，因为工具可能已经修改文件或外部系统。先检查显示的错误和工作 Thread 的结果，再明确发送后续要求。Bot 没有单独的 Run 时限；提供方失败和手动取消仍沿用原生执行生命周期。已废弃的 `webui.bot.run_timeout_seconds` 不再生效。现有文件沿用允许附加字段的策略，仍可正常加载；更新配置时请移除该字段。错过的 heartbeat 间隔会合并成一次机会，不逐次补跑。

## Connector 入站基础

Host 提供私有数据库存储，用于保存 Connector 设置、凭据和所选事件信封。heartbeat 和所选 Connector 事件共用自动 inbox，收集不受模型运行状态影响。周期性投递会公平合并待处理 tick 与跨 Connector 事件，并提交给 Bot 或向其确切的当前操作追加输入。原生执行接受输入后消费该批次，不等待模型确认或任务完成。待回答的人类决策会阻止投递。

在 **Settings → Connections → Connectors** 管理账户，也可以打开对话中的配置卡片。[Connector](connectors.md) 提供可信插件 SDK、结构化配置和规则表单、私有状态及原子事件入站。内置 GitHub provider 通过可复用的 Remote MCP 代码库提供服务工具；代码库不增加通用 MCP 配置入口。事件插件负责收集和匹配。Connector 凭据以明文保存在私有本地数据库中；请保护数据目录及其备份。外部事件内容提供上下文，不代表人类授权。

客户端集成参见 [Bot HTTP API](http-api.md#bot)，根设置参见[配置参考](configuration.md#webui-bot)。

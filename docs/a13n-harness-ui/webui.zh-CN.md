---
title: 使用 WebUI
description: 运行 WebUI，共享对话、文件、Git 变更、终端和配置。
---

WebUI 是 Harness UI 的浏览器工作台，是面向个人和可信小团队的协作环境。它与 TUI 共用同一个 `HarnessUiApp`，提供对话、执行控制、初始化设置、provider 账户、配置和 Environment 就绪检查。WebUI 不是 Console，不提供参与者之间独立的权限或租户隔离。每个对话就是一个根 Thread 及其保存的历史。

启动服务器，打开输出中的登录链接，连接模型，然后发送第一条提示词。使用期间请保持服务器进程运行。共享实例前，先了解[认证方式](#authentication-and-key-retention)。

## 启动服务器

WebUI 提供向导式设置、共享对话与草稿、已保存的历史、实时输出、待处理决策、配置，以及 Host 原生 Files、Changes 和 Terminal 面板。Chat 是主视图，Files 和 Changes 在侧边抽屉中打开，Terminal 位于下方。查看 Git 需要安装 Git 可执行程序；原生 PTY 需要 POSIX Host。目前评论控件已停用，但后端保存的评论仍可通过 API 读取。

```bash
a13n-harness-ui webui                       # 127.0.0.1:8765, generated per-process API key
a13n-harness-ui webui --host 127.0.0.1 --port 9000
a13n-harness-ui webui --no-share-computer   # Opt out of native computer sharing
```

原生 [Files](http-api.md#native-host-files)、[Changes](http-api.md#native-git-changes) 和 [Terminal](http-api.md#native-terminal) 访问默认开启。这些面板使用服务器账户或容器挂载，**不使用** Agent 选定的 Environment。可以用 `--no-share-computer` 关闭它们，不影响共享聊天、[草稿](http-api.md#shared-composer)或[页面在线状态](http-api.md#page-presence)。工作台中的 Git 变更只能查看；修改 Git 状态需要明确执行 shell 操作。在线状态和共享草稿都是实时状态，不属于保存的续接状态。

```mermaid
flowchart TB
    Browser[浏览器] -->|Files、Changes、Terminal| Host[WebUI 服务器账户]
    Browser -->|发送提示词| App[HarnessUiApp]
    App --> Agent[Agent Run]
    Agent --> Environment[选定的 Environment 或 Device]

    class Browser app
    class App,Agent a13n
```

Markdown 文件默认以 **Preview** 打开，可通过 **Preview** 和 **Text** 按钮切换。预览使用当前本地缓冲区，因此保存前就能检查未保存的编辑。文件操作采用紧凑按钮，**Add to chat** 是唯一的文件上下文添加入口：文本编辑器有选区时添加选中的源码行，否则添加已审阅的完整文件。它不会发送输入框内容。Agent 回答中指向同一实例 Host 文件的链接，会在当前页面打开 Files 抽屉。WebUI 根 Agent 在每次输入的界面指引中会获得支持的相对链接格式：`/threads/{root_thread_id}?native=files&native_path={URL-encoded absolute Host path}`。单独的 Host 路径和直接的 Files API URL 不是 WebUI 文件链接；外部 Markdown 链接仍会单独打开。

## 查看 Memory

启用 Memory 后，左下导航的 **Memory** 按钮会打开一个只能观测的 Memory 页面。Global memory 与各个 Project 的作用域分开。中间区域复用对话历史、实时工具活动和 **Inspect & usage** ，但没有输入框或手动执行控件。每次自动整理 Run 都使用新的模型上下文，之前的轮次和记录的用量仍可查看，服务器重启后也会保留。

右侧面板展示当前作用域的**现有** 文件，而非历史快照。即使尚未执行过整理 Run，也可以查看文件；打开作用域不会触发整理。查看器只读，无需开启 Host 计算机共享。如需修改记忆，请通过对服务器的 Host 访问（例如 shell 或编辑器）修改所选根配置旁 `memory/` 下的文件。

Memory 控制仍位于 **Settings → General**。关闭自动整理会保留 Memory 按钮和查看器；关闭 Memory 则隐藏按钮。这两个开关都不会删除文件或已保存的历史。

## 与 Coordinator 协作

选择一个 Project，在输入框中开启 **Coordinator**，然后发送目标。这会创建 Coordinator，或在发送前将已有普通对话转换为 Coordinator。窄屏下，开关仍位于输入框顶部。每个 Project 可以有多个 Coordinator；**Goal** 是独立选项，参阅[围绕 Goal 持续工作](everyday-use.md#work-toward-a-goal)。

创建或转换后，该角色不可撤销。如果之后发送失败，对话仍保留 Coordinator 角色，输入也会保留以便重试。Coordinator 必须归属一个 Project。

也可以打开已有对话的操作菜单或 **Conversation details**，选择 **Make Coordinator** 并确认。该对话必须空闲、未归档、已绑定 Project，且没有待处理决策。转换保留 URL、历史、标题和设置，角色不可撤销。worker 不能转换，已有对话（包括以前通过 [Sidekick](#agent-collaboration-and-sidekick) 创建的对话）也不会被自动收编。

在普通输入框中说明目标。Coordinator 可以创建自己的 worker、回答它们的问题，并整合已验证的结果。连接节点图标用于标识此角色。点击标题打开 Coordinator，点击展开箭头查看其 worker。每个 worker 都是普通根对话，有自己的历史、控件和人工交互，并不是 subagent。worker 只显示在所属 Coordinator 下，不会重复出现在 Running 或 Recent 中。搜索仍能找到它们，并标记为 **Coordinator worker**。即使所属 Coordinator 已归档或不在当前分页中，显示出来的 worker 仍提供访问其 Coordinator 的入口。

如果要自行分配工作，在 Coordinator 的操作菜单中选择 **New worker**，直接在新对话中编写并发送工作内容，Coordinator 不会代为转发。**Managed by · name** 会替代 Coordinator 开关，并固定 Project。创建前，可用标签上的移除按钮将草稿改为独立对话，消息、附件和选项都不会丢失。创建后标签固定，即使发送失败也一样。创建结果不确定时，归属关系保持锁定，直到查看保留的对话。已有 worker 的输入框中也会显示相同的所属标签。

每个 Coordinator 都可通过 **Pause automatic follow-up** / **Enable automatic follow-up** 控制自动生命周期通知。设置保存在后端，并在浏览器之间共享。暂停不会隐藏 Coordinator 或 worker、移除角色，也不会阻止手动消息和执行。新 Coordinator 默认开启自动跟进；迁移的 Coordinator 保留此前的启用设置。Sidekick 偏好为 Agent 创建的 worker 提供默认值，直接创建的 worker 则使用输入框显示的选择。关闭 Sidekick 不会关闭 Coordinator 或自动跟进。

当你首次直接向 worker 提交任务，或所属 worker 完成、失败、取消、等待输入时，自动跟进会尝试通知 Coordinator。Coordinator 会先检查保存的结果，再判断工作是否完成。通知采用尽力交付，没有重试或重启后重放。关闭浏览器不影响它继续运行；停止 Coordinator 不会停止 worker，也不会暂停之后的通知。问题和审批的截止时间仍有效。

## 为重要对话加星标

将鼠标移到对话行上或让其获得焦点，打开 **…** 菜单并选择 **Star conversation**。触屏上不需要悬停就能看到 **…** 按钮。加星标后，菜单旁始终显示实心星；选择 **Unstar conversation** 可取消。星标与实例中的所有参与者共享，不是个人书签，切换浏览器或重启服务器后仍保留。

在每个 Project 中，星标对话位于 **Recent** 顶部，不占用五个普通最近对话的位置，也不会藏在 **Show more** 后面。Running 和未读的 **New results** 仍优先显示，且不会产生重复行。添加或取消星标不会打开对话，也不会改变最后访问时间。归档会隐藏星标对话，但保留星标以便恢复。普通对话和 Coordinator 支持星标；worker 仍嵌套在所属 Coordinator 下，不会新增顶层快捷入口。

## 找回尚未发送的输入

对话搜索框下方的 **Drafts** 会打开一个列表，列出留有未完成共享输入的已保存对话，即使 Project 已折叠或对话不在最近列表中也能找到。列表中每一项都显示 Project 名称。普通列表中的对应对话旁还会出现 **Draft** 标记，不会替换运行中或未读结果指示。搜索或筛选下方列表时，**Drafts** 仍然可见。

打开对话不会消除提醒。成功发送并清除提交时捕获的输入，或手动删除文字和选定附件，才会清除；其他参与者同时新增的编辑仍会保留标记。待上传和上传失败的附件也计入未发送输入，纯空白和输入框设置变更则不计入。归档会将该对话从 **Drafts** 中移除，不丢弃草稿。

这些是共享对话草稿，不是个人队列。同一服务器仍在运行时，刷新浏览器后可以重新找到已同步的草稿；服务器重启会丢弃它们。尚未创建对话的独立 New conversation 草稿仍从 Home 打开。

## Agent 与模型设置

输入框底部右侧只读显示当前 Agent 和模型名称。通过旁边的 **Agent & Model settings** 图标修改。Agent、Model 和 Reasoning mode 在同一面板中打开各自选项；Thinking 等级和 Fast 按钮直接显示在概览中。每项都有 **Use default** ，可以恢复继承设置，无需猜测哪个显式值与默认值等效。

手机上面板以底部弹层打开。**Goal**、**Coordinator** 和 **Environments** 仍位于输入框顶部。在任何屏幕尺寸下，附件旁的 **Clear context** 橡皮擦按钮都会让下一条消息不带之前的模型消息、笔记、任务或其他已保存的 Agent 工作状态开始。待处理的问题和审批会被丢弃；聊天历史、文件、对话设置和未发送输入会保留。

## Fast 模式

打开 **Agent & Model settings**，直接切换 **Fast** 。旁边的标签区分显式 **On** 、**Off** 和 **Default** 。**Default** 遵循模型配置的 Fast 设置；模型未配置层级时，标签显示 **Provider default**，由 provider 决定，这不等同于 Off。On 和 Off 分别为此标签页后续发送显式请求 Fast 或标准处理。选择 **Use default** 清除覆盖。切换 Agent 或模型也会清除临时选择，转为遵循新模型。Fast 不改变 thinking，不保存模型配置，也不随引导发送。

Context / Cost / Cache / Time 一行显示的 **Fast On**、**Off** 或 **Default** 来自当前或已保存 Run 捕获的设置，并非下次发送的选择。Default 表示由模型或 provider 决定；没有捕获到设置时显示横线。这是请求设置，不代表服务已确认提速。Fast 可能增加 API 费用或订阅额度消耗。

控件遵循连接的原生语义：OpenAI API 和 Codex 订阅的优先处理、已确认支持的直连 Anthropic Fast 速度，或已确认支持的 Gemini API 优先处理。不支持或尚未确认的连接会禁用显式选择并说明原因，同时保留 Default 以清除旧覆盖。不会假定 Claude 订阅、Grok 订阅、Vertex 预留吞吐量或任意兼容网关具有同样的 Fast 支持。自定义 endpoint 是否支持、账户是否有权限，由 provider 负责；不会自动发起付费探测，也不会自动回退。

## 浏览对话输入

Chat 左侧的细导航条为每次普通输入提供一个标记。悬停或聚焦可预览输入和保存的输出片段，点击即可跳转。引导消息仍属于原来的轮次，不新增标记。窄屏下，使用 **Input history** 打开同一目录。目录也包含当前尚未加载的历史输入，选择后会加载对应历史窗口。**Load later messages** 和 **Back to latest** 用于返回较新的内容，不会重新提交任何输入。

输入、已应用的引导和所有 Agent 文字——包括进度消息及最终回答的每一部分——都按时间顺序显示。这些消息之间连续的推理、工具和其他执行活动会各自归入 **Execution details**，即使仍在执行也默认折叠。展开后只查看该段活动；移动端以全屏阅读器打开。每个标题统计的是该段已经加载的工具调用，而非整个轮次。

如果已保存轮次的部分内容尚未加载，对话会自动加载，并在原位置显示 **Loading turn…**。无需展开详情即可加载缺失的消息和执行活动；失败时提供 **Retry loading turn**。待处理问题和审批也可在折叠区外操作。导航和展开只是个人显示选择，不代表执行成功，不改变 Agent 上下文，也不影响其他参与者的视图。

## 待处理问题、审批和外部结果

待决策表单处理结构化问题、通用工具审批和外部提供的工具结果。Shell 审批先显示风险评估和理由，再显示命令、工作目录和选定挂载点。参数预览会隐藏环境变量值。其他审批显示工具、参数和可用审查上下文，不要求专门的工具表单。

单个审批可选择 **Approve once** 或 **Deny** ，拒绝前也可填写原因。仅当请求允许替换参数时，才显示 **Edit arguments** ；绑定的 shell 审批必须保留原参数。如需修改这类命令，应拒绝并解释原因，让 Agent 提出新命令。服务器省略了参数的请求无法从表单批准；仅缺少风险评估不会阻止决策。批准不代表执行已经成功。

多个请求或混合请求必须通过 **Submit responses** 一次性完整回答。**Provide a result** 接受 JSON 格式的真实外部工具结果，不会在浏览器中执行工具。如果结果就是 `null`，必须明确输入；空编辑器不算结果。不会自动选中任何批准操作。如果提交确认丢失，应检查当前请求，不要直接认定失败；浏览器绝不会自动重发决策。

### 问题与审批超时

每批新出现的根问题、审批和外部结果请求，共用一个由服务器管理的响应窗口，由 `tools.interaction_timeout_seconds` 控制，默认 **120 秒**。工作台显示剩余时间。必须在截止前提交完整表单；只选择部分选项，或填写但尚未提交的答案，都不会发给 Agent。

超时后，服务器以明确的失败结果继续：未回答的问题会要求 Agent 在可行时作合理假设并继续，审批则被**拒绝**，绝不会自动批准。切换对话、刷新、打开其他浏览器或关闭页面都不会重置或停止计时，其他参与者也可能先行回答。

停止服务器或归档对话会取消待处理计时器。重启不会重放已经过去的截止时间：此前进程保留的问题仍可手动回答，但没有倒计时。如果超时响应未能启动执行或保存，应先检查报告的操作，再决定是否手动重试；App 不会反复提交。TUI 中的问题仍使用独立的逐题倒计时。

## 交互式 MCP 工具结果

已选择启用的 [MCP Apps](mcp-apps.md)会在真实工具结果旁内联展示，Run 结束后仍可交互。已保存历史只有点击 **Open App** 后才会执行 App HTML。**Activate interactions** 用于经过当前策略检查的服务器操作；工具审批、选定上下文、消息确认和外部链接确认都位于 iframe 外可信的 WebUI 卡片中。打开不会重复工具调用，关闭 View 也不会丢弃服务器连接。[Apps 指南](mcp-apps.md)说明了设置、生命周期、限制，以及远程部署必须使用的独立沙箱 origin。

## 安装为应用

打开 **Settings → General → Install Harness UI**，将工作台安装到独立窗口。如果浏览器提供安装提示，选择 **Install app** ；否则使用浏览器提供的 **Install app** 或 **Add to Home Screen** 。iPhone 或 iPad 上，在 Safari 打开页面，选择 **Share → Add to Home Screen** ；Mac 上，Safari 提供 **File → Add to Dock** 。不同浏览器的支持情况和菜单文字有所差异，仍可直接通过普通浏览器访问。

安装需要 HTTPS，或 `localhost`、`127.0.0.1` 等本地回环地址。手机通过局域网 IP 和普通 HTTP 访问服务器时，不能保证同样的安装能力。请保持服务器地址稳定：改变协议、主机名或端口会改变浏览器 origin，可能需要重新安装和登录。

安装后的应用仍需要能连接到 Harness UI 服务器。提示时输入实例密钥（**Instance API key**）；关闭应用后的提醒，需要另行启用[后台通知](#task-notifications)。重新加载前保存编辑器中的私人修改；安装不会增加离线执行或编辑器恢复能力。

## 重启或更新服务器

通过 **SIGTERM**（或 Ctrl+C）正常停止 WebUI，**等待进程退出** ，再使用**同一数据目录** 启动。无需预先发请求或操作设置。更新时，应在旧进程退出后、启动新进程前安装新版本。

优雅关闭期间，App 停止接受新输入，给正在运行的模型和工具批次留出时间，到达可安全继续的边界。它先保存根及子 agent 检查点，完成对应 Environment 的收尾，然后才提交重启交接。下次启动时，交接只消费一次：将兼容的中断 Run 重建为新的 Run 并继续，不会重新发送提示词或重放保存的工具批次。浏览器重连只获取当前状态。

`shutdown_timeout_seconds`（默认 60 秒）限制的是等待安全边界的时间，**不是整个进程的退出时间**。如果模型或工具批次不能在此时间内完成，或者检查点、Environment 收尾失败，该批次就不会被登记为可自动恢复。取消和清理仍需完成。请给进程管理器预留这些步骤的时间，不要在旧进程仍执行时启动替代进程。

自动继续需要成功的优雅关闭，以及兼容的重建。强制终止或恢复失败后，应先检查日志和保存的历史，再重试结果不确定的工作。待决策请求仍未回答。原生终端、旧 shell handle、未发送草稿和未保存的编辑器都不会恢复。

## 服务器日志与关闭

前台服务器按照 `log_level` 和 `log_format`（`pretty` 或 `json`）报告启动、就绪、API 响应状态和清理进度。默认 `INFO` 显示普通 API 活动；静态资源和成功的健康探测使用 `DEBUG`。API 记录包含路由模板、状态和响应头返回耗时，不含请求体、实例密钥、查询值或原生文件路径。警告还包含应用错误码、已知时的安全固定原因，以及可获得的 Thread、receipt 和 Run 标识。例如，`thread_history_continuation_changed`（transcript 400）和 `thread_continuation_conflict`（tasks 409）都表示读取前选定的保存续接已变化，应刷新对话。其他失败保留各自错误码。动态错误文字留在 API 响应中，不复制到日志。

遇到 `object_payload_incompatible` 时，检查对应的存储警告：其中包含对象类型和摘要、schema/codec 版本、预期 payload 模型，以及最多八个带字段位置和错误类型的校验错误。任意映射键会被遮蔽，字段值和动态错误消息被省略。应据此诊断兼容性问题，不要清空数据目录。旧模型的 `context_window` 输入仍可按 `context_window_tokens` 读取，无需改写已有文件或快照。

登录 URL 在启动前输出；**WebUI ready** 才表示监听器成功启动。

按一次 **Ctrl+C** 或发送 **SIGTERM** 停止。服务器报告 **Stopping WebUI** ，先结束浏览器事件流，再等待 HTTP 连接结束，关闭 WebSocket，并让 App 清理终端会话、活跃 Run 和存储。**WebUI stopped** 表示正常清理完成。浏览器断开本身不会停止 Run 或终端会话。连接排空超时是后备机制，不是可信 Python 清理的硬性截止时间。关闭较慢时仍会报告已等待时间；`DEBUG` 还显示 App 清理阶段，包括已接受操作、终端会话、根及子 Run 和订阅。将日志等级调为 `WARNING` 会隐藏普通进度。

WebUI 与服务器失联时，会用一条紧凑的 **Connection interrupted** 提示替代各面板重复的连接错误。它自动重连，**Retry now** 可以跳过当前等待间隔。服务器已停止时，应先重新启动。其他可操作错误仍保留。这不是离线模式：重连只刷新观测，不重放失败的保存或提示词提交。

## 配置工作台

全新安装时，WebUI 会自动打开两步设置向导：

1. **Model**：为 ChatGPT、Codex、Grok 或 GitHub Copilot 订阅选择 **Connect account**，复用可用账户，或保存 provider API key。设备登录显示验证链接和可复制的验证码；浏览器回调登录是高级选项，需要能访问服务器的回环监听器。凭据由此服务器共享，与浏览器的实例密钥分开。新 API key 会立即保存，即使之后退出向导也一样。然后从当前安装版本提供的建议中选择模型，或输入 API provider 模型 ID 和 endpoint。已确认的默认值覆盖推理、上下文和原生工具，也保留高级控件。这些只是建议，并不验证账户是否有模型使用权限。
2. **Workspace**：Full Control 以 Host 账户运行，不隔离；Sandbox 必须通过显式就绪检查。可以填写已有的服务器 Project 目录，也可以留空创建无 Project 的对话。审阅配置后，选择 **Save and start chatting** 。

设置会打开一个空的首次对话，并聚焦输入框，不会发送提示词或发起模型请求测试。**Set up later** 保留不含秘密的草稿，不会在每次导航时重新打开向导。刷新和在另一标签页认证后仍保留选择，但秘密输入不会持久保存在浏览器中。如果保存响应丢失，先用 **Check saved setup and open conversation** 检查，再重试。部分保存或文件变更会明确提示，不会被当作完整保存。

已有安装保留对话和配置。**General → Setup & diagnostics** 提供针对性的修复，不会重新初始化。**Settings** 包含 General、Notifications、Agents、Models、Capabilities、Environments、Projects、Accounts & API keys、MCP connections 和 Advanced。之后连接 provider，也使用 **Accounts & API keys** 下的同一流程。

**Capabilities** 在 **Save changes** 后为选定 Agent 启用已安装的 Capability，不是全局开关。可复用的 agent 插件配置仍单独管理。**Environments** 展示远程 Device、已配置 profile、内置只读 Environment 和已安装 provider。**Configure** 为可用 provider 打开 profile 草稿；provider 专属的适配器和设置仍放在配置文件中。软件包安装在服务器上完成，不通过这些控件执行。

**Advanced** 支持创建、检查、保存和删除配置。具体任务的 Add 操作会直接打开草稿，未保存草稿保留在对应列表中。**Save changes** 已包含配置校验，因此 **Check configuration** 可选。普通字段和高级 YAML 编辑器共用一个本地草稿。当前标签页中导航或替换实例密钥不会丢失编辑，重新加载则会。配置发布以完整文件为单位，后写覆盖前写；发现外部变化时，不会静默覆盖未保存的草稿。校验不会发布，活跃 Run 继续使用捕获的配置。Advanced 编辑器无法读取 MCP 源文件，因此保存该文件会替换其中的所有资源和所有不可见字段。

**Projects** 直接编辑服务器目录和默认值，另有已保存默认值预览及逐项来源说明。每个文件夹各占一行，配有添加和移除控件。**Browse** 可逐层浏览服务器目录、进入父目录或输入路径，并选择 **Use this directory** ；保存前，选择只在草稿中生效。使用 `--no-share-computer` 时需手动输入路径。保存的 Project 默认值和文件夹用于初始化新对话。已有对话保留保存的根目录，除非明确修改下次 Run 的 Environment 选择。Default、None 和 Custom 列表选择具有不同含义。预览不包含未保存的源文件修改，也不执行模型。

首次进入时，右上角会自动显示生成的协作名称；点击修改，**Save name** 会在当前浏览器记住它，并更新实时在线状态和输入框标签。在线指示打开按标签页区分的参与者目录。此显示名称不是 provider 登录，也不是经过认证的身份。

### 添加或移除工作环境

在 **Settings → Environments** 中，使用标识、HTTP endpoint 或反向 WebSocket 传输，以及凭据引用添加 Device 连接。**Check connection** 读取实时 Device 描述；保存连接不要求 Device 在线。同一资源可复用于不同工作目录和别名。

在 **Projects** 或 **Configuration → Change next Run selections** 中，使用 **Add environment** ，选定 Device，再输入已知绝对工作目录，或浏览在线 Device。**Use this directory** 只修改草稿。添加或移除当前默认 Environment 时，明确选择新的默认 Environment，再保存。Project 可以组合本地文件夹和远程 Environment，也可以只使用远程 Environment，但不能移除最后一个工作 Environment。Device 目录浏览不依赖原生计算机共享。

对未被使用的 Device 或自定义 profile 使用 **Forget**，即可删除本地配置，即使 Device 离线或 provider 已卸载也可以。对话框会列出当前配置引用，并链接到对应编辑器。先修复这些默认值，再回来移除资源。这不会删除远程数据、卸载 provider 或删除对话历史。内置 Environment 不能移除。

已有对话保留保存的选择。被移除的资源显示为 **Not configured**，不会静默换成其他 Environment。打开 **Conversation details → Configuration → Change next Run selections** ，移除或替换缺失的 Environment，必要时选择替代默认值，再保存。修改只应用于未来 Run；之前 Run 的捕获配置和续接状态不变。

Files、Changes 和 Terminal 面板仍操作监听器的 Host。选择 Device 不会让它们变成远程面板，Device 工作目录也不是文件系统沙箱。文件配置方式参阅 [Device 配置与移除](environments-and-projects.md#add-device-bindings)。

### 后续 Run 使用的环境

打开输入框中的 **Environments**，同时编辑本地模式、目录、远程绑定和默认工作位置。**Local · Harness server → Local mode** 提供 **Sandbox** 、**Full Control** 和已配置 profile，已有对话也可使用。**Follow conversation local mode** 继承保存的本地 profile。关闭而不应用会丢弃草稿，**Use conversation defaults** 清除所有临时覆盖。Full Control 以服务器 Host 账户运行，不是沙箱。Sandbox 需要 Host 支持的隔离启动器和原生运行时，不可用时会明确失败，不会转为 Full Control。隔离边界由 Host 建立，Device daemon 不把 Project 根目录当作访问策略强制执行。

显式选择会随此标签页启动的每次 Run 发送，直到修改或选择 Default。它不改写对话默认值，不影响其他参与者的选择，也不改变正在运行的 Run。执行期间，选择器准备的是下次 Send；**Steer** 不会改变活跃 Run 的 Environment。回答延后问题或其自动超时，仍保留暂停 Run 选定的 Environment。新 subagent 继承捕获的 Environment，恢复的 subagent 则保留各自对话配置。切换模式不会复制或隔离 Project 文件或对话历史。

打开 **Configuration**，可以区分保存的对话默认值和 Run 捕获的 Environment。不可用的自定义 profile 仍显示，必须明确替换。尚未发送的新对话草稿在重新加载后仍保留选择；已有对话的覆盖则只属于当前标签页。

### 下次 Run 的 Thinking 设置

打开输入框中的 **Agent & Model settings → Thinking**。**Default** 显示选定模型配置的 thinking，并原样继承其设置。菜单来自服务器提供的模型相关控件：effort 等级和 token 预算预设取决于模型及已安装适配器。只有支持时才显示 Off；最低 effort 不一定等于 Off。未知模型保留 Default，并解释为什么不能覆盖。

显式选择仅属于当前标签页，随下次 Run 发送，不随引导发送。改选 Agent 或模型会清除它。它不修改模型设置，也不保存对话偏好。正在执行的工作保留捕获的选择，可在 **Configuration** 中查看该 Run 请求的 thinking。Thinking 控件不改变输出 token 上限。预算被禁用或与自定义设置冲突时，必须在模型配置中解决，不会静默降低或忽略。

## 当前浏览器中的新结果

在当前浏览器中打开或启动的对话会自动被关注。当 Run 成功保存了尚未阅读的结果，侧栏对应行会显示 **New result** 圆点。每个 Project 分组展示 **Running** 、**New results** 和 **Recent** ；未读结果即使不在五个最近对话中也能找到。折叠的 Project 显示有新结果的对话数。多次完成只按每个对话计一次，之后运行或失败的工作不会清除未读的成功结果。

当保存的对话在获得焦点的浏览器标签页中可见，并且你滚动到历史底部时，圆点才会清除。仅选择对话或收到实时文字还不够。阅读较旧快照不能清除更新的结果。归档对话的圆点显示在 **Archived** 中，不计入普通 Project 数量。

这些提醒只属于当前浏览器和站点地址，通过 IndexedDB 在它的标签页之间共享。重新加载或打开 WebUI 时，会独立于侧栏分页刷新已关注的对话，包括浏览器关闭或服务器重启期间保存的结果。首次访问将已有结果视为历史；其他参与者未被你打开的对话不会全部标成未读。清除站点数据会移除关注和阅读状态。浏览器存储或刷新失败时，会明确说明限制并提供重试，已有提醒仍可见。

这不需要桌面通知权限，也不增加关闭页面后的推送。下面的 **Task notifications** 是独立机制；重新打开恢复的是圆点，不会重放旧通知横幅。

## 任务通知

**关闭 WebUI 或锁定手机后，后台通知仍可到达 Android Chrome。** 请使用稳定的 HTTPS 地址，包括日常登录使用的同一端口。Harness UI 服务器必须保持运行，并能向外连接浏览器的推送服务。无需单独注册推送账户或手动配置 VAPID key。

1. 在需要接收提醒的设备上，打开 **Settings → Notifications**。
2. 选择 **Allow notifications** 或 **Enable background notifications** ，出现浏览器提示时允许。最初的 **Enable task notifications** 提示也可完成设置。升级后，仅有原先的浏览器权限并不会启用后台交付。
3. 确认 **Background delivery** 显示 **Enabled on this device** ，再选择 **Send test notification** 。
4. 保持一个 WebUI 页面可见，使设备被标记为活跃。
5. 发送一条提示词。
6. 关闭 WebUI，等待系统通知。点击通知会打开对应对话，可能仍需正常登录。

每台设备都需要独立启用。Android 上同时允许此站点通知和 Chrome 系统通知。强制停止 Chrome、省电限制、勿扰模式、断网或厂商推送服务不可达，都可能阻止或延迟提醒。iPhone 或 iPad 需要 **iOS/iPadOS 16.4 及以上，并使用主屏幕 Web App**，普通 Chrome 或 Safari 标签页不支持。在 Chrome 中选择 **Share → Add to Home Screen → Add** ，再从图标打开 Harness UI，并在应用中启用通知。如果 Chrome 没有此操作，在 Safari 中打开相同地址并添加。在设备通知设置中管理主屏幕应用的权限；只修改 Chrome 应用权限不会启用网站推送。各浏览器支持不同，远程普通 HTTP 不受支持。本地浏览器开发可使用回环地址。

通知覆盖**所有根对话** 的完成、失败和输入请求，包括此设备上从未打开的对话。预览来自真实回答、问题或失败，不会额外调用模型。**预览可能显示在锁屏上。** 归档对话不产生推送。系统通知只发送到已主动启用、且最近**六小时** 内活跃的设备。保持任意 WebUI 页面可见，大约每分钟更新一次此窗口，无需键盘、鼠标活动或页面焦点。六小时没有可见页面后，重新打开 WebUI 即可恢复接收资格。

每个打开的页面还会显示应用内通知，包括正在查看的对话。这些通知不受六小时或访问历史限制。系统推送和应用内通知可能同时出现。**Web Push 是唯一的系统通知路径**：如果不支持或无法连接 provider，就只剩应用内通知，没有本地系统通知后备机制。

升级后，请重新加载此前打开的 WebUI 页面。现有订阅和签名密钥保留，但旧 Thread 关注列表移除。更新后的可见页面首次报告活动时，设备才恢复接收资格；仅同步注册不算活动。

如果启用时报告 **Browser push registration failed**，表示浏览器在将订阅保存到 Harness UI 之前就未能取得订阅。错误保留浏览器原始详情，并将此阶段与服务器交付区分。Android Chrome 的推送服务错误，应检查 Google Play 服务和设备到推送服务的连通性，包括 VPN 或防火墙限制。能打开 WebUI 不代表推送可达。比较 Wi-Fi 与移动数据，再明确重试注册。不要一开始就清空浏览器存储，因为这也会删除私人草稿和偏好。

测试报告的是推送服务是否接受消息，**不是设备是否已经显示通知**。如果没有提醒，检查站点权限、系统通知设置和网络。macOS 上还应检查 **System Settings → Notifications** 和 **Focus** 。**Enabled on this device** 只代表订阅已同步，不代表交付已确认。保存的订阅最初显示 **Checking subscription…**；刷新或测试失败则显示 **Subscription needs attention** ，不会继续声称已启用交付。回到前台或网络重连时会重试同步。**Reconnect background notifications** 替换已拒绝或过期的 endpoint，并修复服务器签名密钥变化。注册在服务器重启后保留，但连续 90 天没有刷新注册或活动会过期。这与六小时交付窗口是两回事。

关闭 **Enable browser notifications** 会移除此浏览器订阅并停止权限提醒，不影响应用内通知或其他设备。退出登录也会尝试清理。如果浏览器和服务器两端清理都失败，可以在浏览器站点通知权限中阻止交付。清除浏览器存储不等于服务器取消订阅；可行时应先关闭通知。

推送采用尽力交付，不是持久通知收件箱。服务器使用有界内存队列和有限重试，关闭或 provider 故障可能丢失提醒。重新打开 WebUI 不会重放旧的完成事件。推送不改变已保存历史、浏览器本地新结果圆点，或 Agent 工作的生命周期。请安全备份服务器数据根目录，以保留签名身份和订阅。

## 整理 Project 并在对话中工作

**Projects** 标题旁的 **Add project** 用于保存名称和服务器目录，可另加根目录。它只创建 Project，不会创建空对话。展开 Project 可见最近更新的五个根对话；**Show more** 只加载该 Project 的下一页，折叠后仍保留已加载分页。**Without a project** 和 **Unavailable projects** 保留未分配对话，以及引用了已移除 Project 的对话入口。

拖动 Project 标题旁的手柄可移动整组。键盘操作时，聚焦手柄，按 Space，使用上下箭头，再按 Enter 保存或 Escape 取消。Project 菜单也提供 **Rename project**、**Move project up**、**Move project down** 和 **Reset project order in this browser**。重命名只改显示名称，不改变 Project ID、目录或对话；先保存或放弃已有设置编辑。默认优先显示首个目录与服务器当前工作目录相同的 Project。手动排序优先，重置恢复默认顺序。设置时新生成的 Project 使用文件夹名，不使用泛化标签，已保存名称保持不变。排序和展开状态只记在当前浏览器，不修改服务器配置或影响协作者。活跃对话位于最近对话上方。操作结束时更新导航活动时间，方便找到刚完成的工作；进度和检查点不会持续重排列表。对话不能手动排序或拖到其他 Project。搜索查询所有已保存根对话，包括未加载分页，并临时替代分组。归档对话不会出现在这两种视图中；在侧栏打开 **Archived** 可查找并恢复它们。

**Home** 和各 Project 旁的 **+** 共用一个 New conversation 草稿。点击 **+** 只改变 Project，不清除提示词或显式选择的 Agent、模型和 Environment。文字和选择保存在当前浏览器，导航、重新加载或重新打开后恢复；要重新开始，请删除输入。这是浏览器本地的单一草稿位，不是已保存对话列表，也不支持实时跨标签页编辑。本地文件字节只在导航期间保留：重新加载后，需要移除不可用的附件标记并重新附加。浏览器存储不可用时，警告会要求保持标签页打开。只有点击 **Send** 才创建服务器 Thread。收到肯定的提交回执后才清除持久化的新对话草稿；结果不确定时保留输入并要求检查，绝不自动重发。创建结果未确定前，Project 选择保持锁定。直接 Thread 链接打开已保存对话。如果选定对话比已加载分页更旧，会用带标记的选中行保持可见，无需加载中间所有页面。每个对话的 **…** 菜单包括 **Star conversation**、**Rename conversation**、**Share conversation**、**Conversation details**，以及 **Archive conversation** 或 **Restore conversation**。Project 标题菜单中的 **Project settings** 修改 Project 本身，不修改对话。归档对话保留历史，可以恢复。

共享 CodeMirror 编辑器显示协作者光标和同步状态。Enter 发送，Shift+Enter 换行，Ctrl+Enter 和 Cmd+Enter 也可发送；输入法组合输入不会发送。紧凑编辑器保持固定高度，长提示词在内部滚动。已接受的引导以可关闭状态在消息流中显示五秒，不显示在编辑器内。手机上编辑器从一行高度开始，保持在可见视口内，仍使用同一个共享编辑器。只有本浏览器的待处理编辑同步完成后，**Send** 才启用。**Attach files** 、剪贴板图片和拖放文件，会在光标或放置位置插入不可拆分的文件名控件，与文字共同排列。图片使用经过认证的 Thread 字节生成缩略图。Backspace/Delete、选区、同一草稿内剪切粘贴，以及撤销重做，都保留附件身份；把可见标签作为普通文字输入或粘贴，不会附加文件。上传位置立即预留，待上传或失败会阻止发送；点击失败项可在原标签页重试，也可移除。上传和不可变的捕获上下文都限定于 Thread，协作者可读取元数据和下载原始字节。点击编辑器附件可查看保留的文本或放大图片。提交的输入在实时和保存消息中保持原有文字、附件顺序；展开紧凑附件控件可看元数据和原始字节。不会自动加载远程媒体 URL。肯定回执只清除已提交的快照，快照之外的编辑仍保留。确认不确定时保留输入，明确发起新的提交前必须检查，没有任何自动执行重试。

Run 活跃时，**Next message** 仍可编辑，但不会成为队列。**Steer**（Run 活跃时的 Send 按钮）发往当前操作，不创建新轮次。引导与普通 Send 一样保留有序文字和附件。**Stop** 针对当前显示的准确回执。关闭页面只停止观测，不停止执行。问题、审批（包括允许的参数覆盖）和外部结果请求都有完整响应集控件；过时或竞争的决策会刷新，不会显示成第二次成功。问题回答后，简短标题和记录的答案仍可见，包括多选和自定义文字。展开 **Questions & details** 可重看完整问题和选项。

替换历史加载或失败期间，已保存的 transcript 页面仍可见。在替换后的保存历史到达之前，实时输出只是临时展示；流结束本身不能证明续接已保存。推理、工具活动、媒体、上下文操作和诊断分别显示。Agent 文字、推理和摘要支持 Markdown 表格、语法高亮代码和 Mermaid 图。Summary 和 Compact Summary 默认折叠。工具生成的媒体不被当作用户编写的轮次，真实上传媒体仍可见。失败操作在输出区显示简短原因，较长详情折叠。**Retry** 将 `Continue completing the previous task.` 作为普通新轮次发送，不改动当前草稿和附件。它不恢复失败的 Run，也不重放历史；提交待处理或确认不确定时保持禁用。除非用户要求减少动态效果，运行中的对话图标会显示动画。

相邻工具活动默认折叠：**Explored** 归组文件读取和查找，命令与进程观测归入 Shell，网页搜索与页面读取归入浏览组，**File changes** 汇总修改文件。Agent 文字和不同活动类型仍是分组边界。展开后可检查每次操作的详情、输出、格式化或原始参数和结果，以及复制控件。已应用的编辑在实时输出和保留历史中显示观测到的前后 diff，保留完整前后内容，没有单次编辑或 Run 级预览省略限制。展开详情在限制高度的块内滚动。旧版本省略的内容明确标为不可获得，旧的替换参数标为 **Requested replacement** ，不会冒充已应用 diff。失败、拒绝、中断和缺失结果在折叠摘要中仍可见；**Awaiting result** 只表示参数完整。**Open on host** 会明确查找 WebUI 服务器或容器上的绝对路径，同时保留私人编辑缓冲区。它不解析相对路径，也不将 Agent Environment 映射到 Host；记录内容可能与打开的文件不同。

**Details** 分开显示根操作、子执行、任务/笔记/用量、捕获配置和下次 Run 选择。模型执行、续接发布和 Environment 清理具有各自结果。子执行的审查和控制使用准确的父执行。每个子执行检查面板显示易读活动和最新完整保存结果，长结果按准确来源分页。活动预览始终有界，并非完整历史事件日志。旧版本截断的结果无法重建。应用 Project 默认值必须先预览前后差异，并使用已审阅的 Thread 版本和摘要。配置竞争时保留本地选择编辑并要求再次审阅，不会静默替换编辑基准。

草稿协作只存在于当前 App 实例。应用内导航保留浏览器编辑器状态，重新连接同一实例会再次同步。服务器重启后，需要明确加入替代草稿，并决定是否恢复本浏览器的文字。本浏览器尚未同步的编辑会在重新加载或浏览器崩溃时丢失；已同步的文字在服务器运行期间保留在共享草稿中。显示名称不是认证身份，撤销仅属于本地编辑器，已接受的 Send 建立新的撤销边界。

### Skill 与工作检查

输入 `$` 加 Skill 名称，可查看可用 Skill 和说明。上下箭头选择，Enter 或 Tab 插入名称，Escape 关闭列表。接受补全不会发送提示词。引用保持为可编辑的普通 `$name` 文字，适用于新对话、已保存对话和引导。识别的引用会针对当前或活跃 Run 的目录校验，未知名称保留为普通文字，不新增 slash-command 接口。

输入框上方的 **Tasks**、**Notes** 、**Subagents** 和 **Processes** 打开浮动检查面板，不改变草稿或对话。Processes 显示最后观测到的后台命令及状态，可展开进程 handle、Run 标识和退出码。数量只统计观测到的运行中 handle，不代表服务器上的全部进程，前台 shell 调用不计入。Run 完成或观测丢失时，未完成 handle 标记为不可获得，不会认定进程已退出；缺口和有界省略会明确展示。最多显示保留的 128 条观测中的 16 条。重新加载或切换对话不会从保存消息重建进程。输出仍在工具详情中，面板不能停止或控制进程。

## Agent 协作与 Sidekick

WebUI 根 Agent 的指令直接包含当前 Thread ID 和捕获的 Project ID、根目录，无需发现调用。它们也可发现已配置的 Project、Agent 和模型，通过 `get_thread()` 检查对话，并启动独立工作或向其发送引导。协作工具行使用易读的操作说明；创建、继续、引导和发消息涉及的对话都有内联链接，点击导航不会启动新 Run。这些是调用同一 App 的 Harness UI 工具，不是 shell 命令、API key 设置或 Skill。资源发现报告已接受配置，不代表模型连通；模型凭据和原始配置会省略。

`create_thread` 接受已配置的 `agent_id` 和 `project_id`：省略 Project 或使用 `"current"` 保留来源 Project，使用 null 不绑定 Project，或选择其他已配置 Project ID。Sidekick 关闭且未显式选择 Agent/Project 时，会保留来源对话设置，包括默认模型；否则按选定 Project/Agent 解析普通默认值。新对话收到初始提示词、请求方 Thread 和捕获的 Project，以及明确要求通过 `send_thread_message` 汇报澄清问题、阻碍和最终发现、改动、验证的指令。请求方 Agent 使用同一工具回答，带来源的消息说明应该回复哪里。新对话不会收到来源完整历史的副本，因此要提供必要上下文。可选 `model_id` 只覆盖首个 Run 的模型，不编辑选定 Agent；`run_thread` 也接受此参数，用于后续明确发起的轮次。创建工作不会形成子 subagent 关系。

问题和报告都遵循同一套 `send_thread_message` 交付规则：活跃目标收到针对准确操作的引导尝试，空闲目标则启动新轮次。准备中或已经结束的操作可能拒绝引导，空闲目标的准入也可能与其他发送者冲突。拒绝引导后绝不会自动启动替代操作。归档对话和子 Thread 不能接收这些消息；空闲目标有待决策请求时，必须先解决才能启动。结果只代表接受，不代表已处理完成或已保存交付。没有离线队列或自动重试；重复不确定的操作前，先检查目标。

Sidekick 是一项 WebUI 偏好，为 Agent 通过 `create_thread` 创建的对话设置默认 Agent、模型和指令；它默认启用。**Settings → General → Sidekick** 通过[根配置](configuration.md#webui-sidekick)选择偏好的 Agent 和/或默认模型。省略配置表示启用，显式 `sidekick: null` 保持关闭，升级后也一样。Agent 选择 **Inherit current agent** 可只使用 Sidekick 模型；Model 选择 **Use agent model** 则保留选定 Agent 的模型。`create_thread` 创建独立工作时，即使工具参数没有明确指定 Agent/Model，Harness UI 也会应用这些默认值。配置模型保存在新对话上，后续消息、Send/Retry 和恢复都使用它。指令涵盖独立工作、上下文、汇报和结果验证。它不会自动启动 Agent，也不引入独立执行生命周期。选择 **Disabled** 移除偏好，通用协作工具仍保留。保存影响未来 WebUI Run 的捕获及由其创建的新对话；当前 Run、已有对话默认值和委派子 agent 不变。

对话配置中的 **Default model** 可独立于输入框 Run 选择器设置持久模型；选择 **Follow Agent model** 清除它。遵循保存的默认值时，选择器显示 **Thread default** ；改选模型只影响该 Run 草稿。配置检查会区分下一次的模型与当前或保存 Run 实际捕获的模型。

## 保存的输出与评论

WebUI 评论创建、选区操作、高亮、菜单和讨论面板目前停用，等待重新设计。这不会删除后端评论或 API。此变更前捕获的反馈引用，在消息和草稿中仍可读取。子执行保存结果仍可从检查面板读取，不提供评论控件。

## 读取、编辑与捕获 Host 文件和 Git 变更

右上角 **Files** 或 **Changes** 图标打开右侧抽屉。宽屏上 Chat 仍在旁边可见。选择文件后编辑器在抽屉内打开；**Back to files** 返回所在目录，**Back to changes** 返回变更列表。文件标签页留在抽屉中，显示未保存状态，切换文件时记住选区和滚动。拖动分隔条或使用箭头键调整宽度，当前浏览器会记住。**Expand drawer** 提供更宽代码视图。查看后返回，已加载路径筛选和列表滚动保持不变。标签页内小型缓存记住最近 Project 目录、标签页和选定终端。关闭抽屉让焦点回到 Chat，不丢弃缓冲区。小屏每次只展示一个工作区，但同一对话和草稿始终保持挂载。Files 和 Changes 自动使用当前对话的 Project，从首个根目录开始；额外配置的根目录显示为紧凑文件夹导航。**Up** 返回父文件夹，到 Project 根目录时禁用。可见路径标记当前文件夹，点击任意上级（包括 Project 名称）可直接返回。文件夹导航位于列表和文件编辑器上方，没有单独的目录输入表单。无 Project 对话会提示打开一个 Project 对话，不会静默选择其他 Project。这些路径属于服务器或容器 OS 账户，不属于浏览器或 Agent 的远程 Environment。

Files 支持目录分页、文本编辑、上传下载、创建、重命名移动、显式替换上传和确认删除。常见源码语言支持高亮，**Open on host** 打开的内容也一样。可以使用 **Wrap**，用 Ctrl/Cmd+G 跳行、Ctrl/Cmd+S 保存。筛选只覆盖当前目录已加载路径，不递归搜索 Project。文本编辑器要求完整、无 NUL 的 UTF-8，最大 512 KiB；二进制或更大的文件明确显示为不可编辑。上传和整文件捕获最大 10 MiB。原文件下载和音视频播放支持流式读取更大的文件，无需先将整个文件加载为 Blob。常见图片预览仍以 10 MiB 为限。音视频使用浏览器播放控件，不自动播放；编码支持取决于浏览器，解码失败时仍可下载原文件。文件访问链接在 30 分钟后过期，刷新可重新获取。磁盘修订变化后必须刷新，不会静默切换预览内容。本地未保存缓冲区在标签页内导航或替换实例密钥时保留，但重新加载或关闭浏览器会丢失。刷新不会替换未保存文字。其他参与者修改磁盘版本时，应查看最新版本，选择 **Use disk version** 或 **Keep local text** 后再明确保存。写入确认丢失不会自动重试。常见 CRLF/LF 换行保留，混合换行编辑会统一为首次出现的风格，并明确提示。

Changes 分开比较暂存区的 HEAD/index、未暂存的 index/worktree，以及未跟踪的新文件。可以筛选已加载路径、折叠分组，并在已加载比较或 hunk 间移动。选择未暂存或未跟踪 diff 的新文件行，可跳到当前工作文件对应行；不会假定暂存或已删除侧的行与当前文件对应。Unified diff 为旧、新文件分别提供行号栏，采用等宽代码，以及适应浅色和深色主题的添加、删除与 hunk 颜色。标题和缺少结尾换行的提示仍属于审阅的 patch。文字选区记录原始 patch 行号以供捕获，显示的文件行号不会替换这些坐标。仓库、HEAD、index 和 diff 标识仍可检查，包括重命名、冲突、二进制和尚无首个 commit 的状态。Git 错误不会显示成无变更结果，Files 在仓库外也可用。完成原生操作后刷新，或返回面板查看新观测；没有递归 watcher，也不会声称变更属于某次 Run。没有 stage、commit、discard 或 worktree 按钮。

文件使用 **Add to chat**，diff 使用 **Add to message** ，将审阅的版本捕获到共享草稿。先选中行可只捕获该范围。发送前审阅附件卡片；后续文件修改不会改变捕获的字节。也可以用同一附件对活跃 Run 进行引导。

## 共享原生终端

打开 **Terminal**，再选择 **New terminal** ，从当前浏览目录启动。Files 中的 **Open terminal here** 也可从文件夹创建新终端会话，不会向已有终端会话注入 `cd`。面板只显示当前 Project 的终端会话。切换 Project 会隐藏并断开旧视图，但不结束终端会话或改变工作目录。只打开面板不会启动 shell；创建需要 Project 已配置根目录。未绑定 Project 的已有终端会话仍可通过原生 API 使用。显示的初始目录不随后续 shell `cd` 更新，终端执行独立于 Agent 的 Environment。

创建方浏览器在首个认证帧后，会自动请求一次控制权，前提是尚未被其他人占用。服务器确认所有权前，输入和调整尺寸保持禁用，确认后输入获得焦点。其他参与者看到 **Viewing only**，可选择 **Take control** 或 **Take over input** ，这些操作比较当前观测的控制 epoch。**Release control** 保留终端会话运行，供其他人查看。控制方尺寸跟随真实面板，查看方保持共享尺寸，并可在窄视口中滚动。Shell 控制键留在终端中。输入为有界 UTF-8，浏览器每次粘贴限制 16 KiB；当前文本输入协议不支持旧式二进制鼠标报告。

**Disconnect**、打开 Settings 或折叠面板，都只释放当前连接，不关闭进程。重新打开会自动以只读查看者重连；如果明确选择过 **Disconnect** ，则需点击 **Reconnect** 。同一 Project 内切换时最多保留三个最近屏幕。重连不会重复按键或自动取得控制权。服务器最多保留 1 MiB 原始字节，终端模拟器保留 2,000 行本地滚动历史，不是持久日志。输出有缺口时会重置解码，并说明屏幕由保留的原始输出构建，并非完整屏幕快照。渲染过慢时暂停连接，不积累无界输出队列；排空后可重连，缺失字节可能已经不可获得。

**End session** 需要确认，因为它会为所有人关闭共享终端会话及其作业。已经退出的终端会话在关闭前仍可检查。创建或关闭结果不确定时不会自动重放，先刷新终端会话列表再决定后续操作。App 重启不恢复或重建终端会话。POSIX Host 支持原生 PTY；Windows 明确报告不可用，Files 和 Git 则仍可独立使用。

通过 **Find in output**（Ctrl/Cmd+F）、前后匹配和 **Copy selection** 检查保留输出。HTTP 链接单独打开；明确的绝对 `path:line` 引用打开 Host 文件。**Add selection to message** 将带来源的片段追加到当前共享草稿，不会发送；**Return to conversation** 聚焦该草稿。不会猜测相对或有歧义的终端路径。

Shell 修改文件或 Git 后，返回 Files/Changes 或刷新原生观测。原生操作、焦点返回和 Run 完成也会刷新观测，不替换未保存缓冲区。刷新观测时，选定路径和私人未保存文字保持不变。浏览器标签页标题跟随当前对话，其他页面使用 `a13n harness ui`。抽屉中的 **Share explorer view** 链接图标生成不含实例密钥的同实例浏览链接。参与者目录也可打开准确聚焦的文件或 diff；它报告聚焦的对话、原生文件/diff、终端或配置页，**Open page** 会明确打开可用的其他参与者视图，不会启用持续跟随或移动别人。后台标签页与前台关注仍可区分。

## 认证与密钥保存

启动时 stdout 输出普通 URL；只有生成的实例密钥才会额外输出密钥和便捷的 URL fragment 登录链接。浏览器读取并移除密钥 fragment，通过 API 请求的 `Authorization: Bearer <key>` 发送，成功使用的密钥保存在同 origin 的 localStorage 中。**Log out** 移除保存的密钥并关闭受保护视图。静态资源不含密钥，也不需要认证。

密钥优先级为 `--apikey`、`A13N_HARNESS_UI_API_KEY`、新生成的进程密钥。提供的密钥不会回显，但命令行参数仍可能对 shell 和操作系统可见。显式空密钥和重复传入的冲突值会被拒绝。`--dangerous-skip-permissions` 只关闭 Web 认证，不关闭 Agent 权限或计算机共享检查；与 CLI 或环境密钥同时使用会报错。`--api-key` 和 `--dangerously-bypass-permission` 仍是兼容别名。

## 反向代理与公开地址

使用公开域名或终止 TLS 的反向代理时，将外部地址添加到选定的 `a13n-harness-ui.yaml`，再重启 WebUI：

```yaml
webui:
  allowed_origins:
    - "https://anui.wh1isper.top:8090/"
```

协议、域名和端口必须一致。这只允许该地址，不会同时允许 HTTP、443 端口或其他域名。显式允许任意地址可用 `allowed_origins: ["*"]`；默认 `[]` 保留原有监听地址限制。两种设置都不放宽 API 认证，也不允许跨 origin API 访问。校验和重启行为参阅[允许的 origin](configuration.md#webui-allowed-origins)。

反向代理必须保留外部 `Host`（包括 `:8090`）和浏览器 `Origin`，通过 `X-Forwarded-Proto: https` 转发原始 HTTPS 协议，并支持 WebSocket 升级。不要把这些头改成内部回环地址来绕过准入。Harness UI 使用 Uvicorn 的代理头处理；如果代理不是从默认可信的 `127.0.0.1` 连接，在 WebUI 进程环境中用 `FORWARDED_ALLOW_IPS` 设置可信代理的 IP 或网段。只信任实际代理，并要求代理覆盖客户端传来的转发头。除非每个可能连接的对端都可信，否则不要使用 `FORWARDED_ALLOW_IPS=*`。容器中，对端指 WebUI 容器看到的代理地址，不是浏览器地址。

Device 配对返回相对于根的批准和连接路径。daemon 针对已配对的 Host URL 解析它们，保留外部 HTTPS 协议和端口，不使用代理内部地址。配对批准仍不代表 Device 已连接或获得桌面权限。

## 监听器与应用生命周期

监听非回环地址，会向可信网络开放共享实例权限，不提供租户隔离；需要时使用外部 TLS。即使没有浏览器，服务器也负责 App 生命周期，Ctrl+C 或 SIGTERM 会关闭它。无需认证的 `/healthz` 和 `/readyz` 分别报告有界存活状态和 App 就绪状态。新实例即使尚未配置模型，也可处于可进行设置的就绪状态。

TUI 和 WebUI 进程可在兼容的软件包升级之间共享同一本地数据库。仅有更新的迁移版本，不会拒绝较旧但兼容的读取方，也不会阻止活跃 Run 保存。较旧 App 保留不认识的新迁移历史，检查自身所需表和列仍可用。存储缺失，以及真实的续接或版本冲突，仍会明确失败；schema 兼容不代表共享实时执行所有权。已发布二进制仍使用自身启动检查，不兼容的 payload 格式也不能仅靠放宽修订版本校验就变得可读。

## 容器与安装资源

GHCR 镜像为 `ghcr.io/converge-ai-labs/a13n-harness-ui`：`dev` 跟随 main，正式发布使用 `X.Y.Z`，RC 使用 `X.Y.Z-rc.N` 且不更新 `latest`。Python 和页面将 RC 元数据显示为 `X.Y.ZrcN`。开发构建显示源码版本 `0.0.0`，另列 Git 修订。需要持久配置、数据和工作挂载，并仅向回环地址发布端口时，使用仓库的 `deploy/docker/compose/a13n-harness-ui.yaml`。镜像以 UID/GID `10001:10001` 运行，bind mount 必须允许该账户写入。需要保留数据时，不要移除卷。重启会更换生成的实例密钥；需要稳定的实例密钥时，在运行时提供 `A13N_HARNESS_UI_API_KEY`。

WebUI 资源随 wheel 发布，最终用户无需 Node.js 或独立前端 checkout。仓库开发使用 `make webui`：构建并安装打包资源，再以 `var/harness-ui/` 下隔离的配置和数据启动前台服务器。无需手动准备实例密钥；未提供 CLI 或环境密钥时，stdout 会输出可直接登录的链接，认证仍然必需。使用 `make webui WEBUI_ARGS='--port 9000 --no-share-computer'` 转发服务器选项，`CLI_ARGS` 将全局选项传到子命令之前。配置初始化与环境覆盖参阅[开发指南](https://github.com/converge-ai-labs/agent-foundation/blob/main/dev/harness-ui/README.md)。

## 选项与职责

监听器、认证和兼容别名参阅[已注册的 webui 选项](command-reference.md#webui)。绑定地址、端口、认证和原生共享选项是进程参数；额外允许的请求 origin 通过根 YAML 的 `webui.allowed_origins` 配置。运行中的服务器持有活跃 App 工作；关闭标签页不会停止服务器，它也不是独立后台 worker 服务。

API 客户端请遵循 [HTTP 工作流程与路由参考](http-api.md)。进程内接入使用 [Python App](embedding.md)。不需要 WebUI 服务器的自动化，使用[单次执行](automation-and-troubleshooting.md#automation-and-diagnostics)。

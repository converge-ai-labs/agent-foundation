---
title: 连接器
description: 为 WebUI 对话和 Bot 统一连接服务账户。
---

连接器为 WebUI 对话和 [Bot](bot.md) 提供实例级账户和工具。每个连接都有独立配置、私密凭据和状态。同一种类型可以连接多个账户。终端对话、Live 和子 agent 不会自动获得连接器工具。

## 连接 GitHub

1. 打开 **设置 → 连接 → 连接器**。
2. 选择**添加连接 → GitHub**，为账户命名，再选择**继续**。在继续之前取消，不会创建账户。
3. 如果还没有令牌，打开**创建个人访问令牌**。按你要授权的工作选择仓库和组织权限。
4. 输入令牌并选择**保存连接**。保存后的页面提供**编辑连接**、**测试连接**和**完成**。
5. 选择**完成**，然后主动发送新消息以使用连接。已有运行仍使用先前捕获的工具。

连接页首先列出已保存账户，分别显示就绪状态。如果新账户的配置尚未完成就离开，可以选择**保留以便稍后继续**或**移除连接**。保留的账户可以**继续配置**。

令牌类型、组织审批和 GitHub 权限决定哪些 GitHub 操作可用。保存只写入本地配置，不会连接 GitHub 或验证权限。在保存后的页面或账户菜单选择**测试连接**，即可明确发起连接检查。GitHub 的连接细节由 provider 内置；配置时只需填写令牌。

令牌字段留空会保留已保存令牌。**已保存**提示表示已有令牌；**移除已保存令牌**会删除它。不能同时提交替换令牌和移除操作。凭据以明文保存在此实例的私有数据库中，由 Host 文件系统访问权限保护。不要把令牌粘贴到聊天、Agent 指令或工具调用中。

GitHub 连接器提供 MCP 工具，不轮询通知、不自动向 Bot 提交输入，也不实现单独的 GitHub OAuth 应用。

## 连接器与 MCP 服务

连接器展示 GitHub 等具体服务，账户配置由该服务的 provider 定义。添加连接菜单列出已安装的具体服务集成。

任意 MCP 服务仍通过独立的 [MCP 服务](mcp.md)设置配置，是由 Agent 选择的资源，也可在 TUI 中使用。连接器 SDK 的 Remote MCP 能力是供开发者实现具体服务集成的代码库，不是另一套通用 MCP 配置界面。

## 从对话中配置

agent 可以提供连接配置卡片。邀请本身不接收凭据，不创建账户，也不启动配置会话。选择已有账户或**添加其他账户**，再打开与设置页相同的配置对话框。表单不会在对话正文内展开。账户就绪后，卡片变为紧凑的确认状态，并提供**管理连接**链接。

普通对话卡片显示在折叠工作记录之外，包括通过 CodeAct 发出的邀请。Bot 在正常对话中主动发布配置卡片，不展示内部工具流。共享访客可以查看被主动发布的卡片，但不能配置实例。

配置成功只更新已保存配置，不启动运行，也不重放先前受阻的操作。如果希望 agent 使用连接，请主动发送新消息。

## 修改、禁用或删除

账户菜单的**管理**会打开只读连接摘要、已保存凭据提示和通用账户设置。**编辑连接**打开独立配置对话框；账户菜单也提供**重新配置**。名称用于区分账户。禁用会停止新操作并保留待处理的外部输入，不会撤销上游写入。删除会清除连接的私密凭据、状态和待处理输入。已删除 ID 不可复用。

如果配置流程或另一个浏览器修改了已保存设置，未编辑的设置对话框会自动更新。已编辑的草稿会保留并显示冲突提示；保存前请选择**重新加载已保存设置**。关闭含未保存修改的对话框前会询问是否丢弃草稿。私密配置字段会在提交时清空。操作响应不确定时，先**刷新状态**再执行其他操作；客户端不会自动重放该操作。配置会话会过期，App 重启后也不会保留；已保存配置不受影响。

同一数据根目录只有一个 WebUI App 拥有 Bot 和连接器运行时。另一个 App 可以查看已保存状态，但不能操作账户。插件缺失时，连接数据会保留并标记为不可用。

## 事件规则与 Bot 投递

插件可以声明事件、条件和可选的**将外部输入交给 Bot**能力。设置页把插件条件渲染成结构化字段。由插件判断匹配，Host 不解释通用规则语言。

采集和匹配独立于 Bot 的激活与暂停状态。已接受输入进入 Bot 共用的有界收件箱。Bot 关闭、暂停、缺少 Model 或等待人工决定时会保留输入。修改规则保留已接受的匹配；禁用会暂停，删除会清除。GitHub provider 不声明事件源。

## 开发可信连接器

从 `a13n_harness_ui.connectors` 导入公开 SDK。`Definition` 提供安全声明、Pydantic 配置/条件校验器，以及产出 `Controller` 的异步上下文。Controller 提供状态、可选配置流程、原生 Capability/capture、服务和按配置代际运行的采集器。通过 `HarnessUiIntegrations(connectors=(definition,))` 显式注册，或在 Python entry-point 组 `a13n_harness_ui.connectors` 下发布无参数工厂。

```toml
[project.entry-points."a13n_harness_ui.connectors"]
example = "example_connector:Example"
```

通过绑定的 Context 访问私密凭据、作用域状态、原子状态/输入提交和类型化共享服务。外部 I/O 在短事务提交之前完成。自行在状态文档中保存编码版本。保留匹配事件时，把输入和游标一起提交；提交被拒绝时不能推进游标。遵守捕获的配置版本和 Bot 投递策略。

表单支持对象、字符串/密码、布尔值、数字、枚举、可空字段、数组/重复行以及无环本地引用。秘密配置字段使用 `writeOnly`。配置 schema 根节点的 `readOnly: true` 保留账户摘要展示，并把编辑交给配置流程。可选表单提示包括：`x-a13n-saved`（根节点 JSON Pointer 路径数组，用于标识已保存的秘密字段），以及字段级显示文案 `x-a13n-null-label`、`x-a13n-selected-label`、`x-a13n-empty-label`、`x-a13n-add-label`。这些提示只携带存在状态和文案，不能包含凭据值。不要在状态、上下文、capture、配置值、日志或模型可见输入中返回秘密。不支持的 schema 会标记为不可用，不会降级为原始 JSON 编辑器。

Controller 持有共享资源，直到已接纳的根运行完成清理。原生工具仍是每次运行的 Capability。后台采集器在配置变化后重启。配置 action 必须显式执行并串行处理；读取只观察当前 Step，可选流程 worker 用于推进外部等待。取消后关闭流程资源。插件是可信 Host Python 代码，不是沙箱脚本。

### 用 Remote MCP 实现具体服务

继承 `a13n_harness_ui.connectors.remote_mcp.RemoteMcp`，复用原生工具和类型化编程访问。这是 Controller 基类，不是已注册的 Definition。你的 Definition 负责具体服务的名称、配置模型和配置流程，其 `open()` 产出你的 Controller。实现 `connection(configuration)`，返回安全的 `ResolvedMcpRecipe`，账户配置未完成时返回 `None`。服务地址与传输策略由代码确定，私密请求头使用 `McpPrivateValueSource` 引用，不能把凭据值放入 recipe。

代码库提供原生 MCP capture、Capability 创建、凭据解析，以及配置代际与启用状态检查。provider 可以在 recipe 中选择原生工具（`None` 允许全部，空元组不允许任何工具），无需在前端展示 MCP 传输参数。[GitHub provider](https://github.com/converge-ai-labs/agent-foundation/blob/main/packages/a13n-harness-ui/a13n_harness_ui/connectors/github.py)提供了具体实现。

Controller 同时发布版本 1 的类型化 `mcp` 服务，不导出凭据：

```python
from a13n_harness_ui.connectors.remote_mcp import RemoteMcpService

service = context.service(
    "connector-account-id", "mcp", api_version=1, expected_type=RemoteMcpService
)
tools = await service.list_tools()
```

provider 在内部检查启用状态、当前配置和工具选择。Agent 使用的 MCP 工具仍走原生传输与 ToolSearch 路径。[可运行连接器示例](https://github.com/converge-ai-labs/agent-foundation/tree/main/examples/harness-ui-connectors)演示了声明、配置流程、插件侧规则匹配和原子输入提交。

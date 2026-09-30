---
title: 交互式 MCP Apps
description: 在浏览器对话中显示可交互的 MCP App 结果。
---

助手结束后，MCP App 仍可通过 MCP 服务器的现有连接继续工作。此功能属于 WebUI，不会安装浏览器控制服务，也不会让 Agent 访问你的浏览器。

## 为指定服务器开启 Apps

先在 `mcp/` 下[定义 MCP 服务器](mcp.md)，再将其确切资源 ID 加入根 YAML：

```yaml
webui:
  mcp_apps:
    enabled: true
    servers: [mcp-counter]
```

Apps 默认关闭。WebUI 会将选中的服务器加入根级和子级 Agent，不修改其 YAML；现有工具过滤和权限仍适用。CLI 继续只使用普通 `mcp_servers` 选择。启用 Apps 或修改沙箱监听后重启 WebUI。App 展示失败时，文本工具结果仍可使用。

服务器必须提供 MCP Apps `ui.resourceUri` 元数据和 `text/html;profile=mcp-app` 资源。旧版 MCP-UI HTML 惯例不是受支持的替代协议。Host 支持内嵌显示、同服务器工具/资源、文本或结构化上下文、消息、外部链接和主题切换。当前不支持图像上下文、App 提供的模型工具、全屏模式或稳定 origin 的浏览器存储。

要运行不需要模型账户的完整本地示例，在源码检出目录运行 `make mcp-apps-demo`。真实 stdio 计数器、公共 App SDK 包和独立服务器说明见仓库 `examples/mcp-apps/README.md`。演示使用脚本化 HTTP 模型，但采用正常的 Host、历史和权限路径。

## 打开并交互

实时 App 显示在工具结果旁。已保存历史显示 **Open App**，不会自动执行 HTML。打开时显示保留的原始展示，不会重做工具调用或连接服务器。

选择 **Activate interactions**，允许 App 请求同服务器操作。激活和后续请求会检查当前 Agent 选择、工具可见性和权限规则，不会无限期授予旧 Run 权限。子级 App 保留来源身份和当前委派路由；已移除的子级路由不能再提供权限。

App 工具操作不使用模型审查或自定义 Agent 审查器。`review` 权限允许无需模型请求直接派发；显式 `deny` 仍会阻止，`ask` 仍需要你的审批。Agent 发起的调用保留普通审查策略，包括确认 App 消息后发起的调用。

- 工具审批显示在可信 Host 卡片中，位于 App iframe 之外。审批前检查服务器、工具和确切参数。后续策略或凭据变化可能使待处理审批失效。
- **Check result** 读取已有回执，核实结果不确定的操作。它不会重做调用，也不保证未经确认的写入没有产生效果。
- 关闭 View 会终止其控件和待确认请求，不会终止 MCP 服务器连接。已派发的操作仍可能完成。
- 连接没有空闲超时。Run 完成或浏览器断开不会丢弃服务器状态。Host 关闭、显式关闭连接、绑定退役或 Thread 释放会终止连接。服务器崩溃不会静默重启，其业务调用也不会重放。

从 Agent 的通用选择移除服务器，不会移除仍被 WebUI Apps 选择的服务器。从 Apps 设置移除会关闭新 App 交互并退役保留连接；如果独立选择了通用 MCP，它仍可用。变更影响后续 Run 捕获，不影响已受理 Run。

重新加载时，Open App 恢复已保存的原始结果，不恢复之前的交互状态。重新激活以读取当前服务器状态；重启 WebUI 不会恢复服务器私有内存。

## 上下文需要主动选择

App 可将最新文本或结构化值提供为上下文。提交普通输入框消息前，在 Host 卡片中检查并选择。未选上下文不会附加。选择仅属于该浏览器对话中已挂载的 App，不共享给其他标签页或共享草稿。

Send 捕获确切的选中值。后续 App 更新不能改写进行中的提交。选择被替换、丢弃或缺失时会明确失败，不会静默换成更新的上下文。每个值最多 64 KiB，每次提交最多选择八个 View。上下文是带来源标注的外部数据，不是 Host 指令，也不会自动附加到执行指导中。

App 消息是独立提议。检查文本和目的地后，在 iframe 外选择 **Send once** 或 **Decline** 。子级 App 提议交接给所属根对话。根对话忙碌时，消息会失败，不会排队或转为执行指导。发送不会替换输入框草稿。响应不确定时应检查回执，不要再次发送同一提议。

## 外部链接

App 可提议绝对 HTTP 或 HTTPS URL。Host 显示规范化目的地，要求再次点击后，才在没有 opener 的新标签页中打开。含凭据的 URL，以及指向 Host 或沙箱 origin 的链接会被拒绝。App 链接不能导航工作台本身。打开外部链接不要求活动 MCP 连接。

## 本地、Docker 与反向代理 origin

App 在不透明 iframe 内运行，该 iframe 嵌套于独立 origin 的沙箱代理。代理没有经身份验证的 Host API 或登录凭据。不要将它代理到 WebUI origin 下，也不要向其转发 Host 身份验证 headers/cookies。

### 同机开发

沙箱默认绑定 `127.0.0.1` 上空闲的临时端口，适用于浏览器与 WebUI 在同一机器的情况。生成的沙箱 URL 使用该回环地址，其他设备的浏览器无法访问。

### 容器或远程 WebUI

设置固定监听地址和浏览器可达的独立公共 origin。例如 WebUI 暴露为 `https://chat.example.com` 时：

```yaml
webui:
  mcp_apps:
    enabled: true
    servers: [mcp-counter]
    sandbox:
      bind: 0.0.0.0
      port: 8766
      public_url: https://apps.example.net
```

将 8766 端口发布到反向代理网络，并把 `https://apps.example.net/sandbox.html` 路由到该沙箱监听地址。保留查询字符串和响应安全 headers。两个公共 origin 都使用 HTTPS；不要在 HTTPS 工作台中嵌入 HTTP 沙箱。`public_url` 是 origin，不是路径前缀，且必须与 WebUI origin 不同。独立主机名还可避免意外共享域范围的 Host cookie。不要给沙箱主机名设置 Host 身份验证 cookie。

同机本地反向代理应保留 `bind: 127.0.0.1`，转发固定端口即可。明文 HTTP 的本地 Docker 测试应分别发布 WebUI 和沙箱端口，将 `public_url` 设置为浏览器可达的沙箱 origin。WebUI 绑定非回环地址时，必须显式设置沙箱 `public_url`；省略会导致启动被拒绝，不会宣告不可用的内部地址。

| 设置                 | 默认值      | 含义                                                     |
| -------------------- | ----------- | -------------------------------------------------------- |
| `enabled`            | `false`     | 开启 Apps 捕获和 WebUI 托管                              |
| `servers`            | `[]`        | 在 WebUI 中自动加入每个根级和子级 Agent 的 App 服务器 ID |
| `sandbox.bind`       | `127.0.0.1` | 字面 IPv4/IPv6 监听地址                                  |
| `sandbox.port`       | `0`         | `0` 选择空闲本地端口；端口转发应使用固定端口             |
| `sandbox.public_url` | `null`      | 浏览器可达的独立 HTTP(S) origin；远程监听必需            |

安装的 Python wheel 和 sdist 包含沙箱代理和已编译 Host 资源。准备仓库资源需要 Node.js，运行已安装 WebUI 或从 sdist 重建 wheel 不需要。

## 故障排查

- **没有 App 卡片：** 检查 `webui.mcp_apps.enabled` 和 `servers` 选择，重启 WebUI，再调用产生 App 的工具。开启 Apps 不会重建旧历史中缺失的展示元数据。
- **沙箱不可用：** 修复绑定地址、端口冲突或公共 origin，然后重启 WebUI。绑定失败时普通工作台和文本结果仍可使用，但不会降级为同 origin HTML。
- **初始化失败：** App 未在 15 秒内完成公共 SDK 握手。检查浏览器错误和资源/CSP 声明。此超时针对 View 握手，不是 MCP 连接生命周期。
- **配置变更后激活失败：** 当前权限或原始工具契约已不匹配。适当时按当前配置重新调用工具；打开历史不会代你执行。
- **达到操作上限：** 显式关闭并重新打开 View。浏览器最多保留 128 个工具请求，每个最多 256 KiB；不会淘汰尚未决策的任务来接受更多调用。
- **App 需要外部资源或浏览器存储：** 不保证离线显示。仅允许已声明的确切资源/连接 origin，禁止 Host origin 网络访问，此配置不提供稳定的 App 存储 origin。

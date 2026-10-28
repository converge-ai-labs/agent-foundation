---
title: 交互式 MCP Apps
description: 开启 MCP App、操作工具结果，并为远程 WebUI 配置独立沙箱 origin。
---

MCP Apps 将工具结果变成 WebUI 中的交互卡片。例如，Agent 完成后，计数器仍可通过同一 MCP 服务器连接响应点击。这是结果界面，与 [Host 浏览器控制](webui.md#browse-on-the-host)分开。

## 为指定服务器开启 Apps

你需要可用的 WebUI 和能产生 App 的 MCP 服务器。开启此设置不会给普通文本工具自动添加界面。服务器必须提供 `ui.resourceUri` 元数据和 `text/html;profile=mcp-app` 资源；不支持旧版 MCP-UI HTML。

1. 在选中配置目录的 `mcp/` 下[添加 MCP 服务器](mcp.md)，记下确切资源 ID。

2. 将以下片段合并到选中的根 `a13n-harness-ui.yaml`，把 `mcp-counter` 换成该 ID。

   ```yaml
   webui:
     mcp_apps:
       enabled: true
       servers: [mcp-counter]
   ```

3. 使用同一配置选择运行 `a13n-harness-ui config validate`，先修复配置错误。

4. 重启 WebUI，打开对话，让 Agent 调用服务器中产生 App 的工具。

5. 检查普通工具结果旁是否出现 App 卡片。没有卡片时，见[故障排查](#troubleshooting)。

Apps 默认关闭。在 WebUI 中，选中的服务器会提供给每个根级和子级 Agent，无须修改 Agent YAML。现有工具过滤和权限仍适用。TUI 和单次 `run` 使用各自的普通 MCP 选择。

要在无模型账户的情况下试用，先准备仓库的[源码开发环境](installation.md#run-from-source)，再在仓库根目录运行：

```bash
make mcp-apps-demo
```

打开打印的登录 URL，新建对话，发送 `[mcp-app] Open the counter.`。激活卡片，在 Agent 完成后点击 **Add one**：数值应变化，而不产生另一轮对话。Ctrl+C 停止演示并移除临时状态。演示使用脚本化模型和真实 stdio 服务器；构建前置条件和独立服务器设置见检出目录中的 `examples/mcp-apps/README.md`。

## 打开并交互

实时结果可立即显示 App。在已保存历史中，选择 **Open App** 显示保留的原始结果。打开不会重复工具调用，也不会连接服务器。

1. 在 Host 卡片中选择 **Activate interactions**，创建 View，即本次展示的活动控件。
2. 操作 App 控件。同服务器工具和资源请求会检查当前 Agent 选择、工具可见性及权限。
3. 需要审批时，在 iframe 外的 Host 卡片中检查服务器、工具和确切参数，再批准或拒绝。
4. 操作结果不确定时，用 **Check result** 读取已有回执。不要重复可能已经完成的写入。

App 操作的调用遇到 `deny` 会阻止，遇到 `ask` 会要求审批。`review` 允许直接派发，不调用审查模型或自定义 Agent 审查器。Agent 发起的调用保留普通审查策略。子级 App 还要求原委派路由保持选中。

App 请求可能需要 [MCP 人工输入](mcp.md#human-input-from-mcp-servers)。在 iframe 外的 Host 界面回答，以继续该操作。只有完整最终结果才能成为已保存 App 快照。

区分以下生命周期：

| 操作                                       | 结果                                         |
| ------------------------------------------ | -------------------------------------------- |
| 关闭 View                                  | 终止其控件和待确认请求；已派发操作仍可能完成 |
| 重新加载并选择 Open App                    | 显示已保存原始结果，而非最后的交互画面状态   |
| 重新激活                                   | 检查当前权限，允许 App 读取当前服务器状态    |
| 完成 Run 或断开浏览器                      | 保留符合条件的 Host 托管服务器连接           |
| 关闭 Host、关闭连接、退役绑定或释放 Thread | 终止连接；WebUI 不恢复服务器私有状态         |

连接没有空闲超时。服务器崩溃不会静默重启，业务调用也不会重放。移除 Agent 的通用 MCP 选择后，只要 Apps 仍选中该服务器，它就仍然可用。移除 Apps 选择会退役启用 App 的绑定；独立的通用选择按 `mcp.host_owned_servers` 管理。选择修改应用于后续 Run 捕获。

## 上下文需要主动选择

要在提示中使用 App 的值：

1. 使用 App 提供文本或结构化上下文的动作。
2. 在 Host 卡片中检查并选中该值。
3. 编写普通输入框消息并发送。

Send 捕获确切的选中值。选择属于本浏览器标签页对话中已挂载的 App，不共享给其他标签页或共享草稿。未选中的上下文不会进入提示；过期或缺失的选择会明确失败。每个值最多 64 KiB，每次提交最多八个 View。Model 收到的是带来源标注的外部数据。上下文不会自动附加到引导。

App 也可提议独立消息。检查文本和目的地，再在 iframe 外选择 **Send once** 或 **Decline**。子级 App 提议交接给所属根对话。根对话忙碌时拒绝消息，不会排队或改为引导。输入框草稿保持不变。响应不确定时，先检查回执再采取后续行动。

## 外部链接

检查 Host 卡片中的目的地，再点击打开。Host 在新标签页打开已确认的 HTTP(S) 链接，且不允许新页面访问工作台。它会拒绝内嵌凭据和指向 Host 或沙箱 origin 的链接。打开链接不需要活动 MCP 连接。

## 本地、Docker 与反向代理 origin

App 在独立 origin 沙箱代理中的不透明 iframe 内运行。该监听器属于 WebUI，与 Agent 的 Environment 分开。它不提供经身份验证的 Host API 或登录。不要向它发送 Host 身份验证 headers 和 cookies。

### 同机开发

浏览器与 WebUI 在同一机器时，默认值即可：`127.0.0.1` 和自动选择的空闲端口。其他设备无法访问这个回环 URL。远程访问应使用显式配置、浏览器可达的 origin。

### 容器或远程 WebUI

假设 WebUI 发布为 `https://chat.example.com`，可将沙箱配置为独立 origin `https://apps.example.net`：

1. 将此片段合并到根 YAML，使用你的服务器 ID 和实际沙箱主机名。

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

2. 让反向代理可访问 8766 端口。将 `https://apps.example.net/sandbox.html` 路由到该沙箱监听器，保留查询字符串和响应安全 headers。

3. 两个公共 origin 都使用 HTTPS。不要将沙箱流量路由到 Host API／认证端点，也不要转发 Host 凭据。

4. 重启 WebUI。在远程浏览器中调用产生 App 的工具，确认卡片可打开和激活。

`public_url` 是 origin，不是路径前缀，且必须与 WebUI origin 不同。优先使用不会接收域范围 Host cookie 的独立主机名。同机代理可保留 `bind: 127.0.0.1` 并转发固定端口。本地 HTTP Docker 测试应分别发布 WebUI 和沙箱端口，并声明浏览器可达的沙箱 origin。

| 设置                 | 默认值      | 何时修改                              |
| -------------------- | ----------- | ------------------------------------- |
| `enabled`            | `false`     | 设为 `true` 以托管 Apps               |
| `servers`            | `[]`        | 选择确切 App 服务器资源 ID            |
| `sandbox.bind`       | `127.0.0.1` | 代理位于 Host 外时，使用可达的字面 IP |
| `sandbox.port`       | `0`         | 需要转发时选择固定端口                |
| `sandbox.public_url` | `null`      | WebUI 绑定非回环地址时必需            |

安装的 wheel 和 sdist 包含沙箱及编译后的 Host 资源。运行已安装 WebUI 或从 sdist 重建 wheel 不需要 Node.js；准备仓库资源需要。

## 故障排查

| 现象                             | 检查或操作                                                                                                                                             |
| -------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 没有 App 卡片                    | 检查启用标志和服务器 ID，重启 WebUI，并调用产生 App 的工具。缺少 App 元数据的旧历史不能事后补出卡片。                                                  |
| 沙箱不可用                       | 检查绑定地址、端口冲突和公共 origin，再重启。普通文本结果仍可使用，不会降级到同 origin HTML。                                                          |
| 初始化失败                       | SDK 握手超过 15 秒。检查浏览器错误和声明的资源／CSP origin。这不是 MCP 连接空闲超时。                                                                  |
| 修改配置后激活失败               | 当前权限、服务器绑定、子级路由或原工具契约已改变。适当时按当前配置重新调用工具。                                                                       |
| 达到操作上限                     | 显式关闭并重新打开 View。每个 View 最多保留 128 个工具请求，每个最多 256 KiB。                                                                         |
| 外部资源失败                     | 检查已声明的确切资源／连接 origin。不提供 Host-origin 访问和通配授权；外部资源也可能阻止离线显示。                                                     |
| App 依赖浏览器存储或不支持的功能 | 当前配置支持内嵌显示、同服务器工具／资源、文本／结构化上下文、消息、外部链接及主题更新。不提供稳定 origin 存储、图像上下文、App 提供的模型工具或全屏。 |

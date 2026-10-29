---
title: MCP 服务器
description: 连接外部工具服务器，为 Agent 选择它，并验证一次工具调用。
---

添加 MCP 工具需要三步：**注册服务器、选择服务器、测试调用**。需要已有 [Agent](agents-and-subagents.md)，以及 Streamable HTTP 端点或已安装的命令行 MCP 服务器。

运行 `a13n-harness-ui config path`。将服务器文件保存在 `configuration_path` 所示根 YAML 旁的 `mcp/` 下。每个服务器选择以下一种格式，不要重复注册同一 ID。

| 已有的服务器信息                 | 使用方式                                     |
| -------------------------------- | -------------------------------------------- |
| HTTPS MCP 端点                   | [远程传输](#remote-transport)                |
| 通过标准输入/输出提供 MCP 的命令 | [命令传输](#command-transport)               |
| 带 `mcpServers` 的客户端配置     | [复制 JSON 配置](#copy-a-json-configuration) |

## 远程传输

1. 获取服务器的 Streamable HTTP 端点和身份验证要求。下方地址为占位示例。

2. 将以下完整文件保存为 `mcp/docs.yaml`，替换 URL：

   ```yaml title="mcp/docs.yaml — complete file; replace endpoint"
   schema_version: "1"
   kind: mcp_server
   id: mcp-docs
   name: Documentation service
   transport:
     url: https://mcp.example.com/mcp
     headers:
       Authorization:
         env: DOCS_MCP_AUTHORIZATION
   ```

3. 在启动 Harness UI 的进程中设置 `DOCS_MCP_AUTHORIZATION`。值必须是服务器要求的完整 header，包括 `Bearer` 等身份验证方案。服务器不需要 header 时，省略 `headers`。

4. [启用服务器](#enable-a-server)，测试其文档列出的一个只读工具。

使用不含凭据的 HTTPS URL。仅字面回环主机且未配置 header 时支持明文 HTTP。客户端不跟随重定向，请直接配置最终 MCP 端点。远程传输使用 Streamable HTTP，不使用旧版 SSE；此配置不提供 OAuth 登录流程。

## 选择工具

在 **Settings → Connections** 打开 MCP 服务，点击 **Load tools**，切换到 **Selected tools**。搜索并勾选需要的工具，然后 **Save**。设置适用于使用该服务的所有 Agent 和对话，从后续 Run 生效。加载或刷新列表不会执行工具；保存工具选择会保留隐藏的连接凭据。

**All tools** 包括服务以后新增的工具。**Selected tools** 不会自动启用新工具，即使你选中了当前全部工具。清空选择会禁用所有工具，但保留连接。已选但消失的工具会显示为不可用；刷新失败会保留选择。

通过文件配置时，MCP 资源需要 `schema_version: "2"` 和 `tools: [search, fetch_content]`。对于 `mcpServers` 包装格式，在 `mcpServers` 旁设置 `schema_version: "2"`，在对应服务条目内设置 `tools`。旧版 Harness UI 可能拒绝版本 2；不要删除版本来绕过检查。省略或 null 表示全部工具，`[]` 表示不启用工具。工具搜索可以进一步减少上下文，但不能启用已排除的工具。

## 命令传输

1. 安装并检查准备运行的 MCP 服务器。从服务器文档获取可执行文件、参数和必需环境变量。

2. 将如下完整资源保存为 `mcp/local.yaml`。用已安装服务器的实际值替换可执行文件和参数：

   ```yaml title="mcp/local.yaml — complete file; replace executable and arguments"
   schema_version: "1"
   kind: mcp_server
   id: mcp-local
   name: Local tools
   transport:
     command: /absolute/path/to/mcp-server
     arguments: []
     environment:
       API_TOKEN:
         env: LOCAL_MCP_TOKEN
   ```

3. 在 Harness UI 进程环境中设置必需变量。服务器不需要时，移除 `environment`。

4. [启用 `mcp-local`](#enable-a-server)，测试一个只读工具。

Harness UI 在 Host 上启动此程序。选择 Sandbox Environment 不会让外部 MCP 进程进入沙箱。启动进程的 `PATH` 可能不同时，使用可执行文件的绝对路径；命令、参数和 URL 字符串不支持环境变量模板。

## 复制 JSON 配置

如果服务器文档提供 `mcpServers`，可将该对象直接保存为 `mcp/servers.json`。以下示例是 `mcp/docs.yaml` 的**替代形式**，使用同一 `mcp-docs` ID：

```json title="mcp/servers.json — complete file; replace endpoint"
{
  "mcpServers": {
    "docs": {
      "type": "http",
      "url": "https://mcp.example.com/mcp",
      "headers": {
        "Authorization": "Bearer ${DOCS_MCP_TOKEN}"
      }
    }
  }
}
```

设置 `DOCS_MCP_TOKEN`，再按下文选择 `mcp-docs`。此例只将 token 插入 `Bearer` 后面；前面的 YAML 示例读取整个 header 值。

一个包装对象可包含多个服务器。名称会规范化为 `mcp-` ID：`My_Server` 变成 `mcp-my-server`。不同文件中的重复 ID 会导致配置被拒绝。

| 包装对象中的条目 | 字段                                                          |
| ---------------- | ------------------------------------------------------------- |
| 命令             | `command`，可选 `args`、`env`；可选 `type: stdio`             |
| 远程             | `url`，可选 `headers`；可选 `type: http` 或 `streamable-http` |

`.yaml` 和 `.json` 都接受包装对象或单资源格式。使用严格 JSON：注释和末尾逗号无效。移除 `disabled` 等不支持的客户端专用字段。

## 启用服务器

1. 编辑 Agent 文件，如 `agents/coder.yaml`。将服务器 ID 合并到其列表，并保留其他已选 ID：

   ```yaml title="Agent file — merge fragment"
   mcp_servers: [mcp-docs]
   ```

   命令传输示例使用 `mcp-local`。如果希望设置全局创建默认值，则将以下片段合并到根 YAML：

   ```yaml title="a13n-harness-ui.yaml — merge into defaults"
   defaults:
     mcp_servers: [mcp-docs]
   ```

2. 运行 `a13n-harness-ui config validate`。使用自定义配置树时，在 `config` 前加 `--config /absolute/path/a13n-harness-ui.yaml`。

3. 用该 Agent 启动新对话。让它用小输入调用服务器中一个已知的只读工具。检查工具结果，不要把文字回答当作连接成功的证据。

Agent 的 `mcp_servers: null` 继承根默认值；`[]` 不选择服务器。Project 默认值可以在 Agent 和全局默认值之前，为新 Thread 提供选择。注册服务器或保留连接本身不会选择工具。

对于已有 TUI 对话，`/resume <id>` 或重启时的 `--resume <id>` 会按当前 Project → Agent → 全局默认值刷新 MCP 列表。这会替换对话专用列表，包括当前默认值为空的情况。WebUI 和无头运行保留已保存的列表，直到显式修改。

| 现象                         | 检查项                                                |
| ---------------------------- | ----------------------------------------------------- |
| 没有 MCP 工具                | 资源 ID、Agent/Thread 选择，以及精确 `tools` 允许列表 |
| 找不到可执行文件             | 服务器安装位置和 Harness UI 进程的可执行文件路径      |
| `runtime_credential_missing` | 指定变量在 Harness UI 进程环境中非空                  |
| 配置通过但调用失败           | 实际端点、凭据、服务器就绪状态和传输类型              |
| `mcp_source_changed`         | 从当前配置启动新 Run；参见下文捕获来源说明            |

## 字面值与环境变量引用

命令的 `environment`（包装对象中为 `env`）和远程 `headers` 接受以下形式：

| 值                      | 含义                              |
| ----------------------- | --------------------------------- |
| `"us-east-1"`           | 字面字符串                        |
| `"${API_TOKEN}"`        | 读取指定进程环境变量              |
| `"Bearer ${API_TOKEN}"` | 将 token 替换到字符串中           |
| `{"env": "API_TOKEN"}`  | YAML 或 JSON 中的显式环境变量引用 |

展开在新 Run 构造时执行一次。引用的变量必须非空。空字面字符串和空白会保留。仅变量名合法的 `${NAME}` 会展开；不支持 shell 求值或默认值语法。替换仅适用于 environment/header 值。

在启动 Harness UI 前设置变量。在其他 shell 中导出不会改变已运行的进程。包含字面凭据的文件应保持私有，不纳入版本控制。

Run 捕获保存来源位置和摘要，不保存字面的 environment/header 值。客户端构造前，捕获的字面值或模板源文件必须保持不变，否则较早的 Run 或子级续接会报告 `mcp_source_changed`。新 Run 捕获当前来源。环境变量引用允许不修改文件就轮换凭据，前提是新值已传入 Host 进程。

## 连接生命周期与协议

默认客户端生命周期为一个逻辑 Run。要跨 Run 保留服务器的进程内状态，将以下片段合并到根 YAML：

```yaml title="a13n-harness-ui.yaml — merge fragment"
mcp:
  host_owned_servers: [mcp-docs]
  protocol_overrides:
    mcp-docs: auto
```

保留前文的普通 Agent/Thread 选择。`host_owned_servers` 默认 `[]`；`protocol_overrides` 默认 `{}`。列出的 ID 必须指向已配置服务器。

每个 Thread，包括子 Thread，都有自己的保留客户端。Run 完成和浏览器断开不会关闭它。Host 关闭、显式关闭、Thread 释放或绑定更改会结束连接。连接只属于当前进程；续接不会序列化客户端状态或待处理的服务器输入。断开后不会自动重放调用。启用的 [MCP Apps](mcp-apps.md) 自动保留客户端，激活 App 可建立替代连接。

每个协议覆盖接受 `auto`、`legacy` 或 `2026-07-28`；省略时使用 `auto`。除非服务器要求特定模式，否则使用 `auto`。该设置控制 Core 协议协商，与连接生命周期和 MCP Apps UI 协议分开。

## MCP 服务器请求的人工输入

服务器可以暂停操作，请求填写表单或完成外部 URL 流程。TUI 和 WebUI 会显示服务器及 Thread，也包括子级请求。

1. 检查请求及其来源。对于 URL 请求，在 App iframe 之外主动打开目的地，完成该流程后再确认。
2. 在五分钟内选择接受、拒绝或取消。不要在 MCP 表单中输入密码或 token。
3. 回答后检查工具结果。输入被接受只表示答案已交付，不表示远程操作已完成。

表单支持原始字符串、数字、布尔值、单选枚举和字符串多选枚举，包括带标题的选项。Host 根据原 schema 验证答案，拒绝不支持的 schema、远程引用和声明为敏感的字段。

取消、过期、连接关闭或进程关闭会结束请求。答案交付不确定时，检查请求或重发完全相同的答案；冲突答案会被拒绝。嵌入应用必须启用[输入处理器](embedding.md#mcp-input-and-integration-control)。

## MCP 字段参考

单资源文件需要 `schema_version: "1"`、`kind: mcp_server`、唯一的 `mcp-` `id`、显示名称 `name`，以及恰好一种传输形式：

| 传输 | 必需      | 可选                               |
| ---- | --------- | ---------------------------------- |
| 命令 | `command` | `arguments: []`、`environment: {}` |
| 远程 | `url`     | `headers: {}`                      |

Environment 和 header 值接受字符串或 `{env: VARIABLE}`。在 `mcpServers` 包装对象中，使用 `args` 和 `env`，而不是 `arguments` 和 `environment`。

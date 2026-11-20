---
title: 工具与连接
description: 挂载内置系统能力、对接远程 Model Context Protocol (MCP) 服务器、配置企业级 OAuth 授权并启用动态工具发现。
---

在 Service 中，智能体通过**工具（Tools）**与**连接（Connections）**同代码库、云上基础设施以及企业级 SaaS 数据展开深度交互。您可以按需激活沙箱执行与网页检索等内置工具集、向智能体开放 Service 平台管理能力，或者通过带 OAuth 授权的协议安全对接远程 Model Context Protocol (MCP) 服务。

根据具体的系统集成目标选择适宜的接入途径：

| 集成类别 | 核心来源 | 配置指引 |
| :--- | :--- | :--- |
| **文件管理、Shell 命令或桌面 UI** | [内置工具集](#built-in-toolsets) | 挂载[执行环境 Environment](environments.md) 并激活特定操作集合 |
| **网页检索与高保真抓取** | [Web 工具](#web-search-and-scrape) | 在工作空间设置中配置搜索或抓取服务商 |
| **Service 平台管控接口** | [平台工具](#platform-tools) | 按需开启平台管理能力组，并配置读写审批控制 |
| **远程 MCP 服务器与 SaaS 生态** | [外部连接 Connections](#connections) | 注册支持 Streamable HTTP 的 MCP 端点或配置 OAuth 连接器 |
| **自研业务系统专有函数** | 客户端工具（Client Tools） | 声明输入 JSON Schema，并通过 API 批量断点恢复机制注入执行结果 |
| **团队标准化操作流程** | [技能 Skills](skills.md) | 为 Agent 挂载带版本保护的操作指引与配套工具包 |

## 内置工具集

Service 平台开箱内置了全套系统级工具集。调用 `GET /api/v1/toolsets` 可以动态查询当前部署所开放的全部工具键名、参数 Schema 规范以及权限默认策略。

| 工具集名称 | 赋予的能力 | 前置依赖条件 |
| :--- | :--- | :--- |
| `files` | 检索、遍历、读取以及修改工程文件 | 默认启用；需要挂载用户级执行环境 |
| `shell` | 执行 Bash 终端命令并管理后台进程 | 默认启用；需要挂载用户级执行环境 |
| `computer` | 自动化控制桌面鼠标、键盘操作并截取屏幕 | 需要选择支持桌面环境的沙箱模板 |
| `web` | 提供 `search`、`scrape`、`fetch` 与 `download` | 在工作区中绑定已配置的 Web 搜索或抓取服务商 |
| `memory` | 读取与修改 Memory Space 空间中的持久化文档 | 智能体需挂载对应的 Memory Space 记忆空间 |
| `assets` | 将沙箱内的输出文件作为成果物发布至资产库 | 在智能体版本中按需开启 |
| `browser` | 控制并观测无头 Chromium 浏览器会话 | 需要配置浏览器自动化运行环境 |

## 平台工具

平台工具赋予智能体可编程管控 Service 自身工作区资源的能力。您可以在智能体配置编辑器的 **Advanced / Platform Features** 面板中按需开启：

| 平台工具组 | 可管控的平台资源与操作 |
| :--- | :--- |
| `agents` | 检索、创建、修改并发布 Agent 版本；切换生效的默认版本 |
| `models` | 检索可用模型、查看模型供应商列表并调优参数 |
| `connections` | 维护远程 MCP 服务器连接并执行连通性健康探测 |
| `skills` | 检索、上传、校验并导入标准 `SKILL.md` 技能包 |
| `environment_templates` | 创建与维护 Docker 容器或云端沙箱环境模板 |
| `schedules` | 编排 Cron 定时表达式、切换暂停状态并执行触发测试 |
| `providers` | 维护外部服务商的配置连接 |
| `execution` | 查询会话进度、审计原始执行 Attempt 调用链并提交缺陷报告 |

平台工具的调用凭证采用短期动态签发机制，严格受限于发起人当前所具备的工作区权限。查询类读操作默认自动放行，而所有涉及资源新增、修改与删除的写操作，均会强制弹出交互式卡片等待人工确认。

## Web 搜索与抓取

为智能体赋予实时网络信息检索与清洗能力：

1. 在工作空间侧边栏进入 **Providers → Add provider**，添加搜索或抓取服务商（如 Tavily、Brave Search 或企业私有抓取端点）。
2. 在智能体配置详情中开启 `web` 工具集，并根据业务需求勾选启用的子工具（`search`、`fetch` 或 `scrape`）。
3. 在工具配置中填入绑定的服务商 ID：

```json title="agent_web_config.json"
{
  "toolsets": {
    "web": {
      "enabled": true,
      "tools": {
        "search": {
          "enabled": true,
          "config": {
            "provider_id": "wprov_search_123",
            "max_results": 5
          }
        },
        "fetch": {
          "enabled": true
        }
      }
    }
  }
}
```

- `search`：调用上游服务商并返回带排名的结构化网页引用卡片。
- `fetch` / `download`：通过 Service 内置的高性能 HTTP 客户端直接抓取目标网页（严格遵循企业出网安全策略）。
- `scrape`：深度渲染复杂动态网页并将其清洗转换为整洁的 Markdown 文本。

## 连接

**Connection（连接）**负责在工作区与远程 Model Context Protocol (MCP) 服务或企业 SaaS 应用之间建立安全的通信隧道，集中托管认证凭据、OAuth 令牌并管理工具 Schema 缓存。

- 工作区内任何具备 `run` 权限的成员均可在会话中调度该连接暴露的工具。
- 创建或修改连接需要工作区的 `write` 写权限。
- 敏感认证凭据在落盘前均经过强加密，接口查询时绝不会明文回显。

### 远程 MCP 服务器

接入支持 Streamable HTTP 标准协议的远程 MCP 服务：

1. 在 Console 侧边栏进入 **Connections → New connection**。
2. 选择 **Custom Remote MCP**（或选择已预置的服务商模板）。
3. 输入目标服务器的端点 URL（例如 `https://mcp.internal.corp/v1`）。
4. 选择对应的鉴权方式：`none`、`bearer`、`headers` 或 `oauth`。
5. 填入鉴权 Token 并点击 **Test connection**，验证网络通畅度并实时抓取已发布的工具列表。

通过 HTTP API 调用 `POST /api/v1/connections` 完成注册：

```json
{
  "type": "mcp",
  "name": "企业内部研发知识库",
  "auth": "bearer",
  "config": {
    "url": "https://mcp.internal.corp/v1",
    "tools": ["query_handbook", "search_docs"]
  },
  "credential": {
    "token": "secret_mcp_token_xyz"
  }
}
```

可选的 `config.tools` 数组可用于精准收敛需要暴露给智能体的工具白名单。

### 供接入方使用的 MCP Apps

- [接入 MCP Apps 完整指南](mcp-apps.md)

### OAuth

针对需要基于用户身份或服务授权进行细粒度控制的连接：

| OAuth 授权类型 | 适用场景与授权流程 |
| :--- | :--- |
| `authorization_code`（默认） | 基于浏览器的用户级授权；管理员在界面点击 **Authorize connection**，在浏览器跳转完成鉴权回跳 |
| `client_credentials` | 服务对服务的机器级认证；Service 凭借预配置的 Client ID 与 Secret 直接向鉴权服务器签发令牌 |

Service 在底层自动管理 OAuth Token 的完整生命周期：在工具调用前透明检测令牌有效期，并在临期前自动发起平滑刷新。

### Composio 连接

Service 原生集成了 Composio 连接器生态，允许智能体快速接入上百款主流企业级 SaaS 应用（如 Jira、Slack、GitHub、Google Drive 等）：

1. 在工作区设置中添加 `composio` 提供商，填入项目 API 密钥。
2. 在 Connections 列表中挑选需要接入的应用，勾选所需的 Action 工具白名单。
3. 在弹出的浏览器窗口中完成目标平台的账号授权。
4. 在智能体配置中直接勾选该连接即可使用。

### 测试与发现工具

随时对连接发起健康检查并刷新其发布的工具清单：

```bash
curl -X POST "$A13N_URL/api/v1/connections/$CONNECTION_ID/test" \
  -H "Authorization: Bearer $A13N_API_KEY"
```

查询当前已发现的工具定义与入参 Schema，访问 `GET /api/v1/connections/{id}/tools`。探测结果会依据全局 TTL 策略缓存在 Redis 中以保障性能。

### 在 agent 中使用连接

在智能体配置中声明 `connection_tools` 即可赋予其调用权限：

```json
{
  "connection_tools": [
    {
      "connection_id": "conn_knowledge_base_123",
      "tools": null,
      "permission": "ask",
      "permissions": {
        "query_handbook": "allow"
      }
    }
  ]
}
```

- `tools: null`：全量暴露该连接所包含的所有工具。
- `permission: "ask"`：默认所有工具调用均需经过人工弹窗审批。
- `permissions`：精细覆盖特定安全的只读工具为 `"allow"`，允许其免密直接执行。

### 原生工具发现

当智能体挂载了数十个企业连接、包含上百个复杂工具时，在每次推理时将所有工具定义全量传入 Prompt 会严重消耗上下文并增加延迟。

在 Console 的 **Tool availability** 设置中开启 **Native tool discovery（原生工具发现）**：

```json
{
  "tool_search": {
    "mode": "native",
    "sources": {}
  }
}
```

Harness 会在底层调用大模型原生的动态工具检索能力（或回退至高性能本地检索工具）。模型仅在真正需要使用某项能力时，才会按需发起检索并动态加载对应的入参描述。

### 分组工具发现

您还可以通过 **Grouped tool discovery（分组工具发现）** 将功能相似的工具归组为高层次的逻辑模块：

```json
{
  "tool_search": {
    "groups": {
      "knowledge": {
        "description": "检索公司规章制度、系统架构规范以及运维应急手册。",
        "connections": ["conn_knowledge_base_123"]
      },
      "code_tools": {
        "description": "在沙箱环境中审查文件、编写补丁并执行 Shell 命令。",
        "capabilities": ["files", "shell"]
      }
    }
  }
}
```

大模型在初次思考时仅会看到简明的组描述信息。当判断任务与某一领域相关时，再调用 `discover_tools` 展开该组工具详情并通过 `call_tool` 执行，极大降低了基线 Prompt 开销。

### 调用方请求头

若需要将非敏感的业务上下文（如当前操作员的 User ID、跟踪追踪编号）透传给远端 MCP 服务，可在会话或线程级别配置 `mcp_headers`：

```json
{
  "mcp_headers": {
    "conn_knowledge_base_123": {
      "X-User-ID": "usr_9981",
      "X-Tenant": "apac"
    }
  }
}
```

### MCP 服务器建议

平台运维团队可以在 Service 节点的 TOML 配置文件中预置企业推荐的 MCP 服务列表：

```toml title="service.toml"
[[providers.mcp_servers]]
key = "internal-handbook"
name = "企业内部知识库"
description = "统一检索企业内部 Wiki、工程规范与技术文档"
url = "https://mcp.internal.corp/handbook"
auth = "oauth"
```

Console 用户在新建连接时只需点击推荐卡片，系统便会自动填入合规的企业端点配置，降低手工配置门槛。

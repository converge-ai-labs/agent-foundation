---
title: Agent Composer
description: 通过自然语言交互式会话、私有凭证安全卡片与平台 MCP 原语，高效创建、微调与审计企业级智能体。
---

**Agent Composer** 是 Service 内置的交互式智能体构建专家。您无需再手动手写繁琐的 JSON 配置规范，只需使用自然语言描述智能体的业务职责、所需工具与交付标准。Composer 会自动审视当前工作区已接入的模型与外部工具，起草一份结构完整的配置方案，并在持久化保存为版本化快照前向您发起审批确认。

除创建 Agent 之外，Composer 还能协助挂载新的外部模型提供商、生成自动化 Cron 定时调度任务，并能直接下钻分析执行失败的会话 Run 以排查底层错误。

## 启动 Agent Composer

使用 Composer 需要拥有当前工作空间的 `write` 写权限，且工作区内至少已启用一款可用的大模型。

1. 在 Console 侧边栏进入 **Agents → Create with AI**。
2. 键入您的业务诉求，例如：
   > *“帮我创建一个专门用于审查 GitHub Pull Request 安全漏洞的智能体。要求检查依赖清单版本、校验对外入参过滤逻辑并标注缺失的鉴权中间件。使用当前可用的快速模型，并在保存前让我确认指令内容。”*
3. 审查界面中呈现的结构化提案卡片。
4. 点击批准写入。确认后，Composer 会将该配置落盘保存为该智能体的初始不可变版本（Revision）。
5. 点击 **Try agent** 开启测试对话窗口，发送测试用例并验证回复是否符合业务预期。
6. 后续如需微调，只需打开该智能体详情并选择 **Edit with AI**。Composer 会自动载入当前生效版本的全部参数，在保留已有配置的基础上执行精准更新。

## 配置服务商

当智能体需要连接新的模型供应商、外部 MCP 远程服务或云端沙箱时，Composer 会引导您完成安全接入而绝不泄露敏感密钥：

1. 向 Composer 提出配置诉求（例如：*“帮我接入 Anthropic 账号以支持深度逻辑推理任务”*）。
2. Composer 会在聊天界面中直接唤起一张**私有服务商配置卡片**。
3. 在卡片中安全输入您的 API 密钥或鉴权 Token，并点击 **Save provider**。
4. 卡片会通过加密通道直接将凭据发送至 Service 服务商管理端点。**任何私有密钥绝不会注入大模型的 Prompt 上下文、工具调用入参或会话消息历史中。**
5. 凭证落盘加密后，Composer 会接收到新生成的 Provider ID，并继续推进后续的 Agent 配置关联。

## 可执行的操作

Composer 通过底层带细粒度鉴权的平台级 MCP 工具集运作，严格约束在您当前的操作权限范围内：

| 资源领域                 | Composer 可执行的自动化操作                                                   |
| :----------------------- | :---------------------------------------------------------------------------- |
| **Agent 智能体**         | 创建、检索、重命名、打标签并发布新的不可变版本；切换默认生效版本              |
| **Model 模型**           | 发现可用模型、注册新服务商并微调温度系数、最大 Token 预算等参数               |
| **Connection 外部连接**  | 自动发现并连通远程 MCP 服务，挑选需暴露的工具集并执行连通性自检               |
| **Skill 技能包**         | 导入并校验 GitHub 公开技能仓库或本地上传的 `SKILL.md` 规范包                  |
| **Environment 沙箱模板** | 创建与维护 Docker 容器或云端沙箱模板，为代码审查与执行提供安全隔离            |
| **Schedule 定时调度**    | 编排 Cron 周期表达式、配置执行时区、预览未来触发时间戳并执行单次触发测试      |
| **故障诊断与观测**       | 检索历史会话线程、下钻查看底层执行 Attempt 痕迹、分析报错调用栈并记录缺陷审计 |

所有写操作（如创建智能体、更新模型、新增调度等）均会在执行前通过交互式审批卡片请求人工确认，杜绝误操作。

## 通过 API 准备

开发者可以通过 Service 标准 HTTP API 配合工作区 API Key 可编程地调用 Composer：

```bash
curl --fail-with-body -sS -X POST "$A13N_URL/api/v1/agent-composer" \
  -H "Authorization: Bearer $A13N_API_KEY" \
  -H "Content-Type: application/json" \
  -o composer.json

jq '{id, source, preset_kind, default_revision_id}' composer.json
```

接口会返回 `preset_kind: "composer"` 的预置 Agent 实例及其唯一分配的 `id`。随后，您的自动化 CI/CD 流水线即可通过 `/api/v1/sessions` 接口向其发送消息，实现全自动的 Agent 动态构建。

## 选择模型

当首次初始化 Composer 时，Service 会智能挑选工作空间内最适合的模型：

1. 优先比对平台部署配置中的首选推理模型列表（如旗舰深度思考模型）。
2. 若未匹配，则按注册顺序挑选工作空间内第一个已启用的可用模型。

若当前工作空间内尚未配置任何可用模型，初始化将返回 `409 conflict`，提示需先连接模型服务商。

## 内置 agent 规则

Composer 作为一个受保护的内置 Agent 运作，平台管理员可以对其进行个性化定制：

- 在 Composer 的详情设置页中，按需调整其思考预算（Thinking Effort）、温度或输出上限。
- 在配置编辑器中追加\*\*补充指令（Supplemental Instructions）\*\*以强制执行企业标准（例如：*“所有工具标识符必须采用 snake_case 命名风格”*）。
- 在 Composer 的环境设置中选择共享实例或模板并保存，即可为整个工作区设置默认环境。线程缺少主环境时会使用它，Composer 可以读取其中的项目文件，包括 `AGENTS.md`。要仅在本次运行跳过主环境，请在运行选项中选择**不使用主环境**。
- 补充指令会在运行时与平台官方指导准则自动合并生效。
- 若需要完全无限制地定制系统 Prompt，您可以一键克隆（Duplicate）Composer 生成一个完全独立的自定义 Agent。

## 在自己的 agent 中使用工具集

驱动 Composer 运作的底层平台 MCP 工具集同样可以挂载至您自建的普通 Agent 中，用于打造运维巡检机器人或工作区资产治理 Agent：

1. 在自建 Agent 的配置详情中展开 **Advanced / Platform Features**。
2. 勾选所需的能力工具集（如 `agents`、`models`、`schedules` 或 `execution`）。
3. 挂载内置的 `platform-operations` 技能包以注入官方推荐的操作规程。
4. 勾选基础的文件查看工具（`view`, `ls`, `glob`），确保智能体能够读取元数据规范：

```json title="agent_config.json"
{
  "toolsets": {
    "agents": {
      "enabled": true,
      "tools": {
        "archive_agent": { "enabled": false }
      }
    },
    "models": { "enabled": true },
    "execution": { "enabled": true }
  },
  "builtin_skills": ["platform-operations"]
}
```

所有平台工具的调用均严格受制于当前发起者的安全权限，保障坚固的多租户隔离边界。

### Computer 坐标模式

若您自建的 Agent 同时启用了桌面自动化操作（Computer Use）与平台管理能力，可在环境设置中自由选择使用百分比归一化坐标还是绝对物理像素坐标。

## 内置 agent 准备行为

每当用户在 Console 中打开 Composer 或运行 **Try agent** 时，Service 会自动感知底层版本变更并自动同步最新的候选版本。这确保了在平台升级后，最新的模型能力与安全更新能够第一时间无缝触达用户，无需任何繁琐的人工介入。

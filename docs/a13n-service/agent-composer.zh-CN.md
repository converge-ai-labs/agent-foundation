---
title: Agent Composer
description: 通过自然语言交互式会话、私有凭证安全卡片与平台 MCP 原语，高效创建、微调与审计企业级智能体。
---

**Agent Composer** 是 Service 内置的对话式智能体构建工具。使用自然语言描述智能体的业务职责、所需工具与交付标准，Composer 就会检查当前工作区已接入的模型与外部工具，起草结构完整的配置方案，并在确认后保存为不可变修订版本。

Composer 还可以配置服务商和调度，分析模型用量与成本，管理 Webhook 和环境出站凭据，并排查执行问题。

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

当智能体需要连接新的模型供应商、外部 MCP 远程服务或云端沙箱时，Composer 通过独立私密卡片引导接入：

1. 向 Composer 提出配置诉求（例如：*“帮我接入 Anthropic 账号以支持深度逻辑推理任务”*）。
2. Composer 会在聊天界面中直接唤起一张**私有服务商配置卡片**。
3. 在卡片中输入 API 密钥或鉴权 Token，并点击 **Save provider**。
4. 卡片通过加密通道将凭据直接提交至 Service 服务商管理端点，保持模型的 Prompt 上下文、工具入参与消息历史不含明文密钥。
5. 凭证落盘加密后，Composer 会接收到新生成的 Provider ID，并继续推进后续的 Agent 配置关联。

## 分析用量与成本

打开 **Usage**，选择时间范围以及成员或 Agent 筛选条件，然后点击**询问 Composer**。新对话会带入这些准确条件和浏览器时区。你可以查询总量、模型或 Agent 明细、高成本 Session、上下文用量或请求延迟。成员统计需要工作空间管理员权限。Composer 汇总已定价模型调用的估算美元成本及未定价请求数（实际结算账单与沙箱环境费用以各供应商账单为准）。

## 配置 Webhook 与凭据

在 **Settings → Webhooks** 或 **Credentials** 的列表或详情页选择**询问 Composer**。Webhook 的读写都需要工作空间管理员权限；凭据配置需要写权限，使用对话还需要运行权限。

- Webhook：查看订阅和投递记录，修改事件筛选或地址，启停、删除，以及重试失败投递。创建和更换签名密钥使用私密表单。创建后先将显示的密钥保存到接收端，再点击**我已保存密钥**。接收端验证 `webhook-signature`，按 `webhook-id` 去重。已入队的投递继续使用原地址和密钥。
- 环境出站凭据：通过私密表单创建、修改名称和说明、轮换、撤销或请求同步。允许域名在创建后固定。由 App 管理的凭据应通过对应 App 修改。Service 内的撤销不可恢复；上游令牌仍需在对应服务商处撤销。

令牌和签名密钥由表单直接提交给现有 Service 接口，Composer 只收到状态、资源 ID 和版本。保存凭据后，可通过 **Session application** 查看各 Session 的同步进度：活跃 Session 异步更新，空闲 Session 在下次使用时更新。

版本冲突需要重新读取并准备请求。保存结果未知时，先到资源页核实，再请求修改。保存成功后，在当前卡片中提交或重试回复不会重复保存；刷新页面后，应先核实资源状态再重复创建。

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
- 在配置编辑器中追加 **Supplemental Instructions**（补充指令）以实施团队统一规范（例如：*“所有工具标识符必须采用 snake_case 命名风格”*）。
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

在未固定版本的新 Composer 对话首次提交前，Console 会在当前用户具有写权限时准备预设。定义更新会创建候选版本，并保留已选默认版本、模型设置和补充指令。需要显式选择新版本才能使用新增工具。已有对话和等待回复的 Run 保留已接受的配置。

准备新定义前应一起升级 Control、Worker 和 Console。新 Composer 候选版本启用 `usage`、`subscriptions` 和 `egress_credentials`，普通 Agent 默认不启用。无需数据库迁移或数据回填；旧程序可能无法识别新增的工具集名称。

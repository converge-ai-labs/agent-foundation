---
title: 使用已有平台
description: 登录 Console，试用团队的 agent，并创建自己的 agent。
---

通过 Service Console 使用团队的 agent。你需要 Console URL、账号和运行 agent 的权限。要部署 Service，请从[快速入门](get-started.md)开始。Console 与 Harness UI 工作台是独立应用。

## 登录

1. 打开管理员提供的 Console URL。
2. 登录，或接受邀请并创建账号。
3. 选择团队的工作空间。

在选定的工作空间中，**runner** 允许执行 agent，**builder** 还允许创建资源。组织或工作空间授予的角色均生效；如果需要访问权限，请联系管理员。参阅[角色](identity.md#roles)。

## 试用 agent

1. 打开 **Sessions → New session**，然后选择 **Choose an agent** 。
2. 发送符合 agent 用途的请求。对于基础助手，可以试试：“把这些笔记整理成接下来的两步：草稿已完成；等待评审；计划周五发布。”
3. 查看流式回复。对话中也可以显示推理和工具调用。
4. 继续追问，例如“把第一步写得更具体些”。同一线程保留讨论历史。

稍后可以回到保存的对话继续。如果运行失败，请查看详情或将运行 ID 提供给管理员；参阅[故障排查](monitoring.md#troubleshoot-a-request-or-run)。

## 创建自己的 agent

在此工作空间拥有 builder 或 admin 角色时：

1. 确认 **Models** 中有可用模型，或[添加模型](get-started.md#add-a-model)。
2. 打开 **Agents → Create agent**，选择模型，并填写指令，例如“将项目笔记整理成简短、具体的后续行动”。
3. 保存，选择 **Try agent**，并发送上面的请求。

每次保存都会创建一个修订版本。按需添加[工具与连接](tools.md)、[skill](skills.md)或[子 agent](agents-and-runs.md#subagents)。修订版本选择请参阅 [Agent](agents-and-runs.md#agents)。

## 引导工作

- **问题与审批：** 在对话中回答问题，批准或拒绝请求的工具调用。
- **引导或停止：** 运行期间发送新消息，或停止当前运行。
- **文件与命令：** 在 **Run options** 中选择环境，或将[环境模板](environments.md#templates)配置为 agent 的默认环境。
- **共享知识：** 在线程上挂载[记忆](memory.md)，或将其设为 agent 默认配置。记忆可以自动提供上下文；启用记忆工具后，agent 可以搜索和更新记忆。

阅读[核心概念](../overview/core-concepts.md)了解对话模型，使用 [Agent Composer](agent-composer.md)协助配置，或将[你的应用连接](connect-application.md)到同一 Service。

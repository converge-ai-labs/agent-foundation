---
title: 记忆
description: 在 Harness 中使用文件记忆（Markdown 笔记）或记录记忆（按相似度召回的事实）实现跨线程知识共享。
---

当一段对话的经验需要服务于后续其他对话（用户偏好、项目规范或长期决策）时，使用**记忆**；仅限单线程内的任务与笔记请使用[工作状态](context.md#working-state)。

## 选择记忆模型

<span id="what-the-model-gets"></span>

| 能力                     | 存储与召回方式                 | 适用场景                           |
| ------------------------ | ------------------------------ | ---------------------------------- |
| `FileMemoryCapability`   | 挂载目录中可读的 Markdown 文件 | 项目规范、操作指南与人工整理的知识 |
| `RecordMemoryCapability` | 按查询/相似度召回的结构化记录  | 大规模用户事实、偏好与历史经验     |

## 在运行中配置记忆

1. 在 `AgentSpec` 或构建器中启用 `FileMemoryCapability` 或 `RecordMemoryCapability`。
2. 在 `RunBindings` 中传入宿主托管的存储或读写挂载，由宿主决定哪些线程共享存储、哪些运行拥有写入权限。
3. 切勿将明文凭据或临时密钥写入持久化记忆存储。

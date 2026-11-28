---
title: Connector 事件
description: 在 a13n 中接收 Composio 签名事件，保存事件记录，或通过服务账号为每个事件启动 Agent 对话。
---

Connector 事件可以在外部账号发生变化时启动 Agent，例如收到新邮件或新建 Issue。a13n 先接收并保存事件，再使用指定的 Agent 和服务账号创建对话，也可以只保存事件而不执行 Agent。

## 创建订阅

1. 添加 Composio Provider，在 **Connections** 中完成账号授权，并让运维人员配置下文的公网回调地址。
2. 打开 Connection 的 **事件** 页签，由工作区管理员为该 Provider 对应的 Composio 项目点击一次 **配置回调**。
3. 点击 **添加订阅**，选择事件类型，填写参数并完成页面提示的上游设置。事件列表和表单由 Composio 动态提供。
4. 选择 **保存事件** 或 **启动 Agent**。启动 Agent 时，管理员需指定 Agent 和有执行权限的[服务账号](identity.md#service-accounts)。保存后固定 Agent 的修订版本。
5. 等待 **正在更新订阅…** 完成，在外部服务触发一个匹配事件，再打开订阅的 **最近事件**。提交成功后点击 **打开对话** 查看 Run 结果。

同一 Composio 项目的订阅共用一个回调。如果已有其他应用使用该项目回调，请使用独立项目和 Provider。a13n 会报告冲突并保留原回调。

## 配置公网回调

运维人员在 Service 配置中设置公网可访问的 HTTPS 基础地址：

```toml
[connector_events]
public_url = "https://events.example.com"
```

将该域名下的 `POST /api/v1/connector-webhooks/*` 转发到此部署的 Control 服务。事件页会显示 Provider 的完整回调地址。管理接口仍需认证；回调入口自行验证 Composio 签名。配置回调前，先将所有接收流量的 Control 升级到新版本。

**轮换签名密钥** 会更新项目的签名密钥。API key 通过普通 Provider 编辑页更换，并必须属于原 Composio 项目。轮换中断后，恢复流程会查询当前远端密钥；自动尝试耗尽时可以点击 **重试回调配置**。

## 管理订阅与事件记录

**暂停** 后，新到达的事件不再触发执行；**启用** 会重新检查账号和执行权限；**退休** 会永久结束订阅。已经接收的事件继续使用保存时的目标执行，不受这些操作影响。

**编辑** 可修改名称或后续事件的执行目标。修改来源账号、事件类型或参数时，请退休原订阅后创建新订阅。Connection 授权撤销或账号变更后，相关订阅会自动暂停。

| 事件状态 | 含义与操作                                     |
| -------- | ---------------------------------------------- |
| 已保存   | 已保存事件，不启动 Agent                       |
| 待投递   | 已接收，等待提交                               |
| 已提交   | 已交给 Inbox；打开对话查看执行结果             |
| 已忽略   | 事件到达时订阅已暂停或退休                     |
| 失败     | 查看原因；管理员可以重试符合条件的投递失败事件 |

**重试订阅配置** 用于恢复失败的远端订阅操作。修复问题后，**重试投递** 会使用已保存的事件重新提交，继续沿用原 Agent 目标。正文与事件定义不兼容时，不允许重试执行。

事件正文默认保留七天，事件标识和回执保留三十天；对话有独立的历史保留规则。运维人员可在[配置参考](configuration-reference.md)中调整保留时间、回调和积压限制。

## 使用 HTTP API

使用工作区 API key 和常规的工作区选择方式。下列路径均以 `/api/v1` 开头，列表接口支持游标分页。

| 操作               | 方法与路径                                                              |
| ------------------ | ----------------------------------------------------------------------- |
| 发现事件类型       | `GET /connector-providers/{provider_id}/apps/{app}/event-types`         |
| 查看或配置共享回调 | `GET` / `POST /connector-providers/{provider_id}/event-webhook`         |
| 列出或创建订阅     | `GET` / `POST /connector-triggers`                                      |
| 读取或编辑目标     | `GET` / `PATCH /connector-triggers/{trigger_id}`                        |
| 修改订阅状态       | `POST /connector-triggers/{trigger_id}/{enable,pause,retire,reconcile}` |
| 查看事件           | `GET /connector-events?trigger_id={trigger_id}`                         |
| 重试投递           | `POST /connector-events/{event_id}/retry`                               |

创建订阅需要 `Idempotency-Key`；更新和命令需要通过 `If-Match` 提交当前 ETag；配置回调使用 Provider 的 ETag。请求结构见 [API 参考](api-reference/index.md)。签名回调返回 HTTP 204 表示事件已经保存；执行结果通过事件上的 Inbox/Thread 引用查询。

创建期间暂停或停用同样以本地数据库的状态更新时间为准：此前收到的事件继续处理，此后收到的事件在关联订阅时标记为 `ignored`，不等待远端暂停完成。

如果创建请求已经发出，但响应丢失，系统会显示 `subscription_creation_unconfirmed`，不会自动认领参数相同的远端订阅。请先在 Provider 中确认并处理该订阅，再停用本地记录、重新创建。其他可以安全重试的操作即使在最后一次尝试中断，也能在超时后恢复；仍在执行中的尝试不能被替换。

---
title: 运维 Service
description: 检查部署健康状态、安全升级、保护数据，并排查停滞的任务。
---

运维 Service 时，使用运行中进程所用的配置和凭据。以下示例假定当前环境已安装 `a13n-service`，且 `service.toml` 指向该部署的[配置](configuration.md)。容器部署应在已配置的容器或迁移 Job 中运行运维命令。

常规升级分四步：备份部署、使用新构建执行迁移、替换进程，再验证就绪状态和真实运行。下文说明各步操作及需要关注的恢复信号。

## 进程角色

单进程部署选用 `all`。API 流量与 agent 执行需要独立扩容时，拆分为 `control` 和 `worker`：

| 角色          | 职责                                                             |
| ------------- | ---------------------------------------------------------------- |
| `all`（默认） | API、Console、维护任务和 agent 执行。                            |
| `control`     | API、Console、会话事件流和维护任务。                             |
| `worker`      | 领取已接受的运行并通过 Harness 执行；HTTP 仅提供健康和就绪端点。 |

在独立进程中分别运行以下命令，每个进程使用自己的监听地址或容器：

```sh
a13n-service --config service.toml run --role control
a13n-service --config service.toml run --role worker
```

所有副本共享 PostgreSQL、Redis、对象存储和加密密钥环。将 control 副本放在 API 负载均衡器后。拆分部署时，将 `server.control_url` 设为 worker 可访问的地址，用于内部平台调用；默认使用 `server.public_url`。明确配置共享执行资源，因为工作空间自动初始化仅在 `all` 角色中运行。

每个 worker 可并发执行 `worker.slots` 个尝试。关闭时先停止领取任务，再给运行中的尝试最多 `worker.drain_seconds`，以便通过检查点交接。随后，保留执行的工具获得独立的 `tool_executions.shutdown_grace_seconds` 和收尾时限。为进程管理器预留足够时间完成这些阶段，再强制终止。

进程崩溃或排空超时后，租约过期允许其他 worker 在新尝试中从最后检查点继续。外部副作用可能已经发生；恢复不会回滚这些操作，也不保证重复执行安全。运行耗尽 `worker.max_attempts` 次计入上限的尝试后失败。

## 健康与就绪

检查每个副本的地址，而不只是负载均衡器。对于监听本机 8080 端口的进程：

```sh
curl --fail-with-body http://127.0.0.1:8080/healthz
curl --fail-with-body http://127.0.0.1:8080/readyz
```

| 结果                                     | 含义与下一步                                                                              |
| ---------------------------------------- | ----------------------------------------------------------------------------------------- |
| `/healthz`：`200`，`status: ok`          | 进程正在提供 HTTP 服务。继续用就绪检查确认运行时状态。                                    |
| `/readyz`：`200`，`status: ready`        | 启动完成、后台服务运行且数据库 schema 匹配。确认 `role` 符合预期。                        |
| `/readyz`：`503`，`dependency: runtime`  | 启动未完成或某个后台服务已停止。检查进程日志。                                            |
| `/readyz`：`503`，`dependency: database` | Schema 校验失败或超过 `server.readiness_timeout`。检查连接和迁移结果。                    |
| `/readyz`：`200`，`degraded: [redis]`    | 恢复 Redis。持久数据仍可读取，但实时输出丢失，限流不再执行。Worker 领取任务改用周期扫描。 |

升级后还应打开一条历史运行，并提交一次仅调用模型的提示。所需工具或环境另行测试。就绪检查不调用模型供应商，也不验证全部已保存对象的内容。

## 备份

保留同一恢复时间点的三个部分：

| 部分                               | 保存的内容                             |
| ---------------------------------- | -------------------------------------- |
| PostgreSQL                         | 账号、资源、运行事实和存储对象的引用。 |
| 对象存储                           | 检查点、显示历史、资产和 skill 包。    |
| 加密密钥环或 `encryption.key_file` | 解密已保存供应商和连接凭据所需的密钥。 |

1. 升级前创建一致的数据库和对象存储备份。单机 Compose 流程会在备份期间停止 Service。
2. 将对应密钥和部署配置保存在部署之外的私有存储中。
3. 在依赖备份之前，先在独立部署中验证恢复。打开历史数据，并确认已保存凭据可用。

具体命令见 [Compose 备份指南](https://github.com/converge-ai-labs/agent-foundation/tree/main/deploy/docker/compose#backups-and-upgrades)或 [Helm 升级指南](https://github.com/converge-ai-labs/agent-foundation/tree/main/deploy/kubernetes#upgrades-and-backups)。更换镜像或执行 `helm rollback` 不会恢复已迁移的数据库，也不会转换保存的对象。

## Schema 迁移

选择一种迁移方式：

- **自动迁移：** `database.auto_migrate = true`（默认值）时，`all` 和 `control` 在启动时迁移。
- **专用 Job：** 在副本上设置 `database.auto_migrate = false`，启动副本前使用新构建执行迁移命令。

Worker 永不迁移。并发迁移进程通过 PostgreSQL advisory lock 协调；`database.migration_advisory_lock_timeout` 限制等待时间。

采用专用迁移步骤时，先完成备份和发布兼容性检查，再运行：

```sh
a13n-service --config service.toml migrate
a13n-service --config service.toml migrate --check
```

检查成功表示数据库符合当前构建预期的 schema，且不存在旧的不分页运行指针。如果命令报告旧版对象，先完成[对象转换](#convert-legacy-run-objects)，再启动副本。

每个进程启动时都要求 schema 与自身构建精确匹配。滚动升级期间，已运行的旧进程可以继续使用修改后的 schema，但 schema head 不同时，就绪检查会变为不可用。选择滚动升级前，应检查迁移兼容性和负载均衡器行为。替换旧进程，检查每个新副本，再验证历史读取和执行。

## 日志与指标

从失败请求的 `X-Request-Id` 开始排查。找到对应日志，再沿运行和尝试 ID 定位失败操作。请求日志使用路由模板，不记录原始 URL 或查询字符串。参阅[监控与排障](monitoring.md)，启用指标、检查运行失败或配置追踪。

## 后台任务

Control 副本通过数据库领取机制协调维护任务。扫描失败后记录错误，并在下一个间隔重试。任务停滞时，先识别受影响的操作，再决定是否增加 worker 容量：

| 现象                         | 检查项                                                          |
| ---------------------------- | --------------------------------------------------------------- |
| 已接受的运行持续排队         | Worker 是否可用、是否有空闲槽位、检查点是否兼容，以及领取错误。 |
| 崩溃的尝试仍显示活动状态     | 租约过期情况和 control 侧恢复扫描。                             |
| 空闲环境仍占用资源           | 模板空闲策略和供应商维护错误。                                  |
| 云环境过期                   | 续期失败和供应商连接情况。                                      |
| 记忆整理或后台压缩一直未结算 | 操作已保存的状态和 control 对账日志。                           |
| Webhook 或邮件停止到达       | Outbox 错误、目标可用性和 dead 投递告警。                       |

Outbox 提供至少一次投递。失败投递按指数退避重试，间隔最长一小时，达到对应类型的 `max_attempts` 后标记为 dead。修复目标后，工作空间管理员可检查并重新投递 [webhook](files-and-webhooks.md#webhooks)。

## Outbox 保留与容量

保留策略控制已结束投递的历史；容量决定待处理投递的推进速度。默认成功记录保留一天，dead 记录保留十四天。清理扫描每分钟执行，每批最多删除 1000 条，持续最多五秒。待处理记录不会因时间或队列深度被删除。

例如，将 webhook 历史保留七天，并降低检查点清理并发度，其他字段继续使用共享默认值：

```toml
[outbox.by_kind.webhook]
delivered_retention_seconds = 604800

[outbox.by_kind.checkpoint_cleanup]
delivered_retention_seconds = 3600
parallel = 2
```

`outbox.defaults` 配置共享策略，`outbox.by_kind` 配置按类型覆盖。各类型继承未指定字段。环境变量 `A13N_OUTBOX__DEFAULTS` 和 `A13N_OUTBOX__BY_KIND` 接收完整 JSON 对象，替换对应 TOML 字段。修改配置后重启进程。完整限制和默认值见[配置参考](configuration-reference.md)。

对 `a13n_outbox_backlog_alert == 1` 和 `a13n_outbox_dead == 1` 设置告警，并取 `all` 和 `control` 副本中的最大值。先恢复目标可用性；若生产速度仍超过消费速度，再调整投递容量。缩短保留时间无法清空待处理任务。

## 环境监控采集器

启用可选的 vmagent 或 Grafana Alloy 集成时，先按[采集器初始化](environment-monitoring.md#bootstrap-an-optional-collector)操作。每个采集器只运行一个 `writer = true` 的 `all` 或 `control` 进程；其他 API 副本使用 `writer = false`。Worker 不同步采集器配置。

修改或停用采集器时，按以下顺序操作：

1. 保留写入进程与采集器共享的受保护配置目录。备份运维管理的转发配置和凭据；Service 根据环境绑定重新生成自己的片段。
2. 修改后检查环境的采集器状态、采集器活动目标，以及后端的新鲜样本。`reload_requested` 确认的是请求已接受，而非数据已入库。
3. 外部模式下，先移除托管片段并重载采集器，再停止写入进程或禁用监控。停止 Service 不会停用已加载的抓取任务。

Helm 托管的 vmagent 使用专用写入进程/采集器 Recreate Pod，并保留队列 PVC。停用前先解除绑定或禁用数据源，确认目标移除，再排空待写入数据。在交付完成或明确决定丢弃之前保留队列。强制替换失联节点前，先将其隔离；RWO 存储和文件锁不能隔离另一节点。详见 [Helm 采集器生命周期](https://github.com/converge-ai-labs/agent-foundation/tree/main/deploy/kubernetes#environment-monitoring-collectors)。

轮换部署预设令牌后，重启普通 control Pod，并明确替换已保存数据源的凭据。移除预设或允许列表项只影响后续确认，不影响已保存或后端仍接受的凭据。

## 运维命令

将 `--config service.toml` 放在命令名之前。使用该部署的环境和存储访问权限，容器内执行时也相同。

| `a13n-service --config service.toml` 后的命令                 | 用途                                                                     |
| ------------------------------------------------------------- | ------------------------------------------------------------------------ |
| `bootstrap --email EMAIL [--password-stdin]`                  | 创建首个组织、工作空间和管理员；参阅[初始设置](get-started.md)。         |
| `user disable --email EMAIL` / `user enable --email EMAIL`    | 通过[运维账号控制](identity.md#operator-account-control)暂停或恢复账号。 |
| `migrate [--check]`                                           | 升级或校验当前构建的 schema。                                            |
| `migrate-run-objects [--apply --backup-dir PATH] [--limit N]` | 在全部副本停止期间检查或转换旧版对象。                                   |
| `run [--role ROLE]`                                           | 启动 Service 进程。                                                      |

`a13n-service --version` 输出已安装版本。Bootstrap 提示输入密码，或读取标准输入首行；成功后以 JSON 输出新 ID，已初始化时不做修改并以 `3` 退出。`bootstrap`、`user` 和 `run` 都要求数据库符合该构建的 schema。

## Run 搜索部署

升级到引入运行搜索的版本时，迁移会添加默认空值的搜索列，并并发创建 GIN 索引，不回填历史内容。列和触发器修改仍需短暂表锁；迁移超时配置限制执行时间。索引构建失败后可重新运行：无效索引会删除，已完成的有效索引保留。

先升级所有 control 进程和读取端，再启用新 worker。当前 worker 写入检查点格式 4，并读取格式 1–4；旧 worker 会跳过无法继续的格式。写入新格式后，回滚仍需保留兼容的读取端、Console 和 worker。数据库降级会删除派生的搜索数据，但不会转换已保存对象。

## 转换旧版运行对象

仅在从最初的不分页运行显示升级，且迁移命令报告旧版指针时，使用此流程。Schema 迁移重命名数据库字段；独立的转换步骤添加消息序号并压缩对象。

1. 等待已接受和正在运行的任务结束，再停止**全部** control 和 worker 副本。
2. 完成 schema 迁移。随后出现的旧版对象错误表示还需执行转换步骤。
3. 使用数据库所有者权限，并在仓库之外准备持久私有备份目录。备份包含会话数据。
4. 检查一批对象，再执行转换：

```sh
a13n-service --config service.toml migrate-run-objects
a13n-service --config service.toml migrate-run-objects --apply --backup-dir /private/backups/a13n-run-objects
```

默认每批处理 100 条运行；`--limit` 接受 1–1000。命令先校验并备份原始对象和数据库指针，再替换每条运行的指针，保留消息 ID、顺序、内容、流覆盖范围及检查点载荷。已转换运行会跳过，因此中断的批次可以继续。

5. 重复执行 apply 命令，直到报告 `"converted": 0`。遇到拒绝时先调查原因：未完成的检查点清理、已有显示分页、对象缺失或损坏、历史已截断，都需要解决后再继续。
6. 检查部署迁移是否完成：

```sh
a13n-service --config service.toml migrate --check
```

7. 恢复副本，打开历史运行及其消息列表，再继续对话。

回退到旧版不分页读取端时，停止副本，并一起恢复备份的数据库指针和对象。后续清理可能删除原始对象，因此应独立保留私有备份。

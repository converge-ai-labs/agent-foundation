---
title: 运维 Service
description: 进程角色、迁移、健康检查、后台任务、运维命令和备份。
---

一个 `a13n-service` 可执行程序运行 Service 的全部组件。同一部署的所有进程共享 PostgreSQL 数据库、Redis 端点、对象存储和加密密钥环，并使用相同[配置](configuration.md)。

## 进程角色

```sh
a13n-service --config service.toml run --role control
a13n-service --config service.toml run --role worker
```

| 角色          | 提供服务                                                                                  | 后台任务                                    |
| ------------- | ----------------------------------------------------------------------------------------- | ------------------------------------------- |
| `control`     | Console、HTTP API、线程事件流、`/api/v1/openapi.json` 和 `/api/v1/docs` 交互式 API 页面。 | 全部维护扫描和投递。                        |
| `worker`      | 仅 `/healthz` 和 `/readyz`。                                                              | 领取已接收的运行，并使用 Harness 执行。     |
| `all`（默认） | 与 `control` 相同。                                                                       | 同时执行 `control` 和 `worker` 的全部任务。 |

单进程部署使用 `all`。可在负载均衡器后扩展 `control` 副本，并独立扩展 `worker`；worker 不接收 API 流量。Control 副本通过数据库领取机制协调维护任务。

每个 worker 最多执行 `worker.slots` 个尝试。Worker 崩溃后，租约过期时另一 worker 在新尝试中从最后检查点继续运行；消耗 `worker.max_attempts` 次计费尝试后运行失败。关闭时，worker 停止领取任务，并最多等待 `worker.drain_seconds` 以便通过检查点交接。外部副作用不会自动回滚，也不保证可安全重复。

## Schema 迁移

```sh
a13n-service --config service.toml migrate          # upgrade to this build's schema
a13n-service --config service.toml migrate --check  # verify the schema matches without changing it
```

`database.auto_migrate = true`（默认值）时，`all` 和 `control` 进程启动时迁移。每次迁移持有 PostgreSQL advisory lock，并发启动只迁移一次；等待锁的上限由 `database.migration_advisory_lock_timeout` 控制。Worker 永不迁移。每个进程启动时检查数据库 schema 是否与构建精确一致，不一致则拒绝启动（提示“run a13n-service migrate”）。

使用专用迁移步骤时，例如 Helm chart 的迁移 Job，应在副本设置 `database.auto_migrate = false`，启动前运行 `migrate`。滚动升级期间，旧副本仍使用迁移后的 schema，因此需检查每个发布版本的 schema 修改是否兼容前一版本。

## 健康与就绪

| 端点           | 响应                                                                                             |
| -------------- | ------------------------------------------------------------------------------------------------ |
| `GET /healthz` | 进程提供 HTTP 服务期间始终返回 `200 {"status": "ok", "role": "<role>"}`。                        |
| `GET /readyz`  | 启动完成、后台任务运行且数据库 schema 可用后，返回 `200 {"status": "ready", "role": "<role>"}`。 |

启动未完成或后台任务停止后，就绪检查返回 `503 {"status": "unavailable", "dependency": "runtime"}`；无法在 `server.readiness_timeout` 内检查 schema 时，返回 `"dependency": "database"`。Redis 不可用时仍保持就绪，并添加 `"degraded": ["redis"]`，因为 PostgreSQL 保存全部持久状态；Redis 恢复前，实时线程事件流和限流都会停止。

## 后台任务

Control 进程通过有界扫描维护排队任务和投递。失败的扫描记录日志，并在下一个间隔重试。

| 扫描任务                        | 间隔                               | 工作                                                                                              |
| ------------------------------- | ---------------------------------- | ------------------------------------------------------------------------------------------------- |
| `advance_threads`               | `control.scan_seconds`             | 启动空闲线程的下一条排队输入。                                                                    |
| `expire_leases`                 | `worker.authority_seconds`         | 结束租约过期的尝试，让运行由其他 worker 继续或失败。                                              |
| `expire_credentials`            | `auth.expiry_scan_seconds`         | 删除过期或撤销的登录会话、邮件链接和未接受邀请。                                                  |
| `maintain_environments`         | `environments.scan_seconds`        | 停止或删除超过模板空闲阈值的托管环境，并继续未完成 provider 操作。                                |
| `renew_environments`            | `environments.scan_seconds`        | 续期即将结束的就绪云沙箱，每次调用受 `environments.renewal_seconds` 限制。                        |
| `recover_connection_operations` | `providers.operation_scan_seconds` | 处理超过截止时间且执行者已消失的连接授权操作。                                                    |
| `deliver_outbox`                | `control.scan_seconds`             | 投递 webhook、身份邮件和子 agent 结果，清理已删除记录型记忆的命名空间，并删除被替换的检查点对象。 |
| `purge_outbox`                  | 默认每 60 秒                       | 删除超过其 outbox 类型保留期的已结束投递。                                                        |

投递至少一次。发送者以 `lease_seconds` 的租约领取一批 `batch` 条记录；失败投递按指数退避重试（间隔最多一小时），达到 `max_attempts` 次后标为 dead。这些值来自该类型的 [outbox 策略](#outbox-retention-and-capacity)。工作空间管理员可通过 API 查看和重新投递 webhook 投递；参阅 [webhook](files-and-webhooks.md#webhooks)。

## 运维命令

| 命令                                                      | 用途                                                                                                                                                     |
| --------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `a13n-service bootstrap --email EMAIL [--password-stdin]` | 创建首个组织、工作空间和管理员。提示输入至少 8 个字符的密码，或读取标准输入首行。以 JSON 输出新 ID；已初始化时不做修改，退出码为 `3`；输入无效时为 `1`。 |
| `a13n-service user disable --email EMAIL`                 | 禁用用户账号；参阅[运维账号控制](identity.md#operator-account-control)。                                                                                 |
| `a13n-service user enable --email EMAIL`                  | 恢复账号原有的授权和密钥。                                                                                                                               |
| `a13n-service migrate [--check]`                          | 迁移或检查数据库 schema。                                                                                                                                |
| `a13n-service run [--role ROLE]`                          | 运行一个进程。                                                                                                                                           |
| `a13n-service --version`                                  | 输出已安装版本。                                                                                                                                         |

`--config` 放在命令名之前。数据库 schema 与构建不匹配时，`bootstrap`、`user` 和 `run` 拒绝执行。

## 日志与指标

每个进程使用关联 ID 记录请求、运行和投递，并可提供用于看板和告警的 Prometheus 指标。请求 URL 和查询字符串永不记录。每个响应包含 `X-Request-Id`，同一 ID 也出现在错误体和请求日志中。参阅[监控与排障](monitoring.md)。

## 备份

同时备份 PostgreSQL、对象存储和加密密钥环（或 `encryption.key_file`）。没有写入时使用的密钥，存储的凭据无法解密；运行检查点、显示数据、资产和 skill 包位于对象存储。[单机 Compose 部署](https://github.com/converge-ai-labs/agent-foundation/tree/main/deploy/docker/compose#backups-and-upgrades)和 [Helm chart](https://github.com/converge-ai-labs/agent-foundation/tree/main/deploy/kubernetes#upgrades-and-backups)介绍各自的备份、恢复和升级。

## Outbox 保留与容量

一套 outbox 策略（可按类型覆盖）管理 webhook、邮件、子运行结果、记忆清理和检查点回收。默认成功投递在完成后保留一天，dead 投递在失败后保留十四天。默认情况下，清理扫描每分钟运行一次，在每个短事务中最多删除 1000 条过期记录，重复执行最多五秒。不会为缩减表而丢弃待处理任务。

配置共享默认值，仅按类型覆盖有差异的字段：

```toml
[outbox]
purge_interval_seconds = 60
purge_batch = 1000
purge_budget_seconds = 5

[outbox.defaults]
delivered_retention_seconds = 86400
dead_retention_seconds = 1209600
max_attempts = 12
batch = 32
parallel = 8
lease_seconds = 60
backlog_count = 10000
backlog_age_seconds = 300
backlog_alert_seconds = 300

[outbox.by_kind.webhook]
delivered_retention_seconds = 604800

[outbox.by_kind.checkpoint_cleanup]
delivered_retention_seconds = 3600
parallel = 2
```

环境变量遵循分节约定：`A13N_OUTBOX__DEFAULTS` 和 `A13N_OUTBOX__BY_KIND` 携带 JSON 对象。每个环境变量替换对应 TOML 字段，各类型再从默认值继承未指定的策略字段。未知字段和类型导致启动失败。每个进程只解析一次配置；修改后需重启。

对 `a13n_outbox_backlog_alert == 1` 和 `a13n_outbox_dead == 1` 设置告警，并取 `all` 和 `control` 副本中的最大值。检查失败目标并恢复投递能力；保留策略不会丢弃待处理任务。

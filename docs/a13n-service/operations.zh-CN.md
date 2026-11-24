---
title: 运维 Service
description: 规模化运行 control 与 worker 角色，检查就绪探针，执行数据库迁移，备份核心状态并调优发件箱（Outbox）投递。
---

执行所有 `a13n-service` 运维命令时，均须在子命令**之前**传入 `--config service.toml`，并保持与线上进程一致的存储及加密密钥配置。

## 进程角色与健康探针

| 角色          | 职责                                                                        |
| ------------- | --------------------------------------------------------------------------- |
| `all`（默认） | 提供 API、Console、后台巡检、自动迁移以及智能体执行                         |
| `control`     | 提供 API、Console、SSE 事件流与后台巡检                                     |
| `worker`      | 认领并经由 Harness 执行运行尝试（HTTP 仅暴露 `/healthz` 与 `/readyz` 探针） |

```sh
a13n-service --config service.toml run --role control
a13n-service --config service.toml run --role worker
```

逐个副本检查 `/healthz`（`200 ok`）与 `/readyz`（`200 ready`；若返回 `"degraded": ["redis"]` 说明 Redis 暂不可达，持久化 PostgreSQL 读取仍可用）。在进程监管器中预留不少于 `worker.drain_seconds` + `tool_executions.shutdown_grace_seconds` + `finalization_seconds` 的优雅退出宽限期。

## 备份与恢复

版本升级前请从同一一致性时间点完整备份以下三项（升级注意事项见[升级说明](upgrade-notes.md)）：

1. **PostgreSQL：** 账号、配置资源、运行记录与对象引用。
2. **对象存储：** 检查点、对话历史、工作区资产与技能包。
3. **加密密钥（`encryption.key_file` 或密钥环）：** 解密已保存提供方与连接凭据所必需的密钥。

## 数据库迁移

`all` 与 `control` 默认在启动时通过 PostgreSQL 咨询锁自动执行迁移（`database.auto_migrate = true`）；`worker` 从不执行迁移。若使用独立迁移 Job，请在副本上设置 `database.auto_migrate = false` 并提前运行：

```sh
a13n-service --config service.toml migrate
a13n-service --config service.toml migrate --check
```

## 发件箱（Outbox）保留期与吞吐容量

Webhook、邮件与异步清理通过持久化 Outbox 提供至少一次投递。可在 `service.toml` 中按类型调整保留时长与并发度：

```toml
[outbox.by_kind.webhook]
delivered_retention_seconds = 604800

[outbox.by_kind.checkpoint_cleanup]
delivered_retention_seconds = 3600
parallel = 2
```

针对 `all` / `control` 副本监控 `a13n_outbox_backlog_alert == 1` 与 `a13n_outbox_dead == 1` 告警。集成 vmagent / Alloy 拉取监控时，每个采集器仅保留唯一一个 `writer = true` 副本（见[采集器初始化](environment-collectors.md#bootstrap-an-optional-collector)）。

## 运维 CLI 命令速查

| 命令（`a13n-service --config service.toml ...`）              | 用途                                                                         |
| ------------------------------------------------------------- | ---------------------------------------------------------------------------- |
| `bootstrap --email EMAIL [--password-stdin]`                  | 初始化首个组织、工作区与管理员（见[快速开始](get-started.md)）               |
| `user disable --email EMAIL` / `user enable --email EMAIL`    | 停用或恢复用户账号（见[运维账号控制](identity.md#operator-account-control)） |
| `migrate [--check]`                                           | 升级或校验当前构建所需的数据库结构                                           |
| `migrate-run-objects [--apply --backup-dir PATH] [--limit N]` | 在所有副本停机期间检查或转换旧版运行对象                                     |
| `run [--role all\|control\|worker]`                           | 启动指定角色的 Service 进程                                                  |

<span id="convert-legacy-run-objects"></span>

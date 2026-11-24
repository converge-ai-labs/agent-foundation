---
title: 配置 Service
description: 在 service.toml 中配置 PostgreSQL、Redis、S3 对象存储、加密密钥环、出站网络策略、Kubernetes 沙箱与遥测采集。
---

按三步完成 Service 配置：连接共享存储后端、提供持久化加密密钥，并启动所需的进程角色（`control`、`worker` 或 `--role all`）。各字段上限与默认值详见[配置参考](configuration-reference.md)。

## 配置来源与优先级

将部署配置保存在 `service.toml` 中，在子命令**之前**传入 `--config service.toml`（或设置 `A13N_SETTINGS_FILE`）。单个字段可通过 `A13N_<SECTION>__<FIELD>` 覆盖（例如 `A13N_WORKER__SLOTS=6`）；字典、列表与嵌套段落需传入完整 JSON 字符串（如 `A13N_AUTH__MAIL`）。

```toml
[server]
host = "0.0.0.0"
port = 8000
public_url = "https://agents.example.com"
trusted_proxies = ["10.0.0.0/8"]

[database]
url = "postgresql+psycopg://a13n_service@db.internal:5432/a13n_service"

[redis]
url = "redis://redis.internal:6379/0"

[objects]
backend = "s3"
bucket = "a13n-service-objects"
region = "us-east-1"

[providers]
return_urls = ["https://agents.example.com/connections/callback"]

[telemetry]
log_format = "json"
```

启动并检查就绪探针：

```sh
a13n-service --config service.toml run --role all
curl --fail --silent --show-error https://agents.example.com/readyz
```

## 必需基础设施

所有进程角色（`control`、`worker`、`all`）必须共享同一套 PostgreSQL、Redis、对象存储与加密密钥：

- **PostgreSQL（`database.url`）：** 持久化核心状态（`postgresql+psycopg://`），迁移顺序见[数据库迁移](operations.md#schema-migrations)。
- **Redis（`redis.url`）：** 实时事件流、任务唤醒、限流与环境监控协调。
- **对象存储（`objects.*`）：** 单机或共享卷可使用 `backend = "local"` 与 `root`；多节点部署使用 `backend = "s3"`（`addressing_style` 支持 `"auto"`、`"path"` 或适用于阿里云 OSS 等的 `"virtual"`；若 S3 兼容存储拒绝流式校验尾标，可在各进程设置 `AWS_REQUEST_CHECKSUM_CALCULATION=when_required`）。
- **加密密钥（`encryption.*`）：** 使用 AES-GCM 保护落盘凭据、连接令牌与邮件链接。可配置共享 `encryption.key_file`（权限 `0600`）或显式密钥环：

```sh
export A13N_ENCRYPTION__ACTIVE_KEY_ID=primary
export A13N_ENCRYPTION__KEYS="{\"primary\": \"$(openssl rand -base64 32)\"}"
```

轮换密钥时，在 `keys` 中新增密钥 ID 并切换 `active_key_id`，同时保留旧密钥以解密存量数据。

## HTTP 服务器、身份与邮件

- **HTTP 服务器（`[server]`）：** `public_url` 填写对外 HTTPS 源站，在 `trusted_proxies` 中列出反向代理 IP/CIDR。分离部署时可通过 `control_url` 指定 `worker` 访问 `control` 的内网地址。
- <span id="identity-and-mail"></span>**身份与邮件（`[auth.mail]`）：** 配合加密密钥启用 SMTP，用于发送邀请、密码重置与邮箱变更链接：

```toml
[encryption]
key_file = "/var/lib/a13n/encryption.key"

[auth.mail]
smtp_host = "smtp.example.com"
smtp_port = 587
smtp_security = "starttls"
sender = "agents@example.com"
```

## 出站请求

Service 默认要求提供方、MCP 与 Webhook 目标使用 HTTPS。如需放行受信任的内网明文 HTTP 源站，可在配置中显式列出：

```toml
[providers]
http_origins = ["http://host.docker.internal:11434"]
```

单次运行还可通过 `options.configuration.allowed_hosts` 进一步收窄主机白名单（见[主机规则](../a13n-harness/context.md#host-rules-and-regular-expressions)）。

### 出站代理

在各进程或容器中设置标准 `http_proxy`、`https_proxy` 与 `no_proxy` 即可代理模型、MCP、连接器、联网请求与 Webhook 流量。TLS 证书验证默认保持开启（仅本地调试可显式设置 `A13N_OUTBOUND_TLS_VERIFY=false`，见[出站 TLS 验证](../a13n-harness/models.md#outbound-tls-verification)）。

## 执行与并发

每个 `worker` 默认最多并发执行 `worker.slots = 128` 个尝试（范围 `1`–`1024`）。在 `service.toml` 中协同调整相关预算：

- **重试与租约：** `worker.max_attempts`、`lease_seconds`、`authority_seconds`。
- **子任务与收件箱：** `worker.child_depth`、`child_count`、`control.inbox_count`、`inbox_bytes`。
- **保留工具与优雅停机：** `tool_executions.max_active`（默认 `64`）、`shutdown_grace_seconds`（`300`）与 `finalization_seconds`（`10`）。
- **插件白名单：** 通过 `plugins.keys` 放行已安装的 Harness 插件工厂。

## 日志、指标与追踪

配置 `telemetry.log_level`、`log_format`（`json`）、可选的滚动 `log_file`，以及独立于 HTTP 端口的 `telemetry.metrics_port` 供 Prometheus 抓取（见[监控指南](monitoring.md#metrics)）。

| `telemetry.trace_backend` | 必填配置项                                                |
| ------------------------- | --------------------------------------------------------- |
| `none`                    | 默认值（关闭链路追踪）                                    |
| `langfuse`                | `trace_url`、`langfuse_public_key`、`langfuse_secret_key` |
| `logfire`                 | `trace_url`、`logfire_write_token`、`logfire_read_token`  |

通过 `telemetry.trace_content`（`none`、`standard` 或 `full`）控制提示词与工具负载的导出级别。

## 环境监控

`monitoring.enabled` 默认为 `true`。如需拉取式采集，先配置 `monitoring.collectors.vmagent` 或 `.alloy`（见[采集器初始化](environment-collectors.md#bootstrap-an-optional-collector)），并可向指定工作区发布预设数据源：

```toml
[monitoring.deployment_preset]
name = "Deployment metrics"
collector = "vmagent"
query_endpoint = "https://metrics.example.com"
workspace_ids = ["ws_example"]
```

## Kubernetes 运行环境

可通过 Helm（`kubernetesEnvironments.enabled: true`、`namespace: agent-sandboxes`、私网环境下设置 `allowPlaintextEnvd: true`）启用 Kubernetes 沙箱 Pod，或在 `control` 与 `worker` 加载完全一致的集群配置：

```toml title="service.toml"
[environments.kubernetes_clusters.in-cluster]
authentication = "in_cluster"
namespaces = ["agent-sandboxes"]
ownership_id = "production-service"
sandbox_service_account = "agent-sandbox"
allow_plaintext_envd = true
```

集群外本地开发可使用 `authentication = "kubeconfig"` 搭配 `kubeconfig`、`context` 与 `allow_port_forward = true`（需安装 `kubectl`）。随后按 [Kubernetes 沙箱](environments.md#kubernetes-sandboxes)创建工作区提供方与模板。

## Kubernetes 浏览器

在 Kubernetes 1.32+（支持 NetworkPolicy 与 PVC）集群的 Helm values 中启用 `kubernetesBrowsers.enabled: true` 与 `namespace: agent-browsers`，随后在 Console 完成提供方配置（见[浏览器指南](browsers.md#kubernetes)）。

## 相关指南

- [配置工作区自动预置](workspace-provisioning.md)
- [Docker Compose](https://github.com/converge-ai-labs/agent-foundation/tree/main/deploy/docker/compose) 与 [Helm Chart](https://github.com/converge-ai-labs/agent-foundation/tree/main/deploy/kubernetes) 部署说明

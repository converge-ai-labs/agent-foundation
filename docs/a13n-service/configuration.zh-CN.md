---
title: 配置 Service
description: 通过 TOML 和环境变量配置 Service 的基础设施、身份、执行和遥测。
---

Service 在启动时从可选 TOML 文件和环境变量读取一次设置。修改后需重启所有进程。[设置参考](configuration-reference.md)列出每个字段的类型、范围和默认值。

## 配置来源与优先级

使用 `a13n-service --config service.toml ...` 或 `A13N_SETTINGS_FILE` 环境变量选择文件。任一 `A13N_<SECTION>__<FIELD>` 变量会覆盖文件中的对应字段，例如 `A13N_DATABASE__URL` 覆盖 `database.url`。不会读取其他来源：没有自动 `.env` 文件，也没有配置搜索路径。

未知设置会阻止启动，不会退回默认值。验证错误不会输出密钥值。

共享进程变量 `A13N_OUTBOUND_TLS_VERIFY` 也受支持，不属于 `A13N_<SECTION>__<FIELD>` 机制。未设置或设为 `true` 会验证目标证书；`false` 会显式关闭自有 HTTP 客户端的验证。其他值会阻止启动。它没有 TOML 字段。请在每个 Service 进程中设置，无论其角色，修改后重启；具体范围、例外及被拦截风险见[出站 TLS 验证](../a13n-harness/models.md#outbound-tls-verification)。

列表、映射和嵌套节的环境变量值使用 JSON，例如 `A13N_PLUGINS__KEYS='["notes"]'`。

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

数据库密码、加密密钥环和对象存储密钥等凭据，请通过环境变量或平台的密钥存储提供，不要放在配置文件中。

## 必需的基础设施

| 设置                | 所需内容                                                                                                                                                                            |
| ------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `server.public_url` | 浏览器和 API 客户端访问 Service 的源，Console 和 API 都在此提供。Service 只接受来自该源的浏览器状态修改，并在邮件链接和授权回调中使用它。URL scheme 决定 cookie 是否设置 `Secure`。 |
| `database.url`      | 使用 `postgresql+psycopg://` 驱动的 PostgreSQL URL。请使用此 Service 专用的数据库。                                                                                                 |
| `redis.url`         | 所有进程共享的 Redis 端点。                                                                                                                                                         |
| `objects.*`         | 用于运行检查点和显示数据、上传、资产、skill 包和图片的共享对象存储。                                                                                                                |
| `encryption.*`      | 加密已保存凭据的密钥环，或单机使用的密钥文件。                                                                                                                                      |

### PostgreSQL

PostgreSQL 保存 Service 的持久状态。请为部署设置连接和语句超时；迁移职责请参阅 [schema 迁移](operations.md#schema-migrations)。

### Redis

Redis 保存限流计数器、worker 唤醒和临时线程事件；PostgreSQL 保存持久状态。Redis 不可用时，已保存结果仍可读取，worker 通过扫描领取任务，但实时事件流不可用且跳过限流。就绪检查报告 `"degraded": ["redis"]`。

### 对象存储

`objects.backend = "local"` 将对象保存到 `objects.root`，相对路径按工作目录解析。每个 Service 进程必须访问同一目录，适用于单机或共享卷。`objects.backend = "s3"` 使用 S3 兼容 bucket：设置 `bucket`，并按需设置 `prefix`、`region`、`endpoint_url` 和 `path_style`（用于 MinIO 等按路径寻址 bucket 的存储）。未设置 `access_key_id` 和 `secret_access_key` 时，使用默认 AWS 凭据链。Service 每次使用新 key 写入对象一次，因此存储只需支持普通读取、写入、列出和删除。

`objects.max_bytes` 限制单个存储对象；`objects.upload_bytes` 限制单次上传；每 `upload_window_seconds` 允许 `upload_limit` 次上传，按主体计数。

使用 `objects.addressing_style` 选择 `auto`（由 SDK 选择）、`path`（`endpoint/bucket/key`）或 `virtual`（`bucket.endpoint/key`）。虚拟寻址要求 bucket 名称、DNS 和 TLS 证书兼容；阿里云 OSS 等要求 bucket 子域名的提供商应使用它。省略时沿用现有 `path_style` 行为：`true` 强制使用 `path`，`false` 使用 `auto`。显式寻址方式优先于 `path_style=false`；`path_style=true` 与 `auto` 或 `virtual` 同时设置会导致配置校验失败。使用 Helm 时设置 `objects.addressingStyle`；对应环境变量为 `A13N_OBJECTS__ADDRESSING_STYLE`。

对于拒绝可选流式校验和尾部的 S3 兼容提供商（包括阿里云 OSS），请在每个 Service 进程上设置标准 AWS SDK 环境变量 `AWS_REQUEST_CHECKSUM_CALCULATION=when_required`。本地、Docker 和 Kubernetes 部署都适用；使用 Helm 时，将它写入用于创建 `existingSecret` 的环境文件。操作本身要求的校验和仍保持启用，Service 的摘要校验也不变。未设置时使用 SDK 默认值。OSS 不需要原生写入请求头或 bucket 版本控制检查：所有提供商都使用同一条普通 `PutObject` 路径。

### 加密密钥

Provider 和连接凭据、模型 provider 的额外请求头、外部目标 token、OAuth token、客户端密钥和待完成的授权、webhook 签名密钥和投递目标，以及排队邮件链接，都使用密钥环当前密钥进行 AES-GCM 加密。每个密钥是 32 字节随机数据，以 base64 编码，使用自选 ID：

```sh
export A13N_ENCRYPTION__ACTIVE_KEY_ID=primary
export A13N_ENCRYPTION__KEYS="{\"primary\": \"$(openssl rand -base64 32)\"}"
```

单机时，`encryption.key_file` 可替代 `active_key_id` 和 `keys`：Service 从文件读取一个密钥；文件不存在时，首次启动生成它，权限为 `600`。文件应位于所有 Service 进程共享的持久存储上，例如[单机部署](https://github.com/converge-ai-labs/agent-foundation/tree/main/deploy/docker/compose)的数据卷。密钥 ID 为 `key_file`；之后转用密钥环（例如轮换）时，在 `encryption.keys` 中以该 ID 添加文件内容。

没有密钥也能启动 Service，但保存任何凭据都返回 `unavailable`。轮换时，向 `encryption.keys` 添加新密钥并设为活跃密钥；保留旧密钥，因为旧数据仍用原密钥读取。丢失密钥会使对应加密值无法读取，请将密钥环与数据库一起备份。

## HTTP 服务器

Service 在 `server.host` 和 `server.port` 提供 HTTP。可使用 `server.tls_certificate` 和 `server.tls_key` 直接提供 HTTPS，或在可信代理上终止 TLS。`server.public_url` 为 HTTPS 时，浏览器登录使用带 `Secure` 的 `__Host-` cookie；明文 HTTP 下两者均不具备，因此超出可信网络的访问应使用 HTTPS。

代理后部署时，在 `server.trusted_proxies` 列出代理地址或 CIDR。Service 仅信任这些代理的 `X-Forwarded-For` 和 `X-Forwarded-Proto` 请求头来获取客户端地址和 scheme；限流按该客户端地址计数。

`server.request_bytes` 限制请求体（`413 payload_too_large`），`server.request_timeout` 限制请求体到达时长（`408 request_timeout`）。`readiness_timeout` 限制单次就绪检查，`shutdown_timeout` 限制优雅关闭时长。

## 身份与邮件

`auth.session_seconds` 是浏览器登录会话寿命。每 `auth.login_window_seconds` 允许 `auth.login_limit` 次密码登录，按客户端地址计数；公共连接授权回调使用相同限制，但单独计数。`auth.invitation_seconds` 决定邀请可接受多久，`auth.link_seconds` 决定密码重置或邮箱修改链接有效多久。

设置 `auth.mail.smtp_host` 后，通过 SMTP 发送身份邮件。未设置时，邀请链接一次性返回给邀请人，密码重置和邮箱修改不可用。邮件链接加密排队，因此 SMTP 需要加密密钥：`encryption.active_key_id` 加 `encryption.keys`，或 `encryption.key_file`。链接永不写入日志。

```toml
[auth.mail]
smtp_host = "smtp.example.com"
smtp_port = 587
smtp_security = "starttls"
sender = "agents@example.com"

[encryption]
active_key_id = "primary"  # the key itself comes from A13N_ENCRYPTION__KEYS
```

`smtp_username` 和 `smtp_password` 必须一起提供。使用环境变量时，将完整节作为 JSON 放入 `A13N_AUTH__MAIL`。

## 出站请求

Service 对 provider、远程 MCP 服务器、OAuth 服务器和 webhook 端点的每次请求都遵循同一端点策略：

- URL 使用 `http` 或 `https`，不包含用户信息、片段或类似凭据的查询参数。
- `providers.require_https = true`（默认值）时，拒绝明文 HTTP，除非源与 `providers.http_origins` 中的条目完全一致。
- Host provider 客户端不跟随重定向，拒绝压缩响应，并通过 `providers.response_bytes` 限制响应体。TLS 验证、凭据规则和超时保持有效。

提交到线程的消息通过 `options.configuration` 接受配置，与对 agent 修订版本的覆盖（override）分开：

```json
{
  "configuration": {
    "allowed_hosts": ["api.example.com", "regex:(api|docs)\\.example\\.com"],
    "extensions": {}
  }
}
```

在 `allowed_hosts` 中列出运行需要的所有 provider、连接和远程环境主机名：

- `null` 表示不限制主机；`[]` 拒绝全部目标。
- 普通条目精确匹配规范化后的主机名或 IP。`regex:<pattern>` 使用 Python 正则匹配完整字符串；示例仅放行 `api.example.com` 或 `docs.example.com`。
- 无效或空表达式会使接收失败。glob、端口和 CIDR 不是主机规则。规范化与转义请参阅[主机规则与正则表达式](../a13n-harness/context.md#host-rules-and-regular-expressions)。

接收时冻结配置，供输入 URL 读取、执行、故障恢复、resume 和子运行使用。引导可以省略配置或重复相同值。要修改配置，提交 `delivery: "next_run"`；活跃运行会以 `run_configuration_immutable` 拒绝不同值。

请通过 API 设置此配置；Console 没有对应控件。带命名空间的 `extensions` 仅由显式支持它们的消费者读取。

`allowed_hosts` 检查只比较 URL 声明的域名：不预解析 DNS、不分类地址，也不固定 IP。运行之外的管理操作保留进程的 URL/HTTPS 策略，不借用运行的配置。任意 shell、第三方插件和不透明 SDK 流量的网络限制应在部署或环境边界实施。

### 出站代理

在需要出站访问的每个 Service 进程或容器中设置标准代理环境变量，无需 `A13N_` 前缀或 TOML 代理设置：

```bash
export http_proxy=http://proxy.example.com:8080
export https_proxy=http://proxy.example.com:8080
export no_proxy=localhost,127.0.0.1,::1,.internal.example.com
```

支持大写形式和 `ALL_PROXY`；选择及绕过匹配遵循 `httpx2`。HTTP 代理 URL 可以通过 CONNECT 转发 HTTPS 流量。模型、Remote MCP/OAuth、connector、记录型记忆、web 请求、模型目录、webhook 和宿主 HTTP 客户端的其他调用方都使用这些路由。

通过运维人员的代理时，主机规则和 TLS 验证仍然生效。目标访问由代理或部署网络控制。代理请求失败不会回退为直连。

通过 HTTPS 连接 `a13n-envd` 时使用这些代理变量；明文 HTTP 的本地或 provider 私有 Envd 连接保持直连。其他环境和存储 SDK 保持各自的代理行为。

Console 模型选择器使用 `https://models.dev/catalog.json`。无法访问时，Service 使用上次的目录，或让你按上游 ID 添加模型。

例如，用 `providers.http_origins` 允许以明文 HTTP 访问 Docker 宿主机上的模型服务器：

```toml
[providers]
http_origins = ["http://host.docker.internal:11434"]
```

其他 `providers` 设置限制 provider 工作：`model_timeout`（单次模型交互的每次读取）、`tool_call_seconds`（一次连接工具调用）、`operation_seconds`（授权步骤和资源测试）、`discovery_ttl`（工具发现缓存）和 `flow_seconds`（浏览器授权允许时长）。`providers.return_urls` 列出浏览器授权可返回的精确 Console URL，`providers.mcp_servers` 添加 [MCP 服务器建议](tools.md#mcp-server-suggestions)。

## 执行

| 设置                                               | 作用                                                                         |
| -------------------------------------------------- | ---------------------------------------------------------------------------- |
| `worker.slots`                                     | 单个 worker 进程并发执行的尝试数。                                           |
| `worker.max_attempts`                              | 运行失败前可消耗的计费尝试数。                                               |
| `worker.lease_seconds`, `worker.authority_seconds` | 尝试租约寿命，以及 worker 续期、检查取消和主体访问权限的频率。               |
| `worker.drain_seconds`                             | 停止中的 worker 等待尝试交接的时长。                                         |
| `worker.child_depth`, `worker.child_count`         | 子 agent 运行的深度和数量限制。                                              |
| `worker.stream_coalesce_seconds`                   | 连续文本、推理或工具参数增量合并为一个实时事件的时间窗口。                   |
| `worker.stream_trim_seconds`                       | 检查点已覆盖的实时事件在 Redis 中保留多久，以便短暂断线客户端无缺口恢复。    |
| `worker.stream_length`, `worker.stream_ttl`        | Redis 中单线程实时事件流的兜底长度上限和空闲寿命。                           |
| `worker.output_bytes`                              | 运行结果的大小限制。                                                         |
| `worker.page_items`, `worker.page_bytes`           | 运行显示历史单页大小：最多 `page_items` 项，或达到 `page_bytes` 时的更少项。 |
| `worker.content_bytes`                             | 大于该值的图片、文档等二进制内容单独保存一次，不随每个检查点重复写入。       |
| `worker.compression_level`                         | worker 写入运行对象时使用的 zstd 级别。                                      |
| `control.inbox_count`, `control.inbox_bytes`       | 单线程收件箱容量（`inbox_bytes` 默认 2 MiB）。                               |
| `control.subscriptions`                            | 每个工作空间的 webhook 订阅数。                                              |
| `environments.*`                                   | 环境维护频率、provider 调用限制和尝试等待环境的时长。                        |

`provisioning.local.enabled = true` 提供 `local` 环境 provider，直接在 worker 宿主机执行命令，没有隔离。仅用于开发。

`plugins.keys` 按入口 key 列出 agent 可选择的已安装 Harness 插件工厂。`composer.models` 列出 [Agent Composer](agent-composer.md)优先使用的上游模型名称。

## 日志、指标与 trace

`telemetry.log_level` 和 `telemetry.log_format`（默认 `json`，也可为 `pretty`）适用于所有命令。`a13n-service run` 还可写入轮转 JSON 文件（`log_file`、`log_file_max_mb`、`log_file_backups`），设置 `log_stdout = false` 则仅写文件。`telemetry.metrics_port` 在独立端口提供 Prometheus 指标。[监控与排障](monitoring.md)介绍各观测信号的内容。

Service 将每次尝试的 Harness span 导出到一个 trace 后端，并从同一后端读取 [trace 查询](agents-and-runs.md#traces)。使用 `telemetry.trace_backend` 选择：

| 后端           | 设置                                                                                                 |
| -------------- | ---------------------------------------------------------------------------------------------------- |
| `none`（默认） | 不导出，trace 查询报告无后端。                                                                       |
| `langfuse`     | `trace_url`（例如 `https://cloud.langfuse.com`）、`langfuse_public_key`、`langfuse_secret_key`。     |
| `logfire`      | `trace_url`（例如 `https://logfire-us.pydantic.dev`）、`logfire_write_token`、`logfire_read_token`。 |

`telemetry.trace_content`（`none`、`standard` 或 `full`）控制多少提示词、输出和工具内容随 span 离开部署；参阅 [Harness 观测](../a13n-harness/observation.md)。`trace_query_timeout` 限制单次后端查询。

## 关联限制

启动时也联合验证相关限制：租约必须覆盖权限检查和对象存储调用；关闭时长必须覆盖 worker 排空；请求和收件箱额度必须容纳上传与子运行输出；环境扫描必须及时续期云沙箱。修改这些限制时，先检查启动验证错误，再调整单项超时。

## 容器部署

`a13n-service` 镜像通过相同的 `a13n-service` 入口运行所有角色。挂载配置文件（提供的部署使用 `/app/service.toml`），并通过环境变量提供凭据。`all` 和 `control` 也提供 Console，浏览器和 API 客户端共享一个源；将 `server.public_url` 设为该源。

- [单机 Compose 部署](https://github.com/converge-ai-labs/agent-foundation/tree/main/deploy/docker/compose)运行 `run --role all`，包括 PostgreSQL、Redis、Console，以及通过宿主机 Engine 提供的 Docker 环境。
- [Helm chart](https://github.com/converge-ai-labs/agent-foundation/tree/main/deploy/kubernetes)在每个发布修订的迁移 Job 后启动 control 和 worker Deployment，并提供本地 kind 集群的 values。

## 工作空间自动配置

自动配置 Local 和 Docker 环境 provider 及其模板只在单机 `all` 角色执行，两者默认关闭。例如：

```toml
[provisioning.local]
enabled = true
root = "/srv/a13n/environments"

[provisioning.docker]
enabled = true
```

Local 必须显式提供 Service 所在机器的绝对根目录，没有通用默认值。它也会启用 Local provider 类型。`make dev` 提供自己的检出路径。禁用 Local 会保留其目录、provider 和模板，但在重新启用 Local 之前，运行无法使用 Local 环境。

Docker 使用现有运维 Engine 设置 `environments.docker_host`，或进程的 Docker 环境。Compose 内运行需要访问 Engine，通常通过提供的单机部署挂载 socket。仅安装 Docker CLI 不够。Docker 自动配置不影响手动创建的 Docker provider。

环境变量为每个嵌套节使用一个 JSON 对象，例如 `A13N_PROVISIONING__DOCKER='{"enabled":true}'`。默认 Docker 模板固定使用Service 审定的独立版本 GHCR sandbox 镜像；首次创建实例时按需拉取。提供的挂载 socket 的 Compose 部署启用 Docker，关闭 Local；其中 `A13N_DOCKER_ENVIRONMENT_IMAGE` 可覆盖初始模板镜像。

使用本地构建镜像时，运行 `make image-sandbox`。然后在 `[provisioning.docker]` 中设置 `image = "a13n-sandbox:local"` 和 `pull_policy = "never"`。设置只初始化资源一次；要改变未来实例，请通过 Console 或 API 修改已有模板。已有实例保留原始镜像。重试和职责规则请参阅[工作空间自动配置](environments.md#workspace-provisioning)。

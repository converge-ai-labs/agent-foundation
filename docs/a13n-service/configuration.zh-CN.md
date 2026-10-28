---
title: 配置 Service
description: 加载部署设置、连接共享基础设施，并调整执行和监控配置。
---

配置 Service 分三步：连接共享存储、提供持久加密密钥，再启动所需进程角色。设置只在启动时加载，修改后需重启所有受影响的进程。本指南帮助你选择配置方式；各字段、范围和默认值见[设置参考](configuration-reference.md)。

## 配置来源与优先级

1. 将部署设置保存为 TOML 文件，例如下面的 `service.toml`。
2. 通过进程环境变量或平台的密钥存储提供凭据。
3. 在命令**之前**传入 `--config service.toml`，或设置 `A13N_SETTINGS_FILE`。
4. 重启进程，检查就绪状态，再测试应用请求。

`A13N_<SECTION>__<FIELD>` 替换对应 TOML 字段。例如，`A13N_WORKER__SLOTS=6` 覆盖 `[worker] slots = 2`。列表、映射和嵌套节使用完整 JSON 值：`A13N_AUTH__MAIL` 替换整个邮件节，而非合并其中字段。Service 只读取选定文件和环境变量，不自动发现 `.env` 文件，也不搜索配置目录。

以下示例假设 PostgreSQL、Redis、S3 兼容 bucket 和 HTTPS 反向代理已经可用。请替换其地址和公开 origin：

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

将 `trusted_proxies` 设为实际代理地址或 CIDR。启动前提供数据库凭据、对象存储凭据和[加密密钥](#encryption-keys)。单进程部署使用：

```sh
a13n-service --config service.toml run --role all
```

在另一个终端检查公开端点，再登录并发送一个仅使用模型的提示词：

```sh
curl --fail --silent --show-error https://agents.example.com/readyz
```

就绪响应确认进程和 schema 已就绪；提示词请求检查已配置的 provider 和模型链路。未知设置会阻止启动。CLI 只报告配置错误类型，不打印可能含有密钥的输入值。

## 必需的基础设施

所有角色必须使用相同的持久状态和解密密钥：

| 配置                | 用途                                                     |
| ------------------- | -------------------------------------------------------- |
| `server.public_url` | Console/API origin、浏览器来源检查、邮件链接和授权回调。 |
| `database.url`      | 使用 `postgresql+psycopg://` 的 PostgreSQL 持久状态。    |
| `redis.url`         | 共享唤醒、实时输出、限流和监控协调。                     |
| `objects.*`         | 检查点、显示数据、上传、资产、skill 包和图片。           |
| `encryption.*`      | 已保存凭据和其他受保护值的密钥。                         |

### PostgreSQL

使用 Service 专用数据库。`database.url` 含凭据时，通过 `A13N_DATABASE__URL` 提供。按部署情况调整连接和语句超时。迁移职责和升级顺序见 [schema 迁移](operations.md#schema-migrations)。

### Redis

所有进程连接同一 Redis 端点。Redis 加速工作并传输实时会话输出，PostgreSQL 保存持久状态。Redis 故障时，已保存结果仍可读取，worker 通过扫描领取工作；实时输出和限流不可用。就绪检查报告 `"degraded": ["redis"]`。临时环境监控也依赖 Redis 的所有权协调和路由。

### 对象存储

按进程共享存储的方式选择后端：

- **单机或共享卷：** 使用 `objects.backend = "local"` 和 `objects.root`。每个进程必须看到同一目录；相对路径按进程工作目录解析。
- **多机：** 使用 `objects.backend = "s3"` 和 `objects.bucket`，再按需设置 `region`、`endpoint_url` 和 `prefix`。同时提供 `access_key_id` 和 `secret_access_key`，或使用默认 AWS 凭据链。

存储需要普通读取、写入、列出和删除能力。Service 将每个对象写入新 key。`objects.max_bytes` 限制单个存储对象；`upload_bytes`、`upload_limit` 和 `upload_window_seconds` 控制上传大小和每个主体的上传频率。

使用 S3 兼容存储时，让 `objects.addressing_style` 与端点匹配：

| 值        | 地址形式              | 用途                                       |
| --------- | --------------------- | ------------------------------------------ |
| `auto`    | 由 SDK 选择           | 默认行为。                                 |
| `path`    | `endpoint/bucket/key` | 要求路径式 bucket 的端点。                 |
| `virtual` | `bucket.endpoint/key` | 使用 bucket 子域名的端点，包括阿里云 OSS。 |

虚拟寻址要求 bucket 名称、DNS 和 TLS 证书兼容。省略 `addressing_style` 时，`path_style = true` 选择 `path`，`false` 选择 `auto`。不要将 `path_style = true` 与显式 `auto` 或 `virtual` 组合。环境变量为 `A13N_OBJECTS__ADDRESSING_STYLE`，Helm 使用 `objects.addressingStyle`。

如果 S3 兼容提供商拒绝可选流式校验和尾部，在每个 Service 进程中设置 `AWS_REQUEST_CHECKSUM_CALCULATION=when_required`，阿里云 OSS 部署也适用。使用 Helm 时，将其放入用于 `existingSecret` 的环境文件。操作要求的校验和与 Service 摘要校验仍保持启用。Service 对各提供商使用相同的普通 `PutObject` 路径。

### 加密密钥

保存 provider 凭据、连接令牌、webhook 密钥或排队身份邮件前，先配置加密。Service 使用带名称的当前密钥进行 AES-GCM 加密。选择一种密钥来源：

- **单机密钥文件：** 将 `encryption.key_file` 指向各进程共享的持久存储。首次启动时，Service 以 `600` 权限创建缺失文件；后续启动读取同一密钥。
- **托管密钥环：** 设置 `encryption.active_key_id` 和 `encryption.keys`。每个值都是 base64 编码的 32 字节随机密钥。通过密钥存储提供密钥环，所有角色使用相同值。

新建密钥环时，生成一次密钥：

```sh
export A13N_ENCRYPTION__ACTIVE_KEY_ID=primary
export A13N_ENCRYPTION__KEYS="{\"primary\": \"$(openssl rand -base64 32)\"}"
```

将这些值持久保存到部署密钥存储中，不要每次重启都重新生成 `primary`。密钥文件和显式密钥环互斥。迁移密钥文件到密钥环时，以 `key_file` 为 ID 保留原文件内容。

轮换时，以新 ID 添加新密钥并设为当前密钥。保留旧密钥，以读取已有加密值。将密钥与数据库一起备份：丢失密钥会使对应数据无法读取。可以不配置密钥启动，但凭据写入会返回 `unavailable`。

## HTTP 服务器

`server.host` 和 `server.port` 指定监听地址。`server.public_url` 是用户访问的 origin，在代理后方可以与监听地址不同。远程访问使用 HTTPS：可在 Service 配置 `tls_certificate` 和 `tls_key`，也可由可信反向代理终止 TLS。

在 `server.trusted_proxies` 中只列出代理地址或 CIDR。Service 接受这些代理转发的客户端地址和协议，限流按解析后的客户端地址计数。公开 URL 使用 HTTPS 时，登录会话 cookie 带有 `Secure` 和 `__Host-`；明文 HTTP 不具备这些属性。

区分请求限制和执行超时。`server.request_bytes` 限制传入请求体（`413 payload_too_large`），`request_timeout` 限制请求体到达时间（`408 request_timeout`）。`readiness_timeout` 限制就绪检查，`shutdown_timeout` 限制 HTTP 优雅关闭时间。

## 身份与邮件

用户需要密码重置或邮箱修改时，启用 SMTP。未启用时，邀请链接仅向创建者返回一次，供其手动转发。

1. 配置加密密钥来源，使排队链接可以加密。
2. 设置 SMTP 主机、端口、安全模式和发件人。
3. 服务器要求认证时，同时提供 `smtp_username` 和 `smtp_password`。
4. 重启提供 API 的进程，发送邀请验证投递。

```toml
[auth.mail]
smtp_host = "smtp.example.com"
smtp_port = 587
smtp_security = "starttls"
sender = "agents@example.com"
```

使用环境变量覆盖时，将**完整**邮件对象作为 JSON 放入 `A13N_AUTH__MAIL`，包括所需凭据。邮件链接不会写入日志。`auth.session_seconds` 控制登录有效期，`invitation_seconds` 控制邀请有效期，`link_seconds` 控制密码重置和邮箱修改链接有效期。每 `login_window_seconds` 的 `login_limit` 按客户端地址计算；公共授权回调使用相同限制的独立预算。

## 出站请求

在工作空间资源中配置 provider 端点和凭据。部署设置决定 Service 自有客户端可以连接哪些目的地：

- 默认要求 HTTPS。需要时，通过 `providers.http_origins` 允许指定明文 origin。
- URL 不含用户信息、片段或类似凭据的查询参数。凭据放入指定字段。
- Host provider 客户端拒绝重定向和压缩响应，并通过 `providers.response_bytes` 限制响应体。

例如，只允许 Docker 宿主机上的模型服务器，而不开放所有 HTTP 目的地：

```toml
[providers]
http_origins = ["http://host.docker.internal:11434"]
```

保持证书验证启用。共享进程变量 `A13N_OUTBOUND_TLS_VERIFY` 接受 `true`（也是未设置时的默认值）或 `false`，其他值会使启动失败。它没有 TOML 对应项。显式设为 `false` 会关闭受覆盖的自有 HTTP 客户端的证书验证；使用前请阅读[覆盖范围和被拦截风险](../a13n-harness/models.md#outbound-tls-verification)。

应用可通过 `options.configuration` 进一步限制单次运行。以下是输入请求中的 `options` 对象：

```json
{
  "configuration": {
    "allowed_hosts": ["api.example.com", "regex:(api|docs)\\.example\\.com"],
    "extensions": {}
  }
}
```

列出运行需要的每个 provider、连接和远程环境主机名。`null` 允许任意主机，`[]` 拒绝全部。普通条目精确匹配规范化主机名或 IP；`regex:` 匹配完整主机名。glob、端口和 CIDR 不是主机规则。转义和规范化规则见[主机规则](../a13n-harness/context.md#host-rules-and-regular-expressions)。

接收输入时冻结此配置，供输入 URL 读取、执行、故障恢复、resume 和子运行使用。引导消息可省略它或重复相同值。要修改配置，提交 `delivery: "next_run"`；在活跃运行上使用不同值会返回 `run_configuration_immutable`。Console 没有此字段的控件。命名空间下的 `extensions` 只影响支持它们的消费者。

主机规则检查 URL 声明的名称，不预解析 DNS，也不固定 IP。运行之外的管理请求使用部署策略。任意 shell、插件和不透明 SDK 流量的限制，需要在环境或部署网络边界实施。

### 出站代理

在需要代理的每个进程或容器中设置标准代理变量：

```bash
export http_proxy=http://proxy.example.com:8080
export https_proxy=http://proxy.example.com:8080
export no_proxy=localhost,127.0.0.1,::1,.internal.example.com
```

也支持大写形式和 `ALL_PROXY`，选择和绕过匹配遵循 `httpx2`。HTTP 代理可通过 CONNECT 承载 HTTPS。宿主 HTTP 客户端为模型、Remote MCP/OAuth、connector、记忆、web 请求、目录和 webhook 使用这些路由。主机规则和 TLS 验证仍然生效；代理请求失败后不会改为直连重试。

HTTPS Envd 连接使用这些变量；明文本地或 provider 私有 Envd HTTP 保持直连。其他环境和存储 SDK 管理自己的代理行为。Console 模型目录需要访问 `https://models.dev/catalog.json`；无法访问时，使用上次的目录或手动填写上游 ID。

上游较慢时，调整对应预算：`providers.model_timeout` 限制模型交互的每次读取，`tool_call_seconds` 限制一次连接工具调用，`operation_seconds` 限制授权步骤和资源测试，`flow_seconds` 限制浏览器授权。`discovery_ttl` 控制工具发现缓存。`return_urls` 列出精确回调目的地，`mcp_servers` 提供 [MCP 建议](tools.md#mcp-server-suggestions)。

## 执行

从 `worker.slots` 开始，它表示每个 worker 进程的并发尝试数。provider 配额、目标容量和数据库连接能够承载更多工作时，再增大它。其他调整按以下分组查找：

| 目标                 | 设置                                                                                                  |
| -------------------- | ----------------------------------------------------------------------------------------------------- |
| 调整恢复和权限检查   | `worker.max_attempts`、`lease_seconds`、`authority_seconds`。                                         |
| 限制子运行和排队输入 | `worker.child_depth`、`child_count`；`control.inbox_count`、`inbox_bytes`。                           |
| 调整实时流和显示存储 | `worker.stream_*`、`page_items`、`page_bytes`、`content_bytes`、`output_bytes`、`compression_level`。 |
| 调整目标准备和维护   | `environments.*`。                                                                                    |
| 限制保留工具调用     | `tool_executions.max_active`、保留和关闭设置。                                                        |

保留调用默认每个 worker 最多 64 个活跃执行，前台等待五秒。该等待是[执行策略](agents-and-runs.md#execution-policy)的部署回退值，不是截止时间；agent、线程或单次运行可以覆盖。结果在完成后最多保留 24 小时。首次交付确认会启动较短的一小时重读窗口，不延长最大保留期限。

为关闭流程的所有阶段预留时间：先等待 worker 尝试交接，保留调用再获得 `shutdown_grace_seconds`（默认 300 秒），随后取消流程获得 `finalization_seconds`（默认 10 秒）用于清理和结果发布。进程退出后，租约协调将未完成记录标记为 lost。按这些阶段设置平台的终止宽限期，参阅[运维指南](operations.md)。

分离部署时，如果公开 URL 不适合 worker 访问，将 `server.control_url` 设为 worker 可达的控制 API 地址。内部平台 MCP 使用该地址，同时保留公开 HTTP Host。`plugins.keys` 按入口 key 开放已安装的 Harness 插件工厂，`composer.models` 为 [Agent Composer](agent-composer.md)设置优先上游模型名称。

## 日志、指标与 trace

先使用 JSON 日志，需要时启用私有指标监听器。`telemetry.log_level` 和 `log_format` 适用于所有命令。服务器可通过 `log_file`、`log_file_max_mb` 和 `log_file_backups` 写入轮转 JSON 文件；`log_stdout = false` 要求配置文件。`metrics_port` 必须与 HTTP 端口不同。按[监控设置](monitoring.md#metrics)验证抓取。

为导出和 [trace 查询](agents-and-runs.md#traces)选择同一后端：

| `telemetry.trace_backend` | 必需设置                                                    |
| ------------------------- | ----------------------------------------------------------- |
| `none`                    | 默认值，关闭 trace。                                        |
| `langfuse`                | `trace_url`、`langfuse_public_key`、`langfuse_secret_key`。 |
| `logfire`                 | `trace_url`、`logfire_write_token`、`logfire_read_token`。  |

根据允许离开部署的提示词、输出和工具内容，将 `trace_content` 设为 `none`、`standard` 或 `full`。`trace_query_timeout` 限制后端读取。内容级别见 [Harness 观测](../a13n-harness/observation.md)。

## 环境监控

`monitoring.enabled` 默认为 `true`，但目标选择数据源后才开始采集。查看第一张图表时，按[环境监控指南](environment-monitoring.md#bind-and-verify-an-environment)绑定 **Local Cache**。

使用 pull 采集时，先配置 `monitoring.collectors.vmagent` 或 `.alloy`，再创建 pull 数据源。映射默认为空。每项需要绝对路径的共享目录和重载端点。每个采集器只将一个 `all` 或 `control` 进程设为 `writer = true`，其他进程使用默认 `false`。Alloy 还需要已有 `receiver`。挂载、凭据、重载策略和验证步骤见[采集器初始化](environment-monitoring.md#bootstrap-an-optional-collector)。环境变量覆盖使用完整 JSON 映射 `A13N_MONITORING__COLLECTORS`。

采集器就绪后，可向指定工作空间提供可选预设：

```toml
[monitoring.deployment_preset]
name = "Deployment metrics"
collector = "vmagent"
query_endpoint = "https://metrics.example.com"
workspace_ids = ["ws_example"]
```

列表为空时不提供预设。`A13N_MONITORING__DEPLOYMENT_PRESET` 接受整个对象的 JSON。通过 `A13N_MONITORING__DEPLOYMENT_QUERY_BEARER_TOKEN` 单独提供可选读取令牌，再重启 control 进程。管理员[确认预设](environment-monitoring.md#confirm-a-deployment-preset)后创建数据源，并可选保存凭据快照。部署变化不会更新已有数据源或绑定。

## 关联限制

调整限制时，一起调整相关预算。上传大小必须小于请求大小，子运行输出加信封必须能放入收件箱，权限检查和对象操作必须能在租约内完成，worker 交接等待必须短于关闭时间。环境扫描和续期预算必须为托管目标续期留出时间。

启动会拒绝不一致的组合。变更导致 `Invalid Service configuration` 时，在[设置参考](configuration-reference.md)中检查修改字段及包含它们的上层预算。

## 容器部署

镜像为所有角色使用相同可执行程序和设置。将 TOML 挂载到 `--config` 指定的位置（提供的镜像使用 `/app/service.toml`），并将凭据注入容器环境。宿主文件只有挂载到容器后，编辑才会影响容器。修改后重新创建或重启受影响容器；环境变量变化需要重新创建。

[单机 Compose 部署](https://github.com/converge-ai-labs/agent-foundation/tree/main/deploy/docker/compose)以 `all` 角色运行，包含 PostgreSQL、Redis 和通过宿主 Engine 提供的 Docker 环境。[Helm chart](https://github.com/converge-ai-labs/agent-foundation/tree/main/deploy/kubernetes)提供分离的 control/worker Deployment 和迁移 Job。`all` 和 `control` 在 `server.public_url` 提供 Console 与 API。

## 工作空间自动配置

Local/Docker provider 和模板的自动创建只在 `all` 角色运行。通用设置中两者默认关闭；提供的挂载 socket 的 Compose 部署启用 Docker，关闭 Local。分离的 control/worker 部署需要明确配置执行资源。

初始化 Docker 时，先让 Service 进程通过 `environments.docker_host` 或其 Docker 环境访问目标 Engine。只安装 CLI 不会获得 Engine 访问权限。然后启用：

```toml
[provisioning.docker]
enabled = true
```

默认模板使用 Service 审定的 sandbox 镜像和 `pull_policy = "if_missing"`。自动配置只创建一次资源，初始化时不拉取镜像或创建容器；实例需要镜像时才拉取。后续设置变化不覆盖已有模板，需要在 Console 或通过 API 编辑。已有实例保留原镜像。

在仓库检出中使用本地构建镜像：

1. 在仓库根目录运行 `make image-sandbox`。
2. 将以下覆盖配置保存为该根目录下的 `compose.local.yaml`：

```yaml
services:
  service:
    environment:
      A13N_PROVISIONING__DOCKER: '{"enabled":true,"image":"a13n-sandbox:local","pull_policy":"never"}'
```

3. 同时加载两个文件启动：

```sh
docker compose -f deploy/docker/compose/a13n-service.yaml -f compose.local.yaml up -d --wait
```

直接使用 TOML 部署时，将同样的 `image` 和 `pull_policy` 字段放入 `[provisioning.docker]`。镜像仓库中的镜像使用 `if_missing`。环境变量覆盖完整 JSON 对象；旧的 `A13N_DOCKER_ENVIRONMENT_IMAGE` 快捷变量不再读取。

仅在开发部署中使用 Local 执行，并指定绝对根目录：

```toml
[provisioning.local]
enabled = true
root = "/srv/a13n/environments"
```

Local 直接在 worker 宿主机执行，没有隔离。禁用后保留目录和资源，但后续执行无法再使用 Local。禁用 Docker 自动配置后，手动配置的 Docker provider 仍可使用。重试和职责规则见[工作空间自动配置](environments.md#workspace-provisioning)。

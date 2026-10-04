---
title: 观测
description: 按需启用执行、模型与工具的 OpenTelemetry 追踪和指标，支持 Logfire 和 Langfuse 配置。
---

Harness 提供可选的 OpenTelemetry 观测层。Host 负责 SDK 配置、资源、采样、处理器、导出器、上下文传播、刷新和关闭。Harness 绝不创建导出器或 collector 客户端。

`HarnessBuilder()` 在构建时直接从 `os.environ` 读取限定的 `A13N_HARNESS_*` 策略变量，绝不打开 `.env` 或其他配置文件。Host 进程、启动器、容器运行时或开发命令可以在 Harness 启动前加载文件并导出变量。默认不启用观测。传入 `instrumentation=None` 可显式禁用，不受环境变量影响；传入 `HarnessInstrumentation` 则以明确指定的 provider 覆盖环境配置。

## 在 Host 中配置 OpenTelemetry

在作为可执行程序的 Host 中安装并配置 OpenTelemetry SDK，再把具体 provider 交给 Harness：

```python
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider

from a13n_harness import (
    HarnessBuilder,
    HarnessInstrumentation,
    HarnessTraceContent,
)

resource = Resource.create({"service.name": "agent-worker"})
tracer_provider = TracerProvider(resource=resource)
meter_provider = MeterProvider(resource=resource)

# Add Host-selected span processors, metric readers, and exporters here.

builder = HarnessBuilder(
    instrumentation=HarnessInstrumentation(
        tracer_provider=tracer_provider,
        meter_provider=meter_provider,
        trace_content=HarnessTraceContent.STANDARD,
    )
)
```

提供 `HarnessInstrumentation` 时，至少需要一个 provider：

- 只提供 tracer，启用追踪；
- 只提供 meter，启用指标；
- 两者都提供，同时启用追踪和指标；
- `instrumentation=None` 同时禁用两者。

Host 必须在进程退出时刷新并关闭 provider。Harness 不会在每次执行后刷新。

## 分别选择追踪结构与内容

追踪结构只有两种状态：

| 状态    | 所选结构                                                                                                                                   |
| ------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| off     | 不创建 Harness 选择的 span。                                                                                                               |
| enabled | `harness.run`、准备/收尾和 skill 解析阶段、原生 Pydantic AI span，以及用于恢复、委派、handoff、压缩和工具审查的 `harness.operation` span。 |

显式 Python API 中，提供 tracer provider 就会启用完整结构。环境变量使用 `off` 和 `verbose` 表示这两种状态，没有摘要或中间结构模式。

内容策略控制原生 Pydantic AI 捕获和有界的逻辑执行输入/输出。原生设置如下：

| 策略       | 普通文本和工具内容 | 二进制内容 | 序列化模型请求参数 |
| ---------- | ------------------ | ---------- | ------------------ |
| `none`     | 省略               | 省略       | 省略               |
| `standard` | 包含               | 省略       | 省略               |
| `full`     | 包含               | 包含       | 包含               |

内容默认值为 `standard`。追踪关闭时，该策略不生效，不产生数据，也不会让仅指标的配置变为无效。提供 tracer provider 后，完整追踪结构按所选内容策略启用。

`none` 表示 Host 不采集普通执行载荷，不能作为上游 Pydantic AI 埋点字段的通用脱敏边界。启用追踪前，应视 agent 描述、元数据、工具定义和 schema 默认值，以及上游异常文本为遥测可见内容。`standard` 也无法对任意自定义模型或 dataclass 中嵌套的二进制值脱敏。

Host 从 collector 读取 span 并展示给用户时，可通过 `a13n_harness.observation.redact_json` 处理内容。它返回独立副本，替换 `authorization`、`api_key`、`password`、`secret` 和 `token` 等携带权限的成员，保留 `input_tokens` 等用量计数，并替换字符串内的 `Bearer` 凭据。Harness 对扩展事件载荷应用相同规则。

## 通过环境变量启用观测并选择 collector

builder 默认模式为 `instrumentation="environment"`。它读取一次以下 Harness 策略变量，并获取可执行 Host 已配置的 OpenTelemetry 全局 provider：

| 变量                         | 值                         | 默认值     |
| ---------------------------- | -------------------------- | ---------- |
| `A13N_HARNESS_TRACE_LEVEL`   | `off`, `verbose`           | `off`      |
| `A13N_HARNESS_TRACE_CONTENT` | `none`, `standard`, `full` | `standard` |
| `A13N_HARNESS_METRICS`       | `off`, `standard`          | `off`      |

无效值在 `HarnessBuilder()` 构建时失败。开发和生产默认值相同：关闭追踪、使用 standard 内容策略、关闭指标。

使用官方 OpenTelemetry Python distro 和 OTLP exporter 配置 SDK 与 collector 目标。Harness 不重复实现其环境变量处理：

```bash
python -m pip install 'opentelemetry-distro[otlp]'

export A13N_HARNESS_TRACE_LEVEL=verbose
export A13N_HARNESS_TRACE_CONTENT=standard
export A13N_HARNESS_METRICS=standard

export OTEL_SERVICE_NAME=agent-worker
export OTEL_TRACES_EXPORTER=otlp
export OTEL_METRICS_EXPORTER=otlp
export OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
export OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4318

opentelemetry-instrument python -m my_agent_host
```

`OTEL_EXPORTER_OTLP_ENDPOINT` 选择公共 collector 端点；官方 OTLP/HTTP exporter 追加 `/v1/traces` 和 `/v1/metrics`。设置时，信号专属的 `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` 和 `OTEL_EXPORTER_OTLP_METRICS_ENDPOINT` 优先。资源、采样、批处理、请求头、TLS、压缩、超时和 temporality 也由标准 `OTEL_*` 变量管理。

`opentelemetry-instrument` 启动器在应用构建 `HarnessBuilder()` 前配置全局 SDK provider。`make harness-dev` 显式加载 `dev/harness/.env` 后执行相同包装。通过代码配置 provider 的可执行程序可以改用 `HarnessInstrumentation.from_environment(tracer_provider=..., meter_provider=...)`，或传入完整显式 `HarnessInstrumentation`。显式 builder 配置优先于 `A13N_HARNESS_*`；`instrumentation=None` 表示主动退出观测。

未选择后端时，使用以下安全进程环境默认值：

```bash
export A13N_HARNESS_TRACE_LEVEL=off
export A13N_HARNESS_TRACE_CONTENT=standard
export A13N_HARNESS_METRICS=off
export OTEL_TRACES_EXPORTER=none
export OTEL_METRICS_EXPORTER=none
```

后面的后端章节包含完整 Logfire 和 Langfuse 配置。将所选值直接放入进程环境，或由 Host 的部署工具导出。在 Host 启动器中加载配置文件，不要在构建 `HarnessBuilder()` 的代码中加载。

## 准备、skill 解析与清理 span

`harness.run` 下，`harness.prepare` 衡量环境进入、输入准备和插件绑定。原生 agent/模型/工具 span 解释执行；`harness.finalize` 衡量资源清理和状态导出。最后一个 `a13n.phase.step` 和错误分类可定位准备/清理失败，无需为每个函数创建 span。这些 span 不添加新指标。Host 保存续接状态仍在 Harness 之外。

`harness.skills.resolve` 衡量实际 skill 目录解析。执行上的 `a13n.skills.available` 和 `a13n.skills.accessed` 列表区分提供过的 skill 与通过支持的文件工具成功读取的 skill。列表最多保留 16 个名称，并记录数量和截断信息。`access_count` 包含重复读取，不只是不同名称数量。原生文件工具 span 标识 skill 及来源。部分读取也算访问；不会推断 shell 读取、完整加载、遵循程度或从之前历史使用 skill。子执行保留独立摘要。

阶段元数据说明选择或处理了什么；阶段输出描述本地结果，不再复制一份回答。例如，`harness.skills.resolve` 报告全部/显式选择和发现/选中/排除数量，输出中最多包含 16 对所选 skill 名称与来源。未知选择报告为拒绝，不视为空目录成功。准备阶段报告输入模式和 Capability/插件数量；收尾阶段报告状态可用性和清理失败。恢复区分退避与重试输入准备，压缩显示前后消息数。这些结构信息有可过滤的元数据别名。阶段输出遵循与执行输出相同的内容策略和限制，因此 `none` 保留元数据，不保留输出正文。操作完成不代表其子执行或所在执行成功。

## 部署环境

本地开发的 `dev/harness/.env.example` 声明：

```bash
OTEL_RESOURCE_ATTRIBUTES=deployment.environment.name=local
```

将其合并到已有私有 `.env`，不要覆盖凭据或其他资源属性，然后重启 `make harness-dev`。这是追踪后端显示的部署标签，不是 Harness 执行环境。已有观测保留旧标签。显式 Host provider 必须自行携带资源，例如 `Resource.create({"service.name": "agent-worker", "deployment.environment.name": "local"})`；Harness 绝不修改该 provider。

## 使用同一条 OpenTelemetry 管道

无论选择哪个后端，都用 OpenTelemetry API 创建 Host 根 span 和自定义生命周期 span。后端 SDK 配置 provider、处理器、导出器和厂商专属功能，不另建逻辑追踪。

具体行为如下：

- 只使用 Logfire 时，`logfire.configure()` 安装全局 OpenTelemetry 追踪和指标 provider，所有 Harness 与 Host OTel span 都导出到 Logfire；
- 只使用 Langfuse 时，通用 OTLP provider 或 `LangfuseSpanProcessor` 将同一 OTel 追踪导出到 Langfuse；
- 同时使用两者时，一个共享 provider 同时包含 Logfire 处理器和 `LangfuseSpanProcessor`；不要分别创建 Logfire 根和 Langfuse 根；
- Langfuse 的评分、提示管理或追踪更新 API 仍是厂商专属功能，不会自动复制到 Logfire。

基于 OpenTelemetry 的 Langfuse Python SDK 创建的 span 仍是 OpenTelemetry span。两个 SDK 共享 Logfire 全局 provider 时，即使未配置 Langfuse exporter，它也会到达 Logfire。不过，通用 Host span 应通过 OpenTelemetry API（`HarnessInstrumentation.get_tracer()` 或 `opentelemetry.trace`）创建，不要使用厂商 SDK，使后端可替换。

## 推荐的 Logfire 配置

需要一个后端同时接收 Harness 追踪和指标时，建议使用 Logfire。Logfire SDK 基于 OpenTelemetry，并全局安装追踪和指标 provider。无需将通用 OTLP exporter 指向 Logfire Cloud。

在可执行 Host 中安装 Logfire 并设置 write token：

```bash
python -m pip install logfire

export LOGFIRE_TOKEN=your-logfire-write-token
export LOGFIRE_SERVICE_NAME=agent-worker
export LOGFIRE_SEND_TO_LOGFIRE=if-token-present
export A13N_HARNESS_TRACE_LEVEL=verbose
export A13N_HARNESS_TRACE_CONTENT=standard
export A13N_HARNESS_METRICS=standard
```

builder 构建前调用精简的后端初始化代码。通过条件判断，只需设置 `LOGFIRE_TOKEN` 就能启用 Logfire：

```python
import os

from opentelemetry import trace

from a13n_harness import (
    AgentSpec,
    HarnessBuilder,
)

if os.environ.get("LOGFIRE_TOKEN"):
    import logfire

    logfire.configure()

executable = HarnessBuilder().build(
    AgentSpec(model="openai-responses:gpt-5"),
    output_type=str,
)
host_tracer = trace.get_tracer("agent-host")

with host_tracer.start_as_current_span("host.work"):
    result = await executable.run("Complete the task")
```

Logfire 仍需调用一次 `logfire.configure()`；环境变量配置该调用，不替代调用。`LOGFIRE_SERVICE_NAME` 提供 OpenTelemetry `service.name`，无需在 Python 中写死；`OTEL_SERVICE_NAME` 是标准回退别名。`LOGFIRE_TOKEN` 选择项目，`LOGFIRE_SEND_TO_LOGFIRE=if-token-present` 使同一初始化代码在无 token 进程中仍安全可用。

`logfire.configure()` 创建全局 OpenTelemetry provider 并配置 Logfire exporter。这是由 Logfire SDK 管理的 OpenTelemetry 导出，不是另行配置的通用 OTLP 端点。默认 `HarnessBuilder()` 随后从全局 OpenTelemetry 注册表选择这些具体 provider。自动厂商初始化属于可执行 Host 边界；Harness 只自动选择环境策略和已配置的全局 OTel provider。不要调用 `logfire.instrument_pydantic_ai()`；Harness 已负责唯一的 Pydantic AI `Instrumentation` Capability。

使用其他 OTLP 后端而非 Logfire Cloud 时，配置 Logfire `send_to_logfire=False`，并使用标准 `OTEL_EXPORTER_OTLP_*` 变量。参见 [Logfire 替代后端指南](https://pydantic.dev/docs/logfire/guides/alternative-backends/)和 [Logfire 配置参考](https://pydantic.dev/docs/logfire/manage/configuration/)。

## 推荐的 Langfuse 配置

不需要在同一后端接收 Harness 指标时，推荐使用 Langfuse 作为 LLM 追踪后端。其 OTLP 端点接收追踪，但不能作为 Harness 指标后端。除非另行配置支持指标的 provider 或 collector，否则保持 `A13N_HARNESS_METRICS=off` 和 `OTEL_METRICS_EXPORTER=none`。

通过标准 OTLP 变量配置 Langfuse Cloud 或自托管实例：

```bash
export LANGFUSE_PUBLIC_KEY=lf_pk_...
export LANGFUSE_SECRET_KEY=lf_sk_...
export LANGFUSE_BASE_URL=https://cloud.langfuse.com

export A13N_HARNESS_TRACE_LEVEL=verbose
export A13N_HARNESS_TRACE_CONTENT=standard
export A13N_HARNESS_METRICS=off
export OTEL_TRACES_EXPORTER=otlp
export OTEL_METRICS_EXPORTER=none
export OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
export OTEL_EXPORTER_OTLP_ENDPOINT="$LANGFUSE_BASE_URL/api/public/otel"
export OTEL_EXPORTER_OTLP_HEADERS="Authorization=Basic $(printf '%s' "$LANGFUSE_PUBLIC_KEY:$LANGFUSE_SECRET_KEY" | base64 | tr -d '\n'),x-langfuse-ingestion-version=4"

opentelemetry-instrument python -m my_agent_host
```

项目不在默认 EU 区域时，使用 `https://us.cloud.langfuse.com`、`https://jp.cloud.langfuse.com` 或所选区域 URL。仓库本地环境的端点为 `http://127.0.0.1:3000/api/public/otel`。

直接 OTLP 导出将 `harness.run`、Pydantic agent/模型/工具 span 和 `harness.operation` span 送入同一 Langfuse 追踪。没有当前 Host span 时，`harness.run` 是根。Host 使用 Langfuse Python SDK 时，将 `LangfuseSpanProcessor` 附加到已有共享 provider，不另注册 provider。Langfuse 默认导出过滤偏重 LLM，因此过滤时显式包含 `a13n-harness` 和 Host 根埋点 scope，否则可能省略结构 span。

Langfuse v4 要求每个后代 span 都携带追踪级分组字段，以便可靠过滤和聚合。Harness 选择的 span 已携带 Harness 分组字段（见[自动分组与过滤](#automatic-grouping-and-filtering)）。对于独立埋点的 scope，以及版本、发布版本和环境等部署字段，直接 OTLP Host 只传播显式允许的字段，使用 Langfuse SDK 文档中的传播上下文，或 OpenTelemetry baggage 加有界 `SpanProcessor`：

| 用途          | OTLP 属性                                |
| ------------- | ---------------------------------------- |
| 追踪名称      | `langfuse.trace.name`                    |
| 用户分组      | `langfuse.user.id`                       |
| 产品会话      | `langfuse.session.id`                    |
| 标签          | `langfuse.trace.tags`                    |
| 元数据        | `langfuse.trace.metadata.<approved-key>` |
| 版本          | `langfuse.version`                       |
| 发布版本      | `langfuse.release`                       |
| 环境          | `langfuse.environment`                   |
| 观测类型      | `langfuse.observation.type`              |
| 观测输入/输出 | `langfuse.observation.input` 和 `output` |

Harness 将可信常规 `user_id` 身份声明复制到 `langfuse.user.id`，并保留 `a13n.user.id` 作为不绑定厂商的 Harness 字段；它不添加含糊的 `user.id` 别名。需要 `harness.run` 作为根时，`HarnessObservationContext` 提供显式追踪名、产品会话、标签和有界标量元数据。Harness 将 `a13n.observation.name`、`a13n.observation.session.id`、`a13n.observation.labels` 和 `a13n.observation.metadata.*` 映射到对应 Langfuse 字段，并复制到 Harness 选择的后代 span；这些字段无需 Host SpanProcessor。Langfuse 要求每个扁平 `langfuse.trace.metadata.*` OTLP 属性都是字符串，因此 Harness 确定地编码非字符串标量，并在 `a13n.*` 字段中保留其原始类型。绝不能传播任意 baggage、全部身份声明、`host_refs` 或 `RunBindings.metadata`。

Langfuse v4 从根观测派生追踪输入和输出。`standard` 或 `full` 下，`harness.run` 在 `langfuse.observation.input` 和 `langfuse.observation.output` 记录准备后的输入和最终由中间件管理的结果，同时提供中立 `a13n.input` / `a13n.output` 等效字段。各自上限 8 KiB；原生媒体只描述，不存内容。`none` 省略正文。内容缺失或不完整时，检查对应 `a13n.input.capture` / `a13n.output.capture` 和截断字段。不要使用已弃用追踪输入/输出别名，也不要导出全部参数、状态、事件或用量记录。Host 有自己的实际根工作单元时，可在相同策略下使用 `HarnessInstrumentation.record_input()` 和 `record_output()`。

例如，一个 Logfire 管理的 provider 可将同一追踪导出到两个后端：

```python
import os

import logfire
from langfuse.opentelemetry import LangfuseSpanProcessor
from langfuse.span_filter import is_default_export_span

structural_scopes = {"a13n-harness", "agent-host"}
langfuse_processor = LangfuseSpanProcessor(
    public_key=os.environ["LANGFUSE_PUBLIC_KEY"],
    secret_key=os.environ["LANGFUSE_SECRET_KEY"],
    base_url=os.environ.get("LANGFUSE_BASE_URL", "https://cloud.langfuse.com"),
    should_export_span=lambda span: (
        is_default_export_span(span)
        or (
            span.instrumentation_scope is not None
            and span.instrumentation_scope.name in structural_scopes
        )
    ),
)
logfire.configure(additional_span_processors=[langfuse_processor])
```

在进程环境设置 `LOGFIRE_SERVICE_NAME` 或 `OTEL_SERVICE_NAME`，完成配置后再构建 `HarnessBuilder()`。通过 `trace.get_tracer("agent-host")` 创建的 Host 根流向两个处理器，Harness 仍只负责一次 Pydantic AI 埋点。

参见 [Langfuse 原生 OpenTelemetry 指南](https://langfuse.com/integrations/native/opentelemetry)和[已有 OpenTelemetry 配置指南](https://langfuse.com/faq/all/existing-otel-setup)。

### 本地运行 Langfuse

仓库包含基于官方部署组合的独立 Langfuse v4 开发环境：

```bash
make langfuse-up
```

打开 <http://127.0.0.1:3000>，使用以下本地开发凭据登录：

```text
Email: dev@agent-foundation.local
Password: agent-foundation-local
```

环境预先创建 `Agent Foundation Local` 组织和项目，使用以下仅限开发的固定 API key：

```text
Public key: lf_pk_agent_foundation_local
Secret key: lf_sk_agent_foundation_local
```

`make langfuse-up` 使用机器共享环境和 `dev/observability/langfuse.py` 的公开测试配置，不读取 Service 设置或根 `.env`。Harness 和 Harness UI 的开发目标（`make harness-dev`、`make cli`、`make webui`）加载各自的 `.env` 文件。Service 通过自己的 `telemetry` 设置导出和查询追踪；参见 [Service 日志、指标与追踪](../a13n-service/configuration.md#logs-metrics-and-traces)。

**嵌入 Harness 的 Host** 应改为显式导出以下仅追踪配置。可将 Host 专属值保存在私有 `.env`，用 `uv run --env-file .env ...` 显式加载；`.env.harness.example` 说明可选调试设置。Harness UI 使用普通 YAML 配置和进程环境，不隐式加载该文件。

```bash
export A13N_HARNESS_TRACE_LEVEL=verbose
export A13N_HARNESS_TRACE_CONTENT=standard
export A13N_HARNESS_METRICS=off
export OTEL_SERVICE_NAME=agent-worker
export OTEL_TRACES_EXPORTER=otlp
export OTEL_METRICS_EXPORTER=none
export OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
export OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:3000/api/public/otel
export OTEL_EXPORTER_OTLP_HEADERS='Authorization=Basic bGZfcGtfYWdlbnRfZm91bmRhdGlvbl9sb2NhbDpsZl9za19hZ2VudF9mb3VuZGF0aW9uX2xvY2Fs,x-langfuse-ingestion-version=4'

opentelemetry-instrument python -m my_agent_host
```

Harness 执行完成后，追踪出现在本地项目中。此配置不向 Langfuse 发送指标。

停止环境并保留数据，或完全移除：

```bash
make langfuse-down
make langfuse-reset
```

该组合将 Langfuse UI 和媒体端点绑定到回环地址（默认端口 3000、3001），不复用 Service 的 PostgreSQL 或 Redis。本机所有检出共享同一个 Compose 项目及其卷；Service 重置保留 Langfuse 数据。凭据仅限本地开发。生产和高可用部署请遵循官方 [Langfuse 自托管文档](https://langfuse.com/self-hosting)，不要改造该开发组合。

## 为 Harness 根添加有界上下文

需要 `harness.run` 作为根，并添加稳定分组字段，而无需额外外层 span 时，使用新 `HarnessObservationContext`：

```python
from a13n_harness import (
    HarnessObservationContext,
    RunBindings,
)

bindings = RunBindings.embedded(
    observation=HarnessObservationContext(
        name="answer-question",
        session_id="conversation-42",
        labels=("interactive", "support"),
        metadata={"channel": "web", "experiment": "control"},
    )
)
result = await executable.run("Complete the task", bindings=bindings)
```

逻辑执行 span 接收 `a13n.observation.*` 字段及其 Langfuse 别名。名称和会话 ID 最多 256 UTF-8 字节。标签不重复，最多 16 项，每项最多 64 UTF-8 字节。元数据最多 16 个已验证键，值可以是标量字符串、布尔值、有符号 64 位整数或有限浮点数；字符串最多 256 UTF-8 字节。无效上下文在执行前失败。它不存入 `HarnessState`、不向模型公开，也不从 `RunBindings.metadata` 复制。

厂商专属 Host 处理器可映射这些已有字段用于展示。Langfuse 无需映射：Harness 已写入 `langfuse.trace.name`、`langfuse.session.id`、`langfuse.trace.tags` 和 `langfuse.trace.metadata.*`，并将 `harness.run`、`invoke_agent` 标为 `agent`，`execute_tool` 标为 `tool`。Logfire 4.41 将根名称映射到 `logfire.msg`，标签映射到 `logfire.tags`。普通 span 不设置 `logfire.span_type`，不合成 `logfire.level_num`，也不写入 `logfire.metrics`；Logfire 已自行推断普通 span 类型和错误级别，并管理指标聚合。

## 将 Harness 执行嵌套到当前 OpenTelemetry 上下文下

Harness 只使用当前 OpenTelemetry 上下文。应用确有外层工作单元，或需要 Host 管理的追踪输入/输出时，才创建 Host span。进入和消费流期间让该 span 成为当前 span：

```python
host_tracer = tracer_provider.get_tracer("agent-host")

with host_tracer.start_as_current_span("host.work"):
    result = await executable.run("Complete the task")
```

`harness.run` 成为 `host.work` 的子 span。Harness 不接受 span、厂商观测对象或追踪 ID 参数。

流式执行中，Harness 让逻辑执行 span 在清理和消费者背压期间保持打开，但在返回每个公开流条目前将其从当前上下文分离。因此，消费端 span 仍是 Host 上下文的子 span，不会成为 `harness.run` 的子 span。

线程用于关联，不等于追踪。后续恢复通常启动新追踪，除非 Host 激活有界分布式父上下文。持久或独立调度工作优先使用新追踪，并通过标准 span link 关联。

## 自动分组与过滤

所选 Harness 和 Pydantic span 自动接收有界关联和 Langfuse 别名。任何传入 provider 都支持，包括 Service worker provider；无需检测 exporter URL 或额外处理器。同一 span 上仍保留原生 GenAI/Logfire 属性。

使用 `RunBindings.embedded(observation=HarnessObservationContext(...))` 选择稳定名称、会话、标签和最多 16 项标量元数据。未显式设置会话时，执行使用自身线程 ID。别名包括 `langfuse.trace.name`、`langfuse.session.id`、`langfuse.trace.tags` 和可过滤的 `langfuse.observation.metadata.*`。元数据别名是字符串；中立 `a13n.observation.metadata.*` 保留原始标量类型。常规可信用户声明仅在存在时提供 `langfuse.user.id`。不虚构公开追踪设置或提示管理身份。

Host 外层操作使用 `instrumentation.get_tracer("agent-host")` 和 `start_as_current_span()`，携带相同中立属性。有界关联只在本地传播到所选后代，不通过网络 baggage 传播。子值优先。新执行绝不从父级继承缺失身份声明，分离追踪也不继承所关联父级的元数据。第三方 span 仍由各自埋点组件负责。

## 身份、来源关系、用量与成本字段

`harness.run` 和其所选 Harness 后代携带有界可信身份投影：

- `a13n.agent.identity.issuer` 和 `a13n.agent.identity.subject`；
- 常规声明 `a13n.agent.id` 和 `a13n.user.id`，存在时提供；
- `a13n.agent.instance.id` 和可选 `a13n.agent.parent_instance.id`；
- 可选 `a13n.delegation.id` 和 `a13n.actor`。

只有 `agent_id` 和 `user_id` 从 `AgentIdentityRef.claims` 投影。任意声明和 `AgentInstanceContext.host_refs` 被排除。每个投影身份或来源关系值必须可编码为 UTF-8，最多 1024 编码字节，不能含 NUL。不安全或超限值直接省略，不截断，防止遥测制造错误 ID 冲突或改变执行。

Pydantic 管理模型请求用量字段，包括输入/输出 token、专用缓存计数、音频/推理详情计数、原生 provider 或 `genai-prices` 成本，以及模型指标。Harness 模型成本 Capability 应用自定义定价时，在 Pydantic 完成活跃模型请求 span 前写入报价。随后同一 span 包含数值 `gen_ai.usage.cost` 和有界来源信息：

- `a13n.usage.cost.source`；
- `a13n.usage.pricing.status`；
- 可选 `a13n.usage.pricing.revision`；
- 可选 `a13n.usage.pricing.rule.id`。

应用这些额外信息前，Harness 标记精确的活跃 Pydantic 模型请求包装层。观测禁用或仅指标配置下，没有符合条件的记录模型 span，因此定价字段绝不泄露到 Host 根。Harness 不创建 token 别名、另一个 generation span、另一份用量指标或扁平用量账本。span 是遥测，不是计费依据。

可用时，定价来源也出现在可过滤观测元数据 `usage_cost_source`、`usage_pricing_status`、`usage_pricing_revision` 和 `usage_pricing_rule_id` 中。当前报价接口提供总金额，因此不虚构输入/输出成本拆分。原生 Pydantic `operation.cost` 估算和成本指标可使用不同定价来源；该集成不修改上游收尾来强制与 Harness 报价一致。比较后端时检查来源信息。

Pydantic token 类别包含子类：输入包含缓存和输入音频 token，输出包含输出音频 token，推理详情可能与输出重叠。Langfuse v4 拆分缓存计数，但当前派生展示的 `usageDetails.total` 时会加上任意音频/推理详情计数。保留并检查各类别，不要将派生总量视为 Pydantic `input_tokens + output_tokens` 或 Harness 核算依据。

## Harness 指标目录

提供 meter provider 后，在 `a13n-harness` 埋点 scope 下启用以下低基数指标：

| 指标                              | 类型          | 单位        | 属性                  |
| --------------------------------- | ------------- | ----------- | --------------------- |
| `a13n.harness.run.duration`       | Histogram     | `s`         | `a13n.run.outcome`    |
| `a13n.harness.run.active`         | UpDownCounter | `{run}`     | 无                    |
| `a13n.harness.run.model_attempts` | Histogram     | `{attempt}` | `a13n.run.outcome`    |
| `a13n.harness.operation.duration` | Histogram     | `s`         | `a13n.operation.kind` |

时长直方图建议使用以秒计的桶边界：执行为 1 秒至 1 小时，操作为 5 毫秒至 5 分钟，而不是 OpenTelemetry 按毫秒设计的默认值；Host 指标 view 可替换它们。

Pydantic AI 单独负责原生 token 用量、成本和首块时间指标。Harness 不重复记录。ID、agent 名称、失败代码、内容、路径和错误文本绝不作为 Harness 指标维度。

## 埋点职责

Harness 是所构建 agent 的唯一 Pydantic AI 埋点负责方。它拒绝定义、插件和执行范围内的 Pydantic `Instrumentation` Capability，以及直接 `InstrumentedModel` 值，也会对每个构建的 agent 禁用外部 `Agent.instrument_all()` 状态。

Harness 构建的 agent 不要调用 `logfire.instrument_pydantic_ai()`。应将 Logfire 或 Langfuse 配置为共享 OpenTelemetry provider 上的 Host 后端：

- 不需要实际 Host 外层工作单元时，让 `harness.run` 作为根；否则只创建一个 Host 根 span；
- 向 `HarnessInstrumentation` 提供所选具体 provider 对象；
- 导出到多个后端时，在共享 provider 上附加多个处理器或导出器；
- 后端导出过滤中包含 `a13n-harness` 埋点 scope；
- 同一工作单元绝不并行创建 Langfuse 和 Logfire 根。

Harness 没有 Langfuse 或 Logfire 专属运行时依赖。

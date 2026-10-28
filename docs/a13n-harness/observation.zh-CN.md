---
title: 观测
description: 先在本地捕获 Run 的 span 和指标，再通过 Host 的 OpenTelemetry 管道导出。
---

先用内存 exporter 观测一次离线 Run。确认能看到追踪和时长指标后，再将同一套埋点接入 collector、Logfire 或 Langfuse。Host 负责 provider 配置与导出，Harness 负责为自己构建的 Agent 埋点。

观测用于解释执行过程。判断工作是否完成并已持久保存，应检查 [Run 结果与已保存状态](hosting.md#events-and-streaming)。

## 在 Host 中配置 OpenTelemetry

使用[开始使用](getting-started.md)中的 Python 环境，再安装 OpenTelemetry SDK：

```bash
uv pip install opentelemetry-sdk
```

将完整程序保存为 `observe.py`。它不使用网络 exporter，也不需要 provider 凭据：

```python title="observe.py"
import asyncio
from collections.abc import AsyncIterator

from a13n_harness import AgentSpec, HarnessBuilder, HarnessInstrumentation
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.function import AgentInfo, FunctionModel


async def respond(
    messages: list[ModelMessage], info: AgentInfo
) -> AsyncIterator[str]:
    del messages, info
    yield "Observed locally."


async def main() -> None:
    resource = Resource.create({"service.name": "observation-demo"})
    spans = InMemorySpanExporter()
    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(SimpleSpanProcessor(spans))
    reader = InMemoryMetricReader()
    meter_provider = MeterProvider(resource=resource, metric_readers=[reader])
    try:
        instrumentation = HarnessInstrumentation(
            tracer_provider=tracer_provider,
            meter_provider=meter_provider,
        )
        executable = HarnessBuilder(instrumentation=instrumentation).build(
            AgentSpec(), output_type=str, model=FunctionModel(stream_function=respond)
        )
        result = await executable.run("Check observation")
        print(result.output_or_raise())
        tracer_provider.force_flush()
        roots = [span for span in spans.get_finished_spans() if span.name == "harness.run"]
        assert len(roots) == 1
        assert roots[0].parent is None
        print("Logical Run spans:", len(roots))
        data = reader.get_metrics_data()
        assert data is not None
        names = {
            metric.name
            for resource_metrics in data.resource_metrics
            for scope_metrics in resource_metrics.scope_metrics
            for metric in scope_metrics.metrics
        }
        assert "a13n.harness.run.duration" in names
        print("Run duration metric recorded.")
    finally:
        meter_provider.shutdown()
        tracer_provider.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
```

运行 `uv run python observe.py`，应输出：

```text
Observed locally.
Logical Run spans: 1
Run duration metric recorded.
```

程序构建 Model、完成 Run、检查导出的 span 和指标，最后关闭两个 provider。

通过传给 `HarnessInstrumentation` 的 provider 选择信号：只有 tracer 时启用追踪，只有 meter 时启用指标，两者都有则同时启用。至少需要一个 provider。向 `HarnessBuilder` 传入 `instrumentation=None` 可显式关闭两者。在 Host 进程生命周期结束时刷新并关闭 provider；Harness 不会在每次 Run 后刷新它们。

## 分别选择追踪结构与内容

分别选择追踪结构与载荷策略。提供 tracer provider 后，启用完整结构：`harness.run`、准备/收尾与 Skill 解析、原生 Agent/Model/工具 span，以及恢复、委派、handoff、压缩和工具审查操作。未提供 tracer provider 时，Harness 选择的追踪关闭。环境配置分别用 `verbose` 和 `off` 表示这两种状态。

| 内容策略   | 普通文本与工具内容 | 二进制内容 | 序列化 Model 请求参数 |
| ---------- | ------------------ | ---------- | --------------------- |
| `none`     | 省略               | 省略       | 省略                  |
| `standard` | 包含               | 省略       | 省略                  |
| `full`     | 包含               | 包含       | 包含                  |

默认值为 `standard`。要修改本地示例，从 `a13n_harness` 导入 `HarnessTraceContent`，在 `HarnessInstrumentation(...)` 中加入 `trace_content=HarnessTraceContent.NONE`。Run span 和指标仍然存在，普通输入/输出正文被省略。关闭追踪时，内容设置不生效。

导出前检查哪些数据会进入遥测。`none` 不会对上游的 Agent 描述、元数据、工具定义、schema 默认值或异常文本统一脱敏。`standard` 也不能对任意自定义模型或 dataclass 内嵌的二进制值脱敏。

向用户展示 collector 内容时，`a13n_harness.observation.redact_json` 会返回独立副本，替换 `authorization`、`api_key`、`password`、`secret` 和 `token` 等携带权限的字段。它还会替换字符串中的 Bearer 凭据，同时保留 `input_tokens` 等用量计数。Harness 对扩展事件载荷应用相同规则。

## 通过环境变量启用观测并选择 collector

启动器负责遥测后端时，使用环境配置。`HarnessBuilder()` 默认采用 `instrumentation="environment"`，从 `os.environ` 读取一次以下策略，再选择 Host 配置的全局 provider；它不加载 `.env` 文件。

| Variable                     | Values                     | Default    |
| ---------------------------- | -------------------------- | ---------- |
| `A13N_HARNESS_TRACE_LEVEL`   | `off`, `verbose`           | `off`      |
| `A13N_HARNESS_TRACE_CONTENT` | `none`, `standard`, `full` | `standard` |
| `A13N_HARNESS_METRICS`       | `off`, `standard`          | `off`      |

非法值在构建 builder 时失败。开发与生产使用相同默认值。显式 `HarnessInstrumentation` 覆盖这些变量，显式 `None` 关闭观测。

通过 OTLP 导出离线 Model 的追踪：

1. 在同一个 Python 环境中安装 `opentelemetry-distro[otlp]`。
2. 将 `collector_demo.py` 保存在 `observe.py` 旁边：

```python title="collector_demo.py"
import asyncio

from a13n_harness import AgentSpec, HarnessBuilder
from pydantic_ai.models.function import FunctionModel

from observe import respond


async def main() -> None:
    executable = HarnessBuilder().build(
        AgentSpec(), output_type=str, model=FunctionModel(stream_function=respond)
    )
    result = await executable.run("Check collector export")
    print(result.output_or_raise())


if __name__ == "__main__":
    asyncio.run(main())
```

3. 激活该环境，在 shell 中导出以下设置。将 `http://otel-collector:4318` 换成可连接的 collector 地址。命令使用 POSIX shell 语法。

```bash
export A13N_HARNESS_TRACE_LEVEL=verbose
export A13N_HARNESS_TRACE_CONTENT=standard
export A13N_HARNESS_METRICS=standard
export OTEL_SERVICE_NAME=agent-worker
export OTEL_TRACES_EXPORTER=otlp
export OTEL_METRICS_EXPORTER=otlp
export OTEL_LOGS_EXPORTER=none
export OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
export OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4318

opentelemetry-instrument python collector_demo.py
```

4. 在 collector 后端中找到服务 `agent-worker`，检查已完成的 `harness.run`。终端回答验证 Model 执行，后端 span 验证导出。如果有回答却没有追踪，另行检查 exporter 错误。

启动器在应用代码执行前初始化全局 SDK provider，并管理其进程生命周期。OTLP/HTTP 在公共端点后追加 `/v1/traces` 和 `/v1/metrics`；信号专属的 `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` 和 `OTEL_EXPORTER_OTLP_METRICS_ENDPOINT` 优先。资源、采样、批处理、请求头、TLS、压缩、超时和 temporality 由标准 `OTEL_*` 变量配置。

通过代码管理 provider、通过环境选择信号时，使用 `HarnessInstrumentation.from_environment(tracer_provider=..., meter_provider=...)`。`make harness-dev` 会在 OTel 启动器前显式加载 `dev/harness/.env`；其他 Host 自行安排文件加载。

关闭启动器配置时，将两个 Harness 信号设为 `off`，并将 `OTEL_TRACES_EXPORTER`、`OTEL_METRICS_EXPORTER` 和 `OTEL_LOGS_EXPORTER` 都设为 `none`。从下面选择一个后端配置，不要叠加彼此独立的 provider 初始化流程。

## 准备、skill 解析与清理 span

从最外层 Run 向内阅读追踪：

| Span                       | 检查内容                              |
| -------------------------- | ------------------------------------- |
| `harness.run`              | 逻辑 Run 的生命周期和结果。           |
| `harness.prepare`          | 环境进入、输入准备和插件绑定。        |
| `harness.skills.resolve`   | 实际 Skill 目录选择。                 |
| 原生 Agent/Model/工具 span | Model 尝试、请求和工具执行。          |
| `harness.operation`        | 恢复、委派、handoff、压缩或工具审查。 |
| `harness.finalize`         | 资源清理和状态导出。                  |

准备或清理失败时，检查最后一个 `a13n.phase.step` 和错误分类。阶段输出描述本地结果：选择数量、状态是否可用、清理失败、恢复退避与重试输入准备，或压缩前后的消息数。未知 Skill 选择会被拒绝，不会作为成功的空目录处理。这些字段具有可过滤的元数据别名。输出正文遵循 Run 的内容策略和大小限制；`none` 保留结构元数据。阶段 span 不增加新指标，Host 保存检查点仍在 Harness 之外。

Run 的 `a13n.skills.available` 和 `a13n.skills.accessed` 列表分别记录提供的 Skill，以及通过支持的文件工具成功读取的 Skill。每个列表最多保留 16 个名称，并附带数量和截断信息。Skill 解析输出同样最多包含 16 对选中的名称与来源。`access_count` 包含重复读取，部分读取也算访问。准确的 Skill 和来源见原生文件工具 span。这些观测不推断 shell 读取、完整加载、遵循程度或从旧历史中的使用。子 Run 保留自己的摘要；一个操作完成后，仍需单独判断所在 Run 的结果。

## 部署环境

在 Host 的 OpenTelemetry resource 中加入 `deployment.environment.name`，区分本地、预发布和生产追踪。在 `observe.py` 中替换 resource 构造：

```python
resource = Resource.create({
    "service.name": "observation-demo",
    "deployment.environment.name": "local",
})
```

使用环境启动器时，设置 `OTEL_RESOURCE_ATTRIBUTES=deployment.environment.name=local`，并保留已配置的其他属性。`dev/harness/.env.example` 使用该设置；合并到私有文件时不要覆盖凭据，随后重启 `make harness-dev`。此标签描述部署环境，不是 Harness 执行 Environment。已有观测保留旧标签，Harness 不修改传入 provider 的 resource。

## 使用同一条 OpenTelemetry 管道

同一工作单元使用一套 provider 层级。通过 `HarnessInstrumentation.get_tracer()` 或 OpenTelemetry API 创建通用 Host span，通过处理器和 exporter 选择目的地。

- **Logfire：** `logfire.configure()` 安装全局追踪和指标 provider。
- **Langfuse：** 通过通用 OTLP provider 导出，或向已有 provider 添加 `LangfuseSpanProcessor`。
- **同时使用：** 将两个 exporter/处理器接到同一 provider，Host 和 Harness span 共享一条追踪。

基于 OpenTelemetry 的 Langfuse SDK span 使用 Logfire 的共享 provider 时，也会到达 Logfire。Langfuse 的评分、提示管理和追踪更新仍是独立厂商 API。通用生命周期代码使用 OpenTelemetry，这样更换后端无需改变应用的追踪结构。

## 推荐的 Logfire 配置

通过 Logfire SDK 同时发送追踪和指标时，使用此配置。它安装全局 OpenTelemetry provider；Harness 读取已启用的环境策略后选择这些 provider。

在 `collector_demo.py` 使用的同一个 Python 环境中安装 Logfire，通过进程环境提供项目 write token：

```bash
uv pip install logfire

export LOGFIRE_TOKEN=your-logfire-write-token
export LOGFIRE_SERVICE_NAME=agent-worker
export LOGFIRE_SEND_TO_LOGFIRE=if-token-present
export A13N_HARNESS_TRACE_LEVEL=verbose
export A13N_HARNESS_TRACE_CONTENT=standard
export A13N_HARNESS_METRICS=standard
```

将 `logfire_demo.py` 保存在 `collector_demo.py` 和 `observe.py` 旁边，激活环境后运行 `python logfire_demo.py`。它复用离线 Model，因此测试遥测无需 Model API key：

```python title="logfire_demo.py"
import asyncio
import logfire

from collector_demo import main

logfire.configure()
asyncio.run(main())
```

在选定的 Logfire 项目中，按服务 `agent-worker` 查找 Run。`logfire.configure()` 在 `main()` 构建 builder 前初始化后端，环境变量用于配置该调用。`LOGFIRE_SERVICE_NAME` 提供 OpenTelemetry `service.name`，无需在 Python 中写死；`OTEL_SERVICE_NAME` 是标准回退别名。`LOGFIRE_TOKEN` 选择项目，`LOGFIRE_SEND_TO_LOGFIRE=if-token-present` 允许未提供 token 的进程运行初始化流程，但不向 Logfire 导出。SDK 在进程退出时关闭 exporter；长时间运行的 Host 应在应用生命周期结束时刷新并关闭 provider。

在可执行 Host 中调用初始化，并将 Pydantic AI 埋点交给 Harness；参阅[埋点职责](#instrumentation-ownership)。

使用其他 OTLP 后端而非 Logfire Cloud 时，配置 Logfire `send_to_logfire=False`，并使用标准 `OTEL_EXPORTER_OTLP_*` 变量。参见 [Logfire 替代后端指南](https://pydantic.dev/docs/logfire/guides/alternative-backends/)和 [Logfire 配置参考](https://pydantic.dev/docs/logfire/manage/configuration/)。

## 推荐的 Langfuse 配置

不需要在同一后端接收 Harness 指标时，推荐使用 Langfuse 作为 LLM 追踪后端。其 OTLP 端点接收追踪，但不能作为 Harness 指标后端。除非另行配置支持指标的 provider 或 collector，否则保持 `A13N_HARNESS_METRICS=off` 和 `OTEL_METRICS_EXPORTER=none`。

复用 collector 练习中的 `collector_demo.py` 和已安装的 `opentelemetry-distro[otlp]`。提供 Langfuse 项目 key，选择项目区域或自托管 base URL，然后在已激活的 POSIX shell 中运行这份仅追踪配置：

```bash
export LANGFUSE_PUBLIC_KEY=lf_pk_...
export LANGFUSE_SECRET_KEY=lf_sk_...
export LANGFUSE_BASE_URL=https://cloud.langfuse.com

export A13N_HARNESS_TRACE_LEVEL=verbose
export A13N_HARNESS_TRACE_CONTENT=standard
export A13N_HARNESS_METRICS=off
export OTEL_TRACES_EXPORTER=otlp
export OTEL_METRICS_EXPORTER=none
export OTEL_LOGS_EXPORTER=none
export OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
export OTEL_EXPORTER_OTLP_ENDPOINT="$LANGFUSE_BASE_URL/api/public/otel"
export OTEL_EXPORTER_OTLP_HEADERS="Authorization=Basic $(printf '%s' "$LANGFUSE_PUBLIC_KEY:$LANGFUSE_SECRET_KEY" | base64 | tr -d '\n'),x-langfuse-ingestion-version=4"

opentelemetry-instrument python collector_demo.py
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

要同时导出到两个后端，在 Logfire 之外安装 Langfuse SDK。用下面的初始化代码替换 `logfire_demo.py` 中的 `logfire.configure()`，保留其后的 `asyncio.run(main())`。Model 和所有 Run 继续使用同一 provider：

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

使用本地后端时，先安装 Docker 和 Compose，再从仓库根目录运行以下命令。这会启动本机共享的 Langfuse v4 开发环境：

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
export OTEL_LOGS_EXPORTER=none
export OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
export OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:3000/api/public/otel
export OTEL_EXPORTER_OTLP_HEADERS='Authorization=Basic bGZfcGtfYWdlbnRfZm91bmRhdGlvbl9sb2NhbDpsZl9za19hZ2VudF9mb3VuZGF0aW9uX2xvY2Fs,x-langfuse-ingestion-version=4'

opentelemetry-instrument python collector_demo.py
```

导出后，在本地项目中检查已完成的 `harness.run`。如果只看到终端回答，检查 exporter 端点、请求头和后端就绪状态。此配置不向 Langfuse 发送指标。

停止环境并保留数据：

```bash
make langfuse-down
```

只有确定要删除共享开发环境及其数据时，才运行 `make langfuse-reset`。该操作也影响本机其他检出。

该组合将 Langfuse UI 和媒体端点绑定到回环地址（默认端口 3000、3001），不复用 Service 的 PostgreSQL 或 Redis。本机所有检出共享同一个 Compose 项目及其卷；Service 重置保留 Langfuse 数据。凭据仅限本地开发。生产和高可用部署请遵循官方 [Langfuse 自托管文档](https://langfuse.com/self-hosting)，不要改造该开发组合。

## 为 Harness 根添加有界上下文

要按应用对话分组 Run，在 `observe.py` 中加入下面的导入，并在 `main()` 内用此绑定和调用替换 `result = await executable.run(...)` 那一行：

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

再次运行程序，检查 `roots[0].attributes`。逻辑 Run span 接收 `a13n.observation.*` 字段及其 Langfuse 别名。名称和会话 ID 最多 256 UTF-8 字节。标签不重复，最多 16 项，每项最多 64 UTF-8 字节。元数据最多 16 个已验证键，值可以是标量字符串、布尔值、有符号 64 位整数或有限浮点数；字符串最多 256 UTF-8 字节。无效上下文在执行前失败。它不存入 `HarnessState`、不向模型公开，也不从 `RunBindings.metadata` 复制。

厂商专属 Host 处理器可映射这些已有字段用于展示。Langfuse 无需映射：Harness 已写入 `langfuse.trace.name`、`langfuse.session.id`、`langfuse.trace.tags` 和 `langfuse.trace.metadata.*`，并将 `harness.run`、`invoke_agent` 标为 `agent`，`execute_tool` 标为 `tool`。Logfire 4.41 将根名称映射到 `logfire.msg`，标签映射到 `logfire.tags`。普通 span 不设置 `logfire.span_type`，不合成 `logfire.level_num`，也不写入 `logfire.metrics`；Logfire 已自行推断普通 span 类型和错误级别，并管理指标聚合。

## 将 Harness 执行嵌套到当前 OpenTelemetry 上下文下

应用确有外层操作时，在进入和消费 Harness 期间将其 Host span 设为当前 span。在 `observe.py` 中替换 Run 调用：

```python
host_tracer = instrumentation.get_tracer("agent-host")
with host_tracer.start_as_current_span("host.work"):
    result = await executable.run("Check observation")
```

同时将 `assert roots[0].parent is None` 改为 `assert roots[0].parent is not None`。Harness Run 现在是 `host.work` 的子 span，无需传入 span 或 trace ID 参数。

流式执行时，逻辑 Run span 在清理和消费者背压期间保持打开。Harness 在返回每个公开流条目前将它从当前上下文分离，因此消费者创建的 span 仍处于 Host 上下文下。

用 Thread ID 关联对话轮次，不要将它当作 trace ID。后续恢复通常启动新追踪。独立调度或持久工作使用新追踪并通过标准 span link 关联；只有确实存在有界的外层操作时，才激活分布式父上下文。

## 自动分组与过滤

无论使用哪个 exporter，都可以按有界上下文和身份字段过滤 Harness 选择的 span。Harness 在同一 span 上写入不绑定厂商的 `a13n.*` 字段和 Langfuse 别名，并保留原生 GenAI/Logfire 属性。

显式观测会话按应用提供的值分组 Run；否则使用 Run 的 Thread ID。名称、标签和元数据映射为 `langfuse.trace.name`、`langfuse.session.id`、`langfuse.trace.tags` 和可过滤的 `langfuse.observation.metadata.*`。Langfuse 元数据别名为字符串，`a13n.observation.metadata.*` 保留标量类型。存在可信的常规用户声明时，提供 `langfuse.user.id`。

对于 Host 自己的操作，`instrumentation.get_tracer("agent-host")` 在本地向选定后代传播有界的中立关联字段，子值优先。新 Run 不继承缺失的身份声明；通过 link 关联的独立追踪不继承父级元数据；本地传播不创建网络 baggage。第三方独立埋点的 span 需要各自接入。

## 身份、来源关系、用量与成本字段

先用身份字段找到目标 Run，再检查请求级用量和定价来源。遥测查询与持久核算分别处理。

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

本地程序通过 `InMemoryMetricReader` 读取这些指标。部署 Host 时，为传入的 meter provider 配置 reader/exporter，再查询 `a13n-harness` scope：

| 指标                              | 类型          | 单位        | 属性                  |
| --------------------------------- | ------------- | ----------- | --------------------- |
| `a13n.harness.run.duration`       | Histogram     | `s`         | `a13n.run.outcome`    |
| `a13n.harness.run.active`         | UpDownCounter | `{run}`     | 无                    |
| `a13n.harness.run.model_attempts` | Histogram     | `{attempt}` | `a13n.run.outcome`    |
| `a13n.harness.operation.duration` | Histogram     | `s`         | `a13n.operation.kind` |

时长直方图建议使用以秒计的桶边界：执行为 1 秒至 1 小时，操作为 5 毫秒至 5 分钟，而不是 OpenTelemetry 按毫秒设计的默认值；Host 指标 view 可替换它们。

Pydantic AI 单独负责原生 token 用量、成本和首块时间指标。Harness 不重复记录。ID、agent 名称、失败代码、内容、路径和错误文本绝不作为 Harness 指标维度。

## 埋点职责

每个 Harness 构建的 Agent 只保留一个埋点负责方：

1. 构建前配置 Host 的 OpenTelemetry provider。
2. 由 Harness 为其 Agent 创建原生 Pydantic AI 埋点。
3. 向这些 provider 添加后端处理器/exporter，导出过滤中包含 `a13n-harness` scope。
4. 只有存在实际外层操作时，才创建一个 Host 根 span。
5. 在 Host 生命周期结束时刷新并关闭。

不要围绕同一个 Agent 额外调用 `logfire.instrument_pydantic_ai()`、加入 Pydantic `Instrumentation` Capability 或包装 `InstrumentedModel`。Harness 会拒绝定义、插件和 Run 范围内的重复埋点，并在所构建 Agent 上禁用外部 `Agent.instrument_all()` 状态。Logfire 和 Langfuse 都是 Host 的可选依赖。

观测缺失时，依次检查：信号选择、builder 构建前的 provider 初始化、处理器/reader 连接、exporter 端点与凭据、后端 scope 过滤、关闭与刷新。回到内存示例，可以区分 Harness 埋点问题和远程导出问题。

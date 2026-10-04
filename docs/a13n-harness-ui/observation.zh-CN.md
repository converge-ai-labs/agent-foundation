---
title: 跟踪 Harness UI
sidebarTitle: 追踪
description: 将 Harness UI 的 OpenTelemetry 追踪导出到 Langfuse、Logfire 或任意 OTLP collector。
---

Harness UI 使用与 [Harness](../a13n-harness/observation.md) 相同的 OpenTelemetry 层级。每个已受理的根提交和每个已接受的异步子级分段外，会增加一个应用 span（`harness_ui.root` 或 `harness_ui.subagent`），不会重复创建模型或工具 span。追踪为可选功能，默认关闭。

## 开启自动 OTLP 导出

启动 CLI、WebUI 或 `open_harness_ui_app()` 前设置以下环境变量：

```bash
export A13N_HARNESS_TRACE_LEVEL=verbose
export A13N_HARNESS_TRACE_CONTENT=none
export A13N_HARNESS_METRICS=off
export OTEL_SERVICE_NAME=a13n-harness-ui
export OTEL_TRACES_EXPORTER=otlp
export OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
export OTEL_EXPORTER_OTLP_ENDPOINT=https://your-collector.example
export OTEL_EXPORTER_OTLP_HEADERS='Authorization=YOUR_COLLECTOR_CREDENTIAL'
a13n-harness-ui
```

未配置 Host tracer provider 时，App 创建自己的 OTLP/HTTP provider，并将其传给每次 Harness 构建。标准 `OTEL_*` 的资源、采样、批处理、TLS、超时和 HTTP exporter 配置均适用。专用于追踪信号的端点必须包含 `/v1/traces`；上面的公共端点是基础 URL。设置 `OTEL_SDK_DISABLED=true` 可阻止自动 SDK 设置。

自动配置支持 `OTEL_TRACES_EXPORTER=otlp` 或 `none`，以及 `http/protobuf` 协议。它不会初始化指标 exporter。如果开启 Harness 指标，需单独提供或配置 meter provider。

关闭时，App 在活跃 Run、终端会话和实时订阅关闭后，在事件循环之外、关闭等待预算之内排空自己的 exporter。导出仍为尽力交付；追踪不能证明检查点或副作用已保存。

## Langfuse

使用 Langfuse 项目端点和 Basic 身份验证：

```bash
export OTEL_EXPORTER_OTLP_ENDPOINT=https://cloud.langfuse.com/api/public/otel
export OTEL_EXPORTER_OTLP_HEADERS='Authorization=Basic%20BASE64_PUBLIC_KEY_COLON_SECRET_KEY,x-langfuse-ingestion-version=4'
```

将占位符替换为项目 `public-key:secret-key` 的 base64 编码；使用对应区域或自托管服务器的端点。v4 头选择实时 OTLP 接收。自动和显式 provider 都会通过 Harness 共享增强逻辑，为选中的后代 span 添加有界追踪名称、作为 Langfuse session ID 的 Thread ID、标签和可筛选元数据，同时保留原生模型、token 和工具观测。这条 OTLP 路径不需要 Langfuse SDK。

查找 `harness_ui.root`、其中的 `harness.run`，以及原生 Agent/模型/工具 span。异步子级有独立且关联的追踪，并以自己的 Thread ID 作为 Langfuse session ID。同一 Thread 的后续轮次是独立追踪。即使内部 Harness Run 已完成，根应用状态仍可能失败，例如续接保存失败时。正常取消不会转为错误。

按观测元数据 `root_thread_id` 筛选，可跨各自独立的追踪关联根 Thread 及其所有子 Thread。子级追踪还公开 `parent_thread_id`、`subagent_role`、`execution_id`、`segment_index`，恢复时还有 `resumed_from_execution_id`。恢复子级会保留其 Thread ID 和 Langfuse session，但创建新的执行分段和追踪。派发 Link 代表有效的实时上下文，并非指向之前进程追踪的持久链接。角色是元数据，不是 Langfuse session ID 前缀。

## Logfire

使用同样的观测机制，导出到 Logfire 的标准 OTLP 接收接口：

```bash
export OTEL_EXPORTER_OTLP_ENDPOINT=https://logfire-us.pydantic.dev
export OTEL_EXPORTER_OTLP_HEADERS='Authorization=YOUR_LOGFIRE_WRITE_TOKEN'
```

EU 项目使用 `https://logfire-eu.pydantic.dev`。从 Langfuse 切换时，同时替换端点和 headers，并移除冲突的 `OTEL_EXPORTER_OTLP_TRACES_*` 覆盖。不要再添加 `logfire.instrument_pydantic_ai()` 等第二个 instrumentor。

如果嵌入应用已经持有 Logfire 或 OTel provider，可以直接提供该 provider：

```python
from a13n_harness import HarnessInstrumentation, HarnessTraceContent
from a13n_harness_ui.app import open_harness_ui_app

# provider is the tracer provider already configured by your embedding Host.
instrumentation = HarnessInstrumentation(
    tracer_provider=provider,
    trace_content=HarnessTraceContent.NONE,
)
async with open_harness_ui_app(settings, instrumentation=instrumentation) as app:
    thread = await app.create_thread()
```

显式观测配置优先于环境选择。显式 `None` 关闭 UI/Harness 观测。如果已配置全局 OpenTelemetry provider，`A13N_HARNESS_*` 策略变量会选择它。App 不会替换外部 provider、向其添加 exporter，或关闭它；其 Host 负责导出和生命周期。不论 exporter 是什么，选中的 UI/Harness span 都会获得相同的自动元数据，不需要增强 processor。之前可选的 `HarnessUiSpanProcessor` 已不再提供。独立观测的 SDK 产生的 span 不会获得之前 processor 提供的本地父级属性传播或 Langfuse agent/工具类型增强；需要这些行为的 Host 应自行负责 SDK 观测。不要再对 Harness 所有的 Agent 调用 `logfire.instrument_pydantic_ai()`。

## 仓库开发

仓库提供单独、被 Git 忽略的私有环境文件：

```bash
cp dev/harness-ui/.env.example dev/harness-ui/.env
cp dev/harness/.env.example dev/harness/.env
make langfuse-up
make cli
make harness-ui-smoke
make harness-dev HARNESS_ARGS=summary
```

仅在私有文件不存在时复制。模板默认使用公共本地 Langfuse 凭据，并包含注释掉的 Logfire 替代配置。`make cli` 显式加载 `dev/harness-ui/.env`，不会将追踪设置写入用户配置。`CLI_ARGS` 转发普通 CLI 参数，`HARNESS_UI_ENV` 选择另一份私有文件。`make harness-dev` 通过 `opentelemetry-instrument` 初始化 provider；`make cli` 依赖 App 设置。

## 部署环境

开发模板声明 `OTEL_RESOURCE_ATTRIBUTES=deployment.environment.name=local`。这会将新根级和子级观测标为 `local`，与执行 Environment 配置或挂载名称无关。已有私有 `dev/harness-ui/.env` 时，应合并此资源属性而不替换其他设置，然后重启 CLI/App。已导出的 shell 值优先；旧观测保留原标签。外部 provider 的部署资源必须由所属 Host 设置。

## 从根级向下阅读执行

从 `harness_ui.root`（子级追踪为 `harness_ui.subagent`）开始：

1. 检查根状态和 Thread/Run ID。此 span 包含准备和检查点保存，不只有推理。
2. 展开 `configuration` 查看捕获的 Agent、Model、Capability、选中集成和 Environment。这是有上限的摘要，不是重放配方；不含凭据、提示、端点和文件系统根目录。
3. 沿 `harness.run` 和模型/工具 span 查看耗时、恢复、用量和成本。如果 resolver 改变了 Model，检查请求级模型字段。
4. 按 `root_thread_id` 和子级执行 ID 筛选，跨追踪查看委派任务。

### Host 阶段与 Skill 检查

`harness_ui.prepare` 加载配置和状态；`harness_ui.finalize` 保存续接并结束 Environment。出现回答但保存失败时，检查 `a13n.phase.step` 和 `a13n.ui.continuation.status`。操作根和 `harness.run` 中的 Skill 摘要显示**该 Thread** 可用/已读取的 Skill。读取不代表 Agent 遵循了 Skill。

## 内容与限制

`A13N_HARNESS_TRACE_CONTENT` 为 `standard` 或 `full` 时，操作根还包含当前提交和最终输出，各自最多 8 KiB。结果并非从最后一条模型响应推断。生成回答后保存失败时，回答可能仍可见，但操作状态为 failed。内容缺失或不完整时，检查 `a13n.input.capture`、`a13n.output.capture` 和截断标记。`none` 省略这些正文。原生媒体仅有描述；二进制内容、完整历史、凭据、原始异常和检查点正文不会复制到根中。`standard` 开启原生模型/工具内容捕获；`full` 还开启上游二进制和请求参数捕获。`none` 省略常规执行载荷，但**不能** 完整清除上游异常、Agent 描述、元数据或工具 schema。在导出真实对话前，应阅读 [Harness 信息边界](../a13n-harness/observation.md)，特别是导出到远程后端时。

缺少追踪不代表缺少对话。对话的根 Thread 的对话记录和检查点存储独立于采样、导出可用性和后端保留策略。

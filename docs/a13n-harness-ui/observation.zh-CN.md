---
title: 跟踪 Harness UI
sidebarTitle: 追踪
description: 将测试 Run 发送到 OTLP 后端，检查执行过程，并选择要导出的内容。
---

用追踪查看 Run 的耗时、使用的 Model 和工具，以及续接状态是否保存成功。追踪默认关闭。开始前，先完成一次可用的[首次对话](setup.md)，并准备 OTLP/HTTP collector；也可以使用下方的仓库本地开发流程。

## 开启自动 OTLP 导出

1. 打开兼容 Bash 的终端，设置追踪策略、collector 基础 URL 和凭据：

   ```bash
   export A13N_HARNESS_TRACE_LEVEL=verbose
   export A13N_HARNESS_TRACE_CONTENT=none
   export A13N_HARNESS_METRICS=off
   export OTEL_SERVICE_NAME=a13n-harness-ui
   export OTEL_TRACES_EXPORTER=otlp
   export OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
   export OTEL_EXPORTER_OTLP_ENDPOINT=https://your-collector.example
   export OTEL_EXPORTER_OTLP_HEADERS='Authorization=YOUR_COLLECTOR_CREDENTIAL'
   ```

2. 替换端点和请求头占位符。使用 Langfuse 或 Logfire 时，采用下方对应配置。首次检查先保留 `trace_content=none`。

3. 从该终端启动 Harness UI：

   ```console
   a13n-harness-ui
   ```

4. 发送一条简短测试提示。回答完成后，在 collector 中找到 `harness_ui.root`，展开其中的 `harness.run` 和模型 span。使用 WebUI 时，改为从相同环境启动 `a13n-harness-ui webui`。

环境变量必须在 CLI、WebUI 或嵌入式 App 启动**之前**设置。`OTEL_EXPORTER_OTLP_ENDPOINT` 是基础 URL；追踪专用的 `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` 必须包含 `/v1/traces`。切换后端时，移除冲突的 `OTEL_EXPORTER_OTLP_TRACES_*` 覆盖。

没有现成 Host provider 时，App 会创建私有的 OTLP/HTTP tracer provider。自动配置支持 `OTEL_TRACES_EXPORTER=otlp` 或 `none`，协议为 `http/protobuf`。标准 OTel 资源、采样、批处理、TLS 和超时设置均适用。`OTEL_SDK_DISABLED=true` 会阻止自动 SDK 设置。指标需要单独配置 meter provider。

如果没有收到追踪，检查端点、凭据、追踪专用覆盖、采样设置和[本地诊断](automation-and-troubleshooting.md#logs-updates-and-exit)。成功回答说明模型执行正常；看到追踪才说明导出成功。已保存的对话独立于追踪和后端保留策略。

## Langfuse

保留上述追踪策略，替换端点和请求头：

```bash
export OTEL_EXPORTER_OTLP_ENDPOINT=https://cloud.langfuse.com/api/public/otel
export OTEL_EXPORTER_OTLP_HEADERS='Authorization=Basic%20BASE64_PUBLIC_KEY_COLON_SECRET_KEY,x-langfuse-ingestion-version=4'
```

将项目的 `public-key:secret-key` 编码为单行 base64，并替换占位符。选择项目所在区域或自托管服务器的端点。`x-langfuse-ingestion-version=4` 请求头选择实时 v4 接收。此集成直接使用 OTLP，不需要 Langfuse SDK。

测试 Run 完成后，用其 Thread ID 查找 Langfuse session。每一轮都有独立追踪。异步子级使用自己的 Thread/session ID；按元数据 `root_thread_id` 筛选即可找到整个根 Thread 家族。Harness 会为选中的后代 span 添加名称、标签和关联元数据，同时保留原生模型、token 和工具观测。

## Logfire

保留相同追踪策略，同时替换端点和请求头：

```bash
export OTEL_EXPORTER_OTLP_ENDPOINT=https://logfire-us.pydantic.dev
export OTEL_EXPORTER_OTLP_HEADERS='Authorization=YOUR_LOGFIRE_WRITE_TOKEN'
```

使用项目的 write token 和所属区域；EU 项目使用 `https://logfire-eu.pydantic.dev`。Harness 已负责 Agent 观测，不要再调用 `logfire.instrument_pydantic_ai()`。

如果嵌入 Host 已经持有 Logfire 或 OTel provider，就将它传给 App。在[嵌入](embedding.md)的异步入口中加载 `settings` 和 `configuration_path` 后，使用以下集成片段：

```python
from a13n_harness import HarnessInstrumentation, HarnessTraceContent
from a13n_harness_ui.app import open_harness_ui_app

instrumentation = HarnessInstrumentation(
    tracer_provider=provider,
    trace_content=HarnessTraceContent.NONE,
)
async with open_harness_ui_app(
    settings,
    configuration_path=configuration_path,
    instrumentation=instrumentation,
) as app:
    thread = await app.create_thread()
    receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="Say hello.")
    operation = await app.wait_root_operation(receipt.receipt_id)
    print(operation.status)
```

这里的 `provider` 是 Host 已配置好的 tracer provider。显式观测配置优先于环境选择；显式 `None` 关闭 UI/Harness 观测。默认选择下，`A13N_HARNESS_*` 策略可以使用已有的全局 OTel provider。两种外部 provider 都由 Host 负责导出和关闭，App 不会替换它，也不会向其添加 exporter。

两种 provider 路径都支持 UI/Harness 元数据增强。独立观测的 SDK 仍由嵌入 Host 负责。之前的 `HarnessUiSpanProcessor` 已不再提供。

## 仓库开发

在准备好的[源码检出](installation.md#run-from-source)中，启动本地 Langfuse，再运行使用脚本模型的 App 冒烟测试：

```bash
make langfuse-up
make harness-ui-smoke
```

冒烟测试使用本地脚本模型，并打印 Thread/Run ID。在 Langfuse 中查找两条根追踪和一条关联的子级追踪。测试实例保留在 `var/harness-ui-smoke/<id>/`，不需要真实模型凭据。

要追踪自己的交互任务，运行 `make cli`。它使用检出目录内的配置和数据，并显式加载被 Git 忽略的 `dev/harness-ui/.env`。私有文件不存在时，启动器从示例创建；已存在时保留原文件。使用真实对话前，先检查内容捕获策略。`CLI_ARGS` 转发普通 CLI 选项；`HARNESS_UI_ENV` 选择另一份私有环境文件。

独立的 Harness 开发流程使用 `dev/harness/.env` 和 `make harness-dev HARNESS_ARGS=summary`。该启动器通过 `opentelemetry-instrument` 初始化 provider；Harness UI 启动器使用 App 的自动设置。

## 部署环境

设置 `OTEL_RESOURCE_ATTRIBUTES=deployment.environment.name=local`，将新观测标记为 `local`。开发模板已包含此值。已有私有环境文件时，将它合并到其他资源属性中，然后重启 App。已导出的 shell 值优先于文件中的值。

此标签描述部署环境，与执行 Environment 配置和挂载名称无关。已有追踪保留原标签。外部 provider 的部署资源必须由其 Host 设置。

## 从根级向下阅读执行

1. 打开 `harness_ui.root`；子级分段则打开 `harness_ui.subagent`。检查操作状态和 Thread/Run ID。应用 span 除执行外，还包含准备和续接保存。
2. 展开 `configuration`，检查捕获的 Agent、Model、Capability、集成和 Environment。这份有大小上限的摘要省略提示、凭据、端点和文件系统根目录。
3. 沿 `harness.run` 及其模型／工具 span 检查耗时、恢复、用量和成本。请求级模型字段显示 Model resolver 作出的变更。
4. 查看委派工作时，按 `root_thread_id` 筛选。用 `parent_thread_id`、`subagent_role`、`execution_id` 和 `segment_index` 识别子级分段。恢复子级会保留 Thread/session ID，并添加 `resumed_from_execution_id`，但会开始另一条追踪。

子级的派发 Link 连接有效的实时追踪上下文。持久化的执行关系可以比该上下文保留更久，因此恢复后的子级不一定能链接到之前进程的追踪。正常取消保持为取消，不会转成错误。

### Host 阶段与 Skill 检查

| Span 或字段                            | 用于回答                                    |
| -------------------------------------- | ------------------------------------------- |
| `harness_ui.prepare`                   | 配置、状态加载和 Environment 准备是否成功？ |
| `harness_ui.finalize`                  | Environment 收尾和续接保存是否成功？        |
| `a13n.phase.step`                      | 哪个准备或收尾步骤失败？                    |
| `a13n.ui.continuation.status`          | 是否已为下一次 Run 选定续接状态？           |
| 操作根和 `harness.run` 中的 Skill 摘要 | 此 Thread 中哪些 Skill 可用、哪些已读取？   |

如果回答可见但应用操作失败，重试任务前先检查收尾阶段。Harness Run 可能成功，而保存失败。Skill 读取记录的是访问行为，不能据此认定 Agent 遵循了指令。

## 内容与限制

导出真实对话前，先选择内容策略：

| `A13N_HARNESS_TRACE_CONTENT` | 捕获内容                                           |
| ---------------------------- | -------------------------------------------------- |
| `none`                       | 结构化诊断；省略常规执行正文                       |
| `standard`                   | 操作根的当前提交和最终输出，以及原生模型／工具内容 |
| `full`                       | Standard 内容，加上上游二进制和请求参数捕获        |

操作根的输入和输出各自最多 8 KiB。用 `a13n.input.capture`、`a13n.output.capture` 和截断标记检查缺失或不完整的值。根中只描述原生媒体，不复制二进制、完整历史、凭据、原始异常或检查点正文。上游模型／工具 span 有自己的信息边界：`none` 不能完整清除异常、Agent 描述、元数据或工具 schema。选择导出策略时，请阅读 [Harness 追踪](../a13n-harness/observation.md)。

关闭时，App 在活跃工作及协作组件结束后，于事件循环之外、等待预算之内排空自己的 exporter。导出采用尽力交付。恢复工作应使用已保存的 Thread 状态，而不是追踪或后端是否收到了追踪。

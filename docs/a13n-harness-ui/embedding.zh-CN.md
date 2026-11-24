---
title: 嵌入 Harness UI Python 应用
sidebarTitle: 嵌入 Python 应用
description: 以编程方式导入并驱动 Harness UI Python App，在共享 YAML 配置与 SQLite 会话存储之上构建自定义前端或自动化脚本。
---

当你的 Python 应用需要直接复用 Harness UI 的 YAML 配置文件、SQLite 会话历史、暂存附件与运行控制接口时，请使用 `HarnessUiApp`。若由应用自行管理配置与持久化，请直接嵌入 [Harness](../a13n-harness/hosting.md)；若需要跨工作进程故障恢复的托管执行，请选用 [Service](../a13n-service/index.md)。

## 打开应用、提交输入并等待结果

在应用的 Python 环境中安装 `a13n-harness-ui`，完成[初始化设置](setup.md)，运行 `a13n-harness-ui config path` 获取根 `a13n-harness-ui.yaml` 路径，并保存 `embedded_chat.py`：

```python title="embedded_chat.py"
import argparse
import asyncio
from pathlib import Path

from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.settings_loader import load_harness_ui_settings


async def run_once(configuration_file: Path, prompt: str, thread_id: str | None):
    source = await load_harness_ui_settings(configuration_file)
    if source.candidate_error is not None:
        raise source.candidate_error

    async with open_harness_ui_app(
        source.settings,
        configuration_path=source.path,
    ) as app:
        if thread_id is None:
            thread = await app.create_thread(title="Embedded conversation")
            thread_id = thread.thread_id
        else:
            await app.get_thread(thread_id)

        print(f"Thread: {thread_id}")
        receipt = await app.submit_thread(thread_id=thread_id, prompt=prompt)
        operation = await app.wait_root_operation(receipt.receipt_id)
        print(operation.model_dump_json(indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--thread")
    parser.add_argument("prompt")
    args = parser.parse_args()
    asyncio.run(run_once(args.config, args.prompt, args.thread))
```

运行首轮对话并基于打印出的会话 ID 续接同一会话：

```bash
python embedded_chat.py --config /path/to/a13n-harness-ui.yaml "Reply with one short sentence."
python embedded_chat.py --config /path/to/a13n-harness-ui.yaml --thread THREAD_ID "Explain your previous answer."
```

需要隔离数据目录时向 `load_harness_ui_settings(...)` 传入 `data_root=Path(...)`；需要启用浏览器协作特性但不启动 [HTTP 监听器](http-api.md)时向 `open_harness_ui_app(...)` 传入 `host_mode="webui"`。

## 跟踪运行回执、在线引导与驻留工具

`submit_thread()` 返回进程内回执 `RootRunReceipt(receipt_id, thread_id, submitted_at)`。请分别检查操作结果中的 `execution`、`continuation` 与 `environment` 三个字段：

| 任务                           | `HarnessUiApp` 方法                                                                                                   |
| ------------------------------ | --------------------------------------------------------------------------------------------------------------------- |
| 查询、等待、引导或取消根操作   | `get_root_operation`、`wait_root_operation`、`active_root_operation`、`steer_root_operation`、`cancel_root_operation` |
| 查询或取消后台驻留工具         | `tool_executions(thread_id, offset=0, limit=20)`、`cancel_tool_execution(thread_id, execution_id)`                    |
| 创建、列出或读取会话与聊天记录 | `create_thread`（`NewThreadDefaults`）、`get_thread`、`list_threads`、`get_thread_transcript`                         |
| 修改会话元数据或默认配置选择   | `update_thread_metadata`、`update_thread_configuration`、`patch_thread_configuration`                                 |
| 查询与控制委派子智能体         | `query_child_executions`、`wait_child_executions`、`steer_child_execution`、`cancel_child_execution`                  |

受信任宿主还可独立于对话历史持久化会话级业务元数据与请求头，供后续运行跨重启读取：

```python
from a13n_harness_ui.external_context import ExternalContext

await app.set_thread_external_context(
    thread_id=thread_id,
    context=ExternalContext(metadata={"tenant": "acme"}, headers={"x-trace-id": "trace-123"}),
)
```

## 完整响应待处理决策集

当操作挂起等待决策（`status == "suspended"`）时，请一次性提交全部待处理提问、审批与外部工具结果：

1. 调用 `await app.thread_decisions(thread_id=...)` 获取待处理列表及其 `expected_continuation_id`。
2. 构造 `DecisionResponseBatch`（`a13n_harness_ui.surfaces`），为每一项填入 `QuestionResponse`、`ApprovalDecision` 或 `ExternalToolResult`（遗漏的审批会被拒绝，遗漏的外部调用与提问会按失败处理）。
3. 通过 `await app.respond_decisions(thread_id=..., response=...)`（或底层 `respond_thread()`）提交并等待返回的新回执。

若要在运行期间处理内联 MCP 表单与 URL 确认，请以 `mcp_input_enabled=True` 打开应用，并发读取 `mcp_input_requests(thread_id)`（或监听 `ThreadWatch.snapshot.mcp_inputs`），并通过 `respond_mcp_input(thread_id, request_id, response)` 提交 `McpInputResponse`（`accept`、`decline` 或 `cancel`）。驻留 MCP 连接可通过 `mcp_status(thread_id)` 与 `close_mcp_integration(thread_id, server_id)` 管理（见 [MCP](mcp.md#connection-lifetime-and-protocol)）。

## 暂存附件、订阅无竞争事件流与注册受信任集成

先在会话上暂存附件（单次提交最多 8 个文件、单文件 10 MiB、合计 20 MiB），再在提交时传入附件 ID：

```python
from a13n_harness_ui.thread_files import AttachmentUpload

attachment = await app.stage_thread_attachment(
    thread_id=thread_id,
    upload=AttachmentUpload(
        name="notes.txt",
        data=b"The release needs a setup guide and a smoke test.",
        media_type="text/plain",
    ),
)
receipt = await app.submit_thread(
    thread_id=thread_id,
    prompt="Summarize the attached notes.",
    attachment_ids=(attachment.attachment_id,),
)
```

构建实时界面时，进入 `async with app.watch_thread(root_thread_id=...) as watch:`——它会在抓取 `watch.snapshot` 前先建立订阅，确保消费 `watch.events` 时不漏掉中间状态切换（断线重连可使用带游标的 `live_events` 与 `summary_events`）。

通过 `open_harness_ui_app(..., integrations=HarnessUiIntegrations(...))` 可注册受信任[连接器](connectors.md#develop-a-trusted-connector)、宿主能力、环境提供方与[插件](extensions-and-mcp.md)；OpenTelemetry 追踪接入见[可观测性](observation.md)。编写隔离测试时，请指定临时 `data_root`、关闭 `pricing_auto_update` 并设置 `A13N_OFFICIAL_MODELS_AUTO_UPDATE=0`。

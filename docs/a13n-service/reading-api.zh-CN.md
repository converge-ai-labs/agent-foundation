---
title: 读取大型 API 结果
description: 读取紧凑摘要、指定章节和带版本片段，并保留后续读取位置。
---

身份验证参阅 [HTTP 约定](http.md)，首次集成按[对话教程](connect-application.md)操作。

## 读取摘要和指定详情

读取较长历史时，先定位记录，再获取需要的字段。例如：

```http
GET /api/v1/runs?status=failed&limit=10
GET /api/v1/runs/{run_id}?section=failure
GET /api/v1/runs/{run_id}/items?view=compact&kind=tool_call&state=failed
GET /api/v1/runs/{run_id}/items/{item_id}/content?ordinal={ordinal}&field=result&offset=0
```

替换为返回的运行 ID、条目 ID 和该条目的实际 `ordinal`。精简条目读取默认从最早的 ordinal 开始，也可提供 `before` 或 `after`；按 `next_before` 或 `next_after` 继续，包括筛选后当前页为空的情况。未加筛选的详细读取会选择最新条目和完整尾部。

Run 列表和祖先链返回元数据及 `input_preview: {text, truncated}`，预览最多包含 200 个 Unicode 码点，且不接受 `view`。完整详情通过 `/runs/{run_id}` 读取。线程内运行分页还支持用 `anchor=<run_id>` 代替 `cursor`，返回 `total`、每条记录的时间顺序编号 `number`（从 1 开始），以及指向较早运行的 `next_cursor` 和较新运行的 `previous_cursor`。Usage 汇总已经包含各运行的 `run_number`。

修订版本、模型、收件箱记录、显示条目、span 和单个运行详情支持 `view=compact`；省略时保留 `view=detailed`。精简列表默认十条记录。精简列表和运行发现分页都可能因共享的 10,000 字符响应预算提前结束。应跟随返回的继续读取值，而不是假设每页必有 `limit` 条记录。

将预览当作完整内容前，先检查响应元数据：

- `truncated` 表示预览被截短；`omitted_sections` 指出可以单独读取的字段。
- `source_truncated` 表示内容保存前已被截短；继续读取无法恢复丢弃的部分。
- `representation: fragment` 提供 `text`、内容 `version`、`next_offset` 和 `has_more`。继续时同时传入返回的 offset 和 version；offset 按 Unicode 码点计数。遇到原因是 `content_changed` 的 `409 conflict` 时，重新开始读取。先拼接 JSON 片段，再解析完整值。
- 字面 `query` 搜索返回匹配项和上下文；其 next offset 用于继续搜索，而非提取全部内容。

根据任务选择详情路由：

| 任务            | 读取路径                                                                                                                                                                                                                                                                        |
| --------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 编辑配置        | 读取修订版本的 `section=all` 或 `instructions` 等字段。运行的 `/agent` 接受 section `original`、`effective` 或 `overrides`；original/effective 可再选 `part`。修订版本的 `/compare` 接受 `base_revision_id` 和可选 section。编辑前取得完整原配置和主记录 ETag，并保留无关字段。 |
| 检查工具定义    | 搜索 `/toolsets/index`、`/connections/{connection_id}/tools/index` 或 `/connector-providers/{provider_id}/apps/{app}/actions/index`。调用对应 `/definitions`，最多重复传入五个准确 `name`。检查 `missing` 和 `oversized`；超大定义通过单个名称和带版本片段读取。                |
| 检查模型        | 使用模型目录的精简搜索、能力筛选和分页，再准确选择 provider/model 读取能力或价格。                                                                                                                                                                                              |
| 检查 trace span | 按 `status`、`kind` 或 `tool_name` 筛选 trace 列表。读取 `/traces/{trace_id}/spans/{span_id}/content?section=input` 或 `section=output`，以 version 和 offset 继续读取片段。                                                                                                    |

Trace span 内容查询只获取该 span；`items` 为空表示它不在该 trace 中。单个 span 超过后端 8 MiB 响应上限时返回 `413 payload_too_large`。可读取的 trace 数据取决于后端可用性和保存期限。写入时使用完整配置和定义，不要将摘要、片段或差异当作完整值提交。

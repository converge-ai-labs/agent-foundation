---
title: 检查运行并改进 agent
description: 检查用量和 trace 证据，审核发现项后再修改配置。
---

在 Console 中打开对话，切换到 **Debug**，检查运行状态、保存的配置、执行尝试和用量。这些记录不需要 trace 后端。查看模型和工具 span 或使用 Finding Agent 分析前，运维人员须另行配置 [trace 后端](configuration.md#logs-metrics-and-traces)。先核对证据，再提出改动。

## 用量

用 `GET /usage?session_id=...` 获取完整模型总量，用 `GET /usage/runs?session_id=...&limit=50` 获取运行分页明细。总量覆盖全部匹配记录，不受 UI 已加载运行数量影响。两个端点也支持按运行、线程和入库时间筛选。

`/usage` 返回 `{models:[...]}`。`/usage/runs` 返回 `{items:[{run_id,thread_id,run_number,models}],next_cursor}`。保持筛选条件不变，读取游标直到 null。运行按从早到晚排列，同一运行的模型组放在一起。`run_number` 计入该线程全部运行，包括没有用量的运行。

每个模型总量按保存价格记录请求数、token 和费用。缓存输入计入输入 token。无法归属的模型 key，以及完全没有计价记录的费用，为 null。`average_time_to_first_chunk_seconds` 对符合条件的已完成流式请求求平均。`output_tokens_per_second` 将完整输出 token 除以已完成且时长为正的请求总时长，包含非流式请求和首个分片之前的等待。缺少相应观测时，各指标为 null。

运行的 `usage_at_seal` 是结束时的用量。延迟报告仍进入 `/usage`，时间筛选按它们的入库时间选择。

## Trace

配置 [trace 后端](configuration.md#logs-metrics-and-traces)，再在 Console 打开 **Traces**，调查缓慢、昂贵或失败的执行。每次尝试导出 Harness span，包括内联子 agent。没有后端时，查询返回 `503 unavailable`。

1. 选择时间范围。
2. 按运行/线程/会话或根属性筛选。
3. 设置以秒为单位的耗时限制或以 USD 为单位的费用限制，再按**耗时**、**费用**或**开始时间**排序。
4. 打开 trace，检查相关 span 和证据。

例如，耗时最小值 `60`、费用最小值 `0.01`，按费用降序排列，可找到上报耗时至少一分钟、费用至少 $0.01 的 trace。范围包含边界。Trace 费用累加上报的 span 费用；缺失费用表示未知，trace 总量与 Service 运行账目独立。

调用 `/traces/search` 时，提供明确的 `started_after`、`started_before`、`order_by` 和 `direction`。使用 keyset 游标时保持筛选条件不变。查询有收集上限和 30 秒截止时间，超限时缩小范围或增加 ID 条件。结果受后端采样、入库和保留期影响，分页不会冻结变化的数据。Trace、span 和尝试端点见 [API 参考](api-reference/index.md)。

## 发现并改进执行问题

通过**洞察 → 发现**，把 trace 证据转化为经过审阅的改进：

1. 选择**分析 traces**，设置规则及时间范围/数量，可补充**本次检查要求**。针对一条已结束 trace，使用**分析此 trace**。
2. 从**分析历史**打开分析运行，阅读摘要、证据及选择上限。
3. 打开发现项，选择**编辑审阅**，指定已确认、预期行为、证据不足或误判，填写**审阅理由**并**保存审阅**。
4. 对值得修改的发现项，选择**交给 Composer 审阅**，发送预填消息，检查建议，再按需要批准配置写入。

分析需要 trace 查询后端，使用内置 Finding Agent 当前选定的默认版本和模型。选择过程最多扫描 100 个根节点，选择最多 20 条 trace。执行/恢复规则在扫描内优先选择失败运行及 error/fatal 根节点。数量说明选择与引用范围，并非每条所选 trace 都已完整分析的证明。即使是严重发现，也要对照证据审阅。

类别区分请求不清、指令问题、工具设计、工具使用、工具执行、回答质量、上下文缺失、流程问题和边界违规。类别描述问题，不证明原因。正常权限拒绝不是边界违规；无依据的成功声明属于回答质量问题。

保存的结论和理由会提供给同一 agent 修订版本的后续分析。修改结论会清空旧理由；关闭或重开发现项保留已保存反馈。每次接受的分析保留原始输入，因此编辑只影响新分析。反馈有范围上限，用于比较诊断，不会合并已保存发现项。

Finding Agent 只读取选定 trace 证据并提交发现项，不修复或发布配置。外部提交方可调用 `POST /findings`，使用在工作空间/主体范围内唯一且稳定的 `source_key`。提交方负责 trace/span 引用；Service 校验所引运行和 agent 修订版本。

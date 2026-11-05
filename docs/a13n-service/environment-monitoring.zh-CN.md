---
title: 监控环境
description: 查看第一张资源图表、接入历史查询后端，并运维环境采集器。
---

环境监控独立于 agent 运行，展示 Envd 目标的 CPU、内存、网络和磁盘活动。先用 **Local Cache** 查看图表，无需指标数据库。需要持久历史时，再接入外部后端。Service 自身的健康状态和运行用量见 [Service 监控](monitoring.md)。

## 绑定并验证环境

你需要一个有权管理的环境、受支持的 Envd 目标，以及启用了监控的 Service 部署。Local Cache 支持外部 HTTP 目标和托管 Docker/E2B `envd_http` 目标。目标必须正在运行，并启用 Envd 采样器。

1. 在 Console 中打开环境，在 **监控** 下选择 **配置**。
2. 将 **指标数据源** 设为 **Local Cache**，再选择 **保存选择**。新建托管 Docker/E2B `envd_http` 环境时，在创建前选择数据源，让 Service 启用 10 秒采样器。
3. 已有目标未启用采样时，请运维启用 [Envd 采样器](../a13n-envd/configuration.md#system-information-and-resource-monitoring)。选择数据源或启动已有环境不会重新配置其守护进程。
4. 等待目标产生样本，再选择 **打开仪表盘**。查看最近的时间范围，并在 **查询详情** 中检查新鲜度和实际覆盖范围。执行正常工作负载，观察资源用量变化。

Local Cache 将样本保存在负责采集的 Service 进程内存中。Redis 协调所有权和跨副本查询。进程重启或所有权变化可能丢失历史；需要跨这些事件保留数据时，应使用外部后端。

也可以通过 HTTP 查询最近 15 分钟。将 `A13N_URL` 设为 Service origin，`A13N_API_KEY` 设为具有读取权限的工作空间密钥，`ENVIRONMENT_ID` 设为已选择的环境 ID。私有环境还要求所有者或管理员权限。以下 shell 示例使用 `curl` 和 `date` 输出的 Unix 秒级时间戳：

```sh
END=$(date +%s)
START=$((END - 900))
curl --fail-with-body --silent --show-error \
  -H "Authorization: Bearer $A13N_API_KEY" \
  -H 'Content-Type: application/json' \
  --data "{\"metric\":\"system_memory_used_bytes\",\"history\":\"temporary\",\"start\":$START,\"end\":$END,\"step\":60}" \
  "${A13N_URL%/}/api/v1/environments/$ENVIRONMENT_ID/metrics/query"
```

使用 Local Cache 时，检查 `observation.status` 和 `history.series`。新鲜样本和数据点表明采集已生效；保存绑定本身不能证明这一点。图表持续为空时，先检查选中的数据源、目标就绪状态和采样器。使用 pull 数据源时，还要检查 `GET /api/v1/environments/{environment_id}/metrics/collector` 和采集器的目标状态。

API 客户端可在创建时设置 `metrics_data_source_id`，或调用 `PUT /api/v1/environments/{environment_id}/metrics-binding`，将环境 ETag 放入 `If-Match`。将数据源 ID 设为 `null` 可解除绑定。为外部目标选择自定义数据源，以及设置自定义身份标签，需要管理员批准。

托管创建的 `metrics_application` 记录启动时使用的数据源版本和采集方式。`applied_at` 表示已按这些输入完成创建；是否摄取成功要在接收端确认。后续 proxy/pull 选择可以改变已启用采样目标的实时采集。原生导出器变更需要重新配置或替换目标。解除绑定或禁用数据源后，已注入的凭据仍留在目标中，直到你停用或重新配置导出器。

## 在 Console 中使用仪表盘

打开 **环境 → 指标**，选择环境或监控 Device。在目标选择器中按名称、ID 或 `region=west` 等标签搜索，也可结合类型、状态和数据源筛选。

通过时间范围按钮选择最近时间、绝对日期或自动刷新。拖动图表选区进行缩放，选择 **返回实时** 恢复实时刷新。悬停可比较同步数值，点击图例可隐藏序列；超过四个序列的面板还提供序列搜索。

调整内置 **Environment overview** 或自定义仪表盘：

1. 选择 **编辑仪表盘**，添加或编辑面板。
2. 选择预定义的 Environment / Device 指标。管理员也可选择数据源并输入 PromQL；Local Cache 只支持预定义指标。
3. 选择 **运行查询**，检查结果，再选择 **应用面板**。设置单位、时间序列或统计值视图，以及面板大小。
4. 拖动面板调整位置，选择 **保存仪表盘** 共享布局。**放弃** 会丢弃草稿；**恢复默认** 将内置布局放入草稿，供你保存。

编辑仪表盘需要工作空间写入权限。应用内导航会保留各仪表盘的草稿；刷新页面或退出登录前请先保存。如果其他人抢先保存，你的草稿仍会保留，直到你明确重新加载。

图表请求约 600 个求值时间点，每个面板最多显示 32 个序列。通过 **查询详情** 查看截断、保留时长和覆盖范围，再缩小较大的查询。刷新失败时，可保留带标记的上次结果，同时显示错误。缺失观测显示为空缺，而非零值。仪表盘布局持久保存，Local Cache 样本为临时数据。

## 选择数据源

<span id="confirm-a-deployment-preset"></span> <span id="bootstrap-an-optional-collector"></span> <span id="vmagent"></span> <span id="grafana-alloy"></span> <span id="reconciliation-and-retirement"></span> <span id="connect-a-reverse-device"></span> <span id="query-and-size-temporary-history"></span>

- [运维环境采集器](environment-collectors.md)

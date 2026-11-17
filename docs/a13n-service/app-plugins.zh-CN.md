---
title: 使用 App 插件扩展 Service
description: 安装可信应用包，按组织启用，并保持已接受 run 的行为稳定。
---

App 插件为 Service 添加业务 API、组织范围后台工作、Harness factory 和打包的 Console 资源。运维人员在所有 API 和 worker 进程中安装可信 Python 包。组织管理员逐个启用 App。新安装的 App 默认禁用。

App 插件在 Service 进程内运行，拥有进程权限。安装前应审查软件包。[MCP Apps](mcp-apps.md) 是用于交互式工具结果的另一套协议。

## 注册软件包

在 wheel 中声明一个无参数的定义函数：

```toml
[project.entry-points."a13n_service.app_plugins"]
"acme.reports" = "acme_reports.app:definition"
```

使用宿主路由器，让策略检查发生在插件依赖和 handler 执行之前：

```python
from a13n_service.app_definitions import AppDefinition
from a13n_service.tenancy.app_plugins import app_router
from a13n_service.tenancy.requests import Actor, WorkspaceId

router = app_router("acme.reports")


@router.get("/summary", name="get_acme_report_summary", openapi_extra={"x-a13n-mcp": True})
async def summary(actor: Actor, workspace_id: WorkspaceId) -> dict[str, str]:
    # Read admission is complete. Check additional business permissions here when needed.
    return {"workspace_id": workspace_id, "requested_by": actor.id}


def definition() -> AppDefinition:
    return AppDefinition(app_id="acme.reports", name="Reports", routers=(router,))
```

Service 在应用组装时发现一次定义。重复的 App ID、factory 归属、设置或 HTTP 操作会使启动失败。安装或升级 wheel 后重启部署。control 与 worker 必须保持包版本一致。

`AppDefinition.factory_keys` 声明所属 Harness 入口点 key。agent 仍通过普通配置选择具名实例。Service 在启动时加载所属 factory，并根据已接受 run 的快照决定是否构造实例。独立 factory 继续使用 `plugins.keys`。

`OrganizationSweep` 回调接收已有 Runtime 和一个已启用的组织 ID。Service 选择组织，并在回调前关闭 SQL session。回调负责有界业务工作以及多副本安全的认领。可选的 `startup(runtime, stack)` 打开进程资源，并在已有退出栈上注册清理。App 定义不提供表或迁移；业务数据库应通过应用自身的运维流程准备。

## 启用组织

读取安装目录及组织 ETag：

```http
GET /api/v1/organizations/org_example/app-plugins
```

使用该 ETag 启用一个已安装 App：

```http
PUT /api/v1/organizations/org_example/app-plugins/acme.reports
If-Match: "org_example:1"
Content-Type: application/json

{"enabled": true}
```

组织读者可以查看目录。组织管理员可以修改策略。浏览器修改使用普通 session、CSRF 和 Origin 规则。ETag 使用组织版本，因此其他组织修改也可能使其过期。收到 412 后重新读取。实际策略变化会记录审计；重复设置当前值不会改变版本或审计历史。

业务端点位于 `/api/v1/apps/acme.reports/`。每次请求使用已有 workspace 选择与授权。禁用的 App 在执行请求依赖之前返回 `disabled`。此前已获准的工作可以完成并清理。

## 保持 run 行为稳定

每个已接受 run 冻结组织策略、包版本和 factory 归属。后续轮次与 worker 恢复使用该快照。inline 子 agent 共享可用性上限，但保留自身实例声明。新的持久化子 run 或 resume 后继 run 捕获当前策略。

如果 worker 上已启用包的版本或归属不同，执行会在构造插件前明确失败。升级时应一致部署软件包，并排空旧工作。如果 resume 后继恢复的未完成工具现已禁用，则在执行工具前以 `deferred_surface_mismatch` 失败。禁用会保留历史结果和插件保存状态。

## 打包浏览器资源

`Frontend(package="acme_reports", directory="static", entry="index.js", styles=("index.css",))` 声明预构建 ESM 资源树以及桥接版本 `1`。将所有相对 chunk、样式和字体包含在 wheel 中。Service 通过同源、带版本的 URL 提供资源，并让解压出的包资源在进程关闭前保持可用。缺失文件返回 404。刷新会读取不缓存的当前目录；同一包版本的资源字节不可变。

打包代码与 Console 资源一样公开。业务 API 授权独立执行。资源字节变化时必须修改包版本。

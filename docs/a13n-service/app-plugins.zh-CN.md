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

入口点名称必须与 `AppDefinition.app_id` 一致。Service 在应用组装时加载所有已安装的定义，包括禁用的 App。重复的 App ID、factory 归属、设置或 HTTP 操作会使启动失败。在所有 control 和 worker 进程中安装 wheel，再重启部署。整个部署使用相同的包版本。

`AppDefinition.factory_keys` 声明所属 Harness factory key。按照[插件和扩展](../a13n-harness/plugins.md#publish-a-plugin-factory)将 factory 注册到 `a13n_harness.plugins`，再将 key 加入定义，例如 `factory_keys=frozenset({"acme.audit"})`。在 agent 配置中照常选择具名插件实例。Service 在启动时加载所属 factory，并根据已接受 run 的快照决定是否构造实例。独立 factory 通过 `plugins.keys` 选择。

`OrganizationSweep` 回调接收已有 Runtime 和一个已启用的组织 ID。Service 选择组织，并在回调前关闭 SQL session。回调负责有界业务工作以及多副本安全的认领。每个组织使用声明的超时时间；某个回调失败后，同批其他组织继续执行，该轮扫描记为失败。可选的 `startup(runtime, stack)` 打开进程资源，并在已有退出栈上注册清理。每个已安装 App 都会运行 startup，与组织启用状态无关。App 定义不提供表或迁移；业务数据库应通过应用自身的运维流程准备。

## 启用组织

读取安装目录及组织 ETag：

```http
GET /api/v1/organizations/org_example/app-plugins
```

将响应头 `ETag` 的值填入 `If-Match`，以启用已安装的 App（以下值仅作示例）：

```http
PUT /api/v1/organizations/org_example/app-plugins/acme.reports
If-Match: "org_example:1"
Content-Type: application/json

{"enabled": true}
```

组织读者可以查看目录。组织管理员可以修改策略。浏览器修改使用普通 session、CSRF 和 Origin 规则。ETag 使用组织版本，因此其他组织修改也可能使其过期。收到 412 后重新读取。实际策略变化会记录审计；重复设置当前值不会改变版本或审计历史。卸载软件包后，管理员可以通过同一个 PUT 和 `{"enabled": false}` 清除保留的策略 ID。

业务端点位于 `/api/v1/apps/acme.reports/`。每次请求使用已有 workspace 选择与授权。禁用的 App 在执行请求依赖之前返回 `disabled`。此前已获准的工作可以完成并清理。

## 保持 run 行为稳定

每个已接受 run 冻结组织策略、包版本和 factory 归属。后续轮次与 worker 恢复使用该快照。inline 子 agent 共享可用性上限，但保留自身实例声明。新的持久化子 run 或 resume 后继 run 捕获当前策略。

如果 worker 上已启用包的版本或归属不同，执行会在构造插件前明确失败。检查覆盖快照中每个已启用 App，包括 run 的 agent 未选择的 App。升级时应一致部署软件包，并排空旧工作。如果 resume 后继恢复的未完成工具现已禁用，则在执行工具前以 `deferred_surface_mismatch` 失败。禁用会保留历史结果和插件保存状态。

## 添加 Console 页面

将 `AppContribution` 作为前端入口的默认导出。Console 加载当前组织已启用的 App，并在已有 provider 内渲染其贡献。以下示例展示上面定义的 `/summary` 端点：

```tsx
import { React, useApp, useQuery } from "@a13n/console";
import type { AppContribution } from "@a13n/console";

type Summary = { workspace_id: string; requested_by: string };

function ReportsPage() {
  const app = useApp();
  const summary = useQuery({
    queryKey: app.queryKey("summary"),
    queryFn: async ({ signal }) => {
      const response = await app.request("/summary", { signal });
      return (await response.json()) as Summary;
    },
  });

  if (summary.isPending) return <p>Loading reports…</p>;
  if (summary.isError) return <p>Could not load reports.</p>;
  return <p>Workspace: {summary.data.workspace_id}</p>;
}

const contribution: AppContribution = {
  bridge: 1,
  pages: [{ id: "reports", label: { en: "Reports", "zh-CN": "报告" }, component: ReportsPage }],
};

export default contribution;
```

`useApp()` 提供 `appId`、`organizationId`、`workspaceId`、带作用域的 `queryKey(...)` 和 `request(path, init)`。请求路径以 `/` 开头，解析到 `/api/v1/apps/{appId}` 下。Host 添加所选 workspace 请求头，并使用已有认证、CSRF 处理和传输层。业务请求需要选择 workspace。

页面位于 `/workspace/{workspaceId}/apps/{appId}/{pageId}/*`。页面 ID 以字母开头，使用小写字母、数字和连字符。可选的 `description` 和 `permission` 字段用于描述页面，并控制其在 Console 中的显示和路由访问。业务端点仍自行执行授权。标签和描述可以是字符串，也可以是包含 `en` 和可选 `zh-CN` 的对象。`settings` 条目使用相同字段，并添加 `scope: "workspace"` 或 `"organization"`；Host 中的设置 key 为 `app.{appId}.{sectionId}`。

App 还可以提供 `workspaceHome: Component` 来替换 workspace 首页。核心 agent 路由仍然可用。如果多个已启用 App 都声明首页，Console 会显示错误和指向 agents 的链接。组织管理员可以在设置中启用或禁用 App；成功修改后，活动注册表随即更新。刷新 Console 会获取当前目录。

### 构建入口

将前端打包为 ESM，并将 `@a13n/console` 排除在 bundle 外。Console 的 import map 提供共享的 React、query、router、翻译、UI 和 Host context 导出。通过 `@a13n/console` 导入这些能力，使用其导出的 `React` 和 classic JSX，并由 Console 挂载组件。

[独立 App 示例](https://github.com/converge-ai-labs/agent-foundation/tree/main/examples/service-app-plugin)包含 Vite 构建、独立的 App 定义和可安装的 wheel。在仓库根目录运行以下命令构建其前端：

```bash
pnpm --dir examples/service-app-plugin/frontend install --frozen-lockfile
pnpm --dir examples/service-app-plugin/frontend build
```

按照示例的构建和安装说明打包生成的资源、启动 Service 并启用 App。示例实现了自己的 `/summary` 响应，使用与本页代码相同的 App ID。

桥接模块还导出 `useAuth`、`useAccess`、`useWorkspace`、`useAppearance`、`useTranslation`、`useMutation`、`useQueryClient` 等 hook，以及 `Page`、`Empty`、`Loading`、`ErrorPage` 等选定 UI 组件。Console 负责 React 根节点和 provider。

## 打包浏览器资源

软件包包含预构建 ESM 资源树时，在定义中添加 `Frontend`：

```python
from a13n_service.app_definitions import AppDefinition
from a13n_service.app_plugins import Frontend


def definition() -> AppDefinition:
    return AppDefinition(
        app_id="acme.reports",
        name="Reports",
        routers=(router,),  # router from the registration example above
        frontend=Frontend(
            package="acme_reports", directory="static",
            entry="index.js", styles=("index.css",),
        ),
    )
```

将入口、样式、相对 chunk 和字体放在 wheel 的 `acme_reports/static/` 下。如果生成目录被 Git 忽略，应配置构建后端将其明确包含在 wheel 和源代码分发包中，示例使用 Hatch 的 `force-include` 实现。Service 在启动时检查入口和声明的样式；其他缺失的引用资源返回 404。Service 通过同源、带版本的 URL 提供资源，并让解压出的包资源在进程关闭前保持可用。声明默认使用桥接版本 `1`。刷新会读取不缓存的当前目录；同一包版本的资源字节不可变。

打包代码与 Console 资源一样公开。业务 API 授权独立执行。资源字节变化时必须修改包版本。

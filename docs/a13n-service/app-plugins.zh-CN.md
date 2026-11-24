---
title: 使用 App 插件扩展 Service
description: 打包并安装可信 Python App 插件，为 Service 扩展自定义 API、Console 页面、数据库表、Harness 插件与业务指标。
---

App 插件在复用 Service 认证、工作区与智能体运行时的基础上，为平台扩展领域 API（`/api/v1/apps/<app_id>/...`）、组织级后台巡检、Harness 插件工厂、PostgreSQL 业务表以及 Console 界面页面。

> [!IMPORTANT]
> App 插件在 Service 进程内以相同权限运行。请在所有 `control` 与 `worker` 进程（或 `all`）中安装完全一致的包版本，并按组织启用。

## 本地脚手架与开发

从 [App 插件模板](https://github.com/converge-ai-labs/agent-foundation/tree/template/app-plugin) 生成独立项目并启动本地开发栈：

```bash
uvx cookiecutter gh:converge-ai-labs/agent-foundation --checkout template/app-plugin
cd my-service-app
make dev
```

`make dev` 会启动本地 PostgreSQL、Redis 与 S3，执行数据库迁移并以热重载模式运行 Service。登录 Console 后，在 **Settings → Organization → App plugins** 中启用你的 App。完整参考实现见 [`examples/service-app-plugin`](https://github.com/converge-ai-labs/agent-foundation/tree/main/examples/service-app-plugin)。

## 注册 App 定义

在 `pyproject.toml` 的 `a13n_service.app_plugins` 入口组中注册零参工厂函数，键名须与 `AppDefinition.app_id` 一致：

```toml title="pyproject.toml"
[project.entry-points."a13n_service.app_plugins"]
"acme.reports" = "acme_reports.app:definition"
```

```python title="acme_reports/app.py"
from a13n_service.app_definitions import AppDefinition
from a13n_service.tenancy.app_plugins import app_router
from a13n_service.tenancy.requests import Actor, WorkspaceId

router = app_router("acme.reports")


@router.get("/summary", name="get_acme_report_summary", openapi_extra={"x-a13n-mcp": True})
async def summary(actor: Actor, workspace_id: WorkspaceId) -> dict[str, str]:
    return {"workspace_id": workspace_id, "requested_by": actor.id}


def definition() -> AppDefinition:
    return AppDefinition(app_id="acme.reports", name="Reports", routers=(router,))
```

- **`factory_keys`：** 声明归属的 Harness 插件工厂键（注册在 `a13n_harness.plugins` 下，见[插件与扩展](../a13n-harness/plugins.md#publish-a-plugin-factory)），如 `factory_keys=frozenset({"acme.audit"})`。
- **`OrganizationSweep` 与 `startup`：** `startup(runtime, stack)` 用于初始化进程级资源；周期性组织级后台任务请放入多副本安全的 `OrganizationSweep`。

## 使用进程内 App SDK 与运行封存事件

通过 `a13n_service.app_sdk` 在同一数据库事务内原子操作原生 Thread、Schedule 与 App 自有出站凭据：

- **线程与消息提交：** 在锁定 App 业务行之前先调用 `threads.prepare_admission(session, runtime, workspace_id, principal_id, app_id=...)`，再以具备 `run` 权限的工作区服务账号调用 `threads.create_thread_in_transaction` 或 `threads.submit_message_in_transaction`。使用 `threads.update_thread_resources` 同步更新根线程及其共享环境的子线程。
- **App 自有出站凭据：** 使用 `egress.create_app_credential`、`rotate_app_credential`、`revoke_app_credential`（或对应的 `*_in_transaction` 变体与业务状态原子提交，事务提交后自动通知活跃 Session）。
- **线程出站密钥绑定：** 调用 `threads.update_app_egress_secrets`（传入 `set_secrets` 的 `egress.SecretReference` 或 `remove_envs`）更新空闲共享线程子树的主绑定，同时保留原有目标策略与其他所有者的引用。若绑定尚未配置出站模式或策略，首次添加引用时须传入 `initial_destinations`（如 `a13n_envd_client.eip.v1` 中的 `AllowlistDestinations(mode="allowlist", hosts=("api.example.com",))`）。
- **运行封存订阅：** 在 `AppDefinition(run_seals=(...,))` 中声明 `RunSealSubscription(callback, timeout=5)` 监听终止或 `waiting` 状态。将其视为尽力投递唤醒信号，并配合 `OrganizationSweep` 对账持久化状态。

## 持久化业务数据

使用 `AppDatabase` 在宿主 PostgreSQL（`public` Schema，带独立表名与迁移前缀）中管理业务表，并直接与原生 Service 表关联查询：

```python title="acme_reports/models.py"
from a13n_service.app_database import AppDatabase
from a13n_service.tenancy.tables import WorkspaceRow
from sqlalchemy import ForeignKey
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

database = AppDatabase("acme.reports", migrations="acme_reports:migrations")
database.reference(WorkspaceRow.__table__)


class Base(DeclarativeBase):
    metadata = database.metadata


class Note(Base):
    __tablename__ = database.name("notes")
    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    body: Mapped[str]
```

将 `database=database` 传入 `AppDefinition`，并针对临时 PostgreSQL 库生成与校验迁移：

```bash
a13n-service apps db init --app acme.reports
a13n-service apps db revision --app acme.reports -m "create notes" --scratch-url "$APP_TEST_POSTGRES_URL"
a13n-service apps db check --app acme.reports --scratch-url "$APP_TEST_POSTGRES_URL"
```

业务查询使用 `a13n_service.infra.db` 中的 `short_session(runtime.storage)` 或 `transaction(runtime.storage)`。启动 `worker` 副本前，由 `control`、`all` 或 `a13n-service migrate` 完成 Core 与已安装 App 的迁移升级。

## 添加 Console 页面

构建前端 ESM 入口并将 `@a13n/console` 设为外部依赖，默认导出 `AppContribution`，并在 `AppDefinition` 中声明 `Frontend`：

```tsx
import { React, useApp, useQuery } from "@a13n/console";
import type { AppContribution } from "@a13n/console";

function ReportsPage() {
  const app = useApp();
  const summary = useQuery({
    queryKey: app.queryKey("summary"),
    queryFn: async ({ signal }) => (await app.request("/summary", { signal })).json(),
  });
  return <div>{summary.data?.workspace_id}</div>;
}

export default {
  pages: [{ path: "reports", label: { en: "Reports", "zh-CN": "报告" }, component: ReportsPage }],
} satisfies AppContribution;
```

```python
from a13n_service.app_plugins import Frontend

frontend = Frontend(
    package="acme_reports", directory="static", entry="index.js", styles=("index.css",)
)
```

导出 TypeScript 类型声明供编辑器补全与 `make typecheck` 使用：

```bash
python -m a13n_service.console_sdk frontend/.console-sdk/index.d.ts
```

## 上报自定义指标

通过 `opentelemetry-api` 使用带 App 前缀的指标名记录业务指标，Service 会在 `/metrics` 端点一并导出：

```python
from opentelemetry import metrics

meter = metrics.get_meter("acme.reports")
previews = meter.create_counter("acme.reports.previews", description="Report previews completed")
```

指标标签须为有限枚举值——切勿将工作区、用户、运行 ID、原始 URL 或凭据作为标签属性。

## 部署与启用

1. 在所有 `control` 与 `worker` 环境中安装相同版本的 App wheel（若关闭了自动迁移，先执行 `a13n-service migrate`）。
2. 如需在首次初始化部署时默认启用，可在 `service.toml` 中配置：

```toml
[plugins]
bootstrap_enabled_apps = ["acme.reports"]
```

3. 在 **Settings → Organization → App plugins** 或通过 API（先 `GET` 获取组织 ETag，再带 `If-Match` 调用 `PUT /api/v1/organizations/{org_id}/app-plugins/acme.reports` 传入 `{"enabled": true}`）按组织启用或停用。已受理的运行会保持其启用快照直至结束。

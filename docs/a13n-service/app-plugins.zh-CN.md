---
title: 使用 App Plugins 扩展平台能力
description: 开发并安装可信 Python App 插件，为 Service 部署扩展自定义工具集、HTTP 路由、外部事件处理器与 Prometheus 业务指标。
---

App 插件为 Service 添加业务 API、组织范围后台工作、Harness factory 和打包的 Console 资源。运维人员在所有 API 和 worker 进程中安装可信 Python 包。组织管理员可以逐个启用 App。运维人员可以在首次初始化部署时指定初始启用列表。

App 插件在 Service 进程内运行，拥有进程权限。安装前应审查软件包。[MCP Apps](mcp-apps.md) 是用于交互式工具结果的另一套协议。

## 部署 App 插件

所有 `control` 和 `worker` 进程都使用相同的一组 App 包和版本，包括只提供 Console 页面或管理 API 的 App。`all` 进程使用同一组包，同时承担两种角色的职责。一个 Service Python 环境可以安装多个 App 包。

- **自定义 Service 镜像：** 构建镜像时，将选定的 App wheel 及其依赖安装到 Service 使用的 Python 环境。`control` 和 `worker` 使用同一个镜像。如果 App 提供前端，预构建的资源随 wheel 一起分发，无需重新构建 Console。
- **本地 Service：** 将选定的 wheel 安装到启动 Service 的 Python 环境。例如，将 wheel 放入 `./app-wheels/`，再运行 `uv pip install --python /path/to/service/.venv/bin/python ./app-wheels/*.whl`。

重启 Service 以发现已安装的包。安装后，App 在整个部署中可用；组织侧保持禁用，直到管理员启用，或首次初始化写入该组织的策略。

`control` 提供 App API 和 Console 资源、运行组织级后台扫描，并按配置执行自动迁移。`worker` 执行 agent 及其使用的 App Harness 扩展。它在启动时检查 App 数据库版本，但不执行迁移或组织级后台扫描。对于带数据库迁移的 App，在启动依赖新 schema 的 worker 前，先由 `control`、`all` 或专用迁移任务完成迁移。

无论组织是否启用 App，其 `startup` 回调都会在每个 Service 进程中运行。用它初始化进程级资源；组织级后台工作应放在 sweep 中，而不是 `startup` 中。

### 首次初始化时启用指定 App

对于预装了 App 的全新部署，在 `service.toml` 里指定初始策略：

```toml
[plugins]
bootstrap_enabled_apps = ["acme.reports"]
```

使用 Docker Compose 时，在负责 Console 初始化的 Service 或运行 `a13n-service bootstrap` 的任务中设置环境变量 `A13N_PLUGINS__BOOTSTRAP_ENABLED_APPS: '["acme.reports"]'`。填写 App ID，不是 Python 软件包名，也不是 `plugins.keys` 中的 Harness factory key。执行初始化的进程必须装有列表里的所有 App；缺少任何一个，初始化都会失败，不会创建组织。

列表默认为空，仅在创建首个组织时写入一次。之后通过组织的 App 管理界面或 API 调整；修改部署配置、升级镜像或重启 Service 都不会重新应用该列表。

## 创建独立 App

[App 插件模板](https://github.com/converge-ai-labs/agent-foundation/tree/template/app-plugin)可生成全栈、仅前端或仅后端的独立项目，包含 Makefile 和 GitHub Actions 检查。现有 [Reports 示例](https://github.com/converge-ai-labs/agent-foundation/tree/main/examples/service-app-plugin)仍是单独的集成示例。

安装 Git、uv、Make、Python 3.13 和 Docker Compose v2；前端项目还需要 Node.js 24 与 pnpm 10.30.3。生成项目，进入项目目录并启动：

```bash
uvx cookiecutter gh:converge-ai-labs/agent-foundation --checkout template/app-plugin
cd my-service-app
make dev
```

`make dev` 使用项目固定的 `a13n-service` wheel 和内置 Console，以可编辑模式安装 App，并启动本地 PostgreSQL、Redis 和 S3。命令会迁移数据库，并在首次启动时初始化开发管理员和工作区。打开终端显示的 Console URL，登录后进入 **设置 → 组织 → App 插件**启用 App。带 Console 页面的 App 会出现在工作区侧栏。若旧项目仍包含 `.service-ref`，继续使用该项目原有的 README 和启动流程。

修改 Python 后，开发命令会重启本地 Service。需要挂接调试器而不自动重启时，使用 `make dev RELOAD=0`。修改 TSX 或 CSS 后，等待构建完成，再刷新 App 页面；开发资源提供 sourcemap，不使用生产缓存。Ctrl+C 会停止本地 Service 和 watcher；`make dev-down` 会停止容器并保留卷。

`make verify` 构建并测试已安装的 wheel，不会替换可编辑安装。升级 Service 时修改 `pyproject.toml` 中的依赖并更新项目锁文件。生成项目不会创建远程仓库或替你选择许可证；生成的 README 介绍了如何创建仓库和独立发布 App 的 `vX.Y.Z` 版本。

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

`OrganizationSweep` 回调接收已有 Runtime 和一个已启用的组织 ID。Service 选择组织，并在回调前关闭 SQL session。回调负责有界业务工作以及多副本安全的认领。每个组织使用声明的超时时间；某个回调失败后，同批其他组织继续执行，该轮扫描记为失败。可选的 `startup(runtime, stack)` 打开进程资源，并在已有退出栈上注册清理。每个已安装 App 都会运行 startup，与组织启用状态无关。App 可以声明带独立迁移的可选数据库，见下文。Service 在运行任何 App startup 回调前校验所有已安装 App 的数据库版本。

## 响应 Run 封口并提交 Inbox 消息

在 `AppDefinition(run_seals=(...,))` 中声明 `RunSealSubscription(callback, timeout=5)`。异步回调接收 Runtime 和 `RunSealEvent`，后者包含组织、工作区、Session、Thread、Run 和 principal ID、状态以及接受 Run 时固定的 options 标签。默认选择 completed、failed 和 cancelled；需要 waiting 时，在 `statuses` 中显式加入。Service 只在提交后发布提示，并在回调前检查 App 当前是否启用。有界 Redis Stream 的 consumer group 使用 NOACK 将每条保留的提示交给一个 control 副本。创建组之前发布、读取前被裁剪或接收后丢失的提示不会重试；提示也可能重复。保留组织 sweep，并确保业务操作幂等。发布和回调并发有上限；通过 `a13n_app_run_seal_dropped_total` 和 `a13n_run_seal_publish_dropped_total` 可以发现容量不足造成的丢弃。中断后集中到达的提示可能超出回调上限，因此 App 必须通过自己的 sweep 对账持久状态，不能依赖收到每条提示。

等待中的 Run 已封存，其历史记录会保持 waiting 状态。恢复操作会创建具有新 ID 的后继 Run；根据提示执行操作前，请检查 Thread 当前的等待状态。每个部署需要独立的 Redis 逻辑数据库。多个部署共享 Redis 服务器时应选择不同数据库；如果服务器只支持数据库 0，请为每个部署使用独立实例。

为自动操作配置 service account principal。也可以使用用户 principal，Service 会检查其当前权限。事件中的 principal 属于原始 Run，并不是操作授权。准备提交时会持有组织锁直到事务结束，同一组织内的 App 提交因此会串行；尤其在大量并发跟进时，应保持事务短小。调用 `runs.submit.submit_app_message(runtime, principal_id, workspace_id, thread_id, message, request_key=...)`，Service 会按当前权限和工作区范围提交。需要与 App 数据原子提交时，使用 `infra.db.transaction`，在获取任何 App 行锁或 Thread acceptance lock 之前调用 `prepare_app_submission(session, runtime, workspace_id, principal_id)`（锁顺序为组织 → App → Thread），再将返回值传给 `submit_message_in_transaction`。该准入结果只属于当前事务。自动跟进时，在 Thread 锁内检查预期的最近 Run、空闲且未暂停的状态，以及原生 Inbox 是否已有待处理输入，再提交消息。事务提交后回执才持久化；普通输入不会回答等待中的请求，也不保证送达指定的活动 Run。这些 API 不应用 Run 的委托权限上限，不能用于 Run 委托操作。

## 使用进程内 App SDK

已安装的插件可通过 `a13n_service.app_sdk` 使用原生 Thread、项目资源、Agent 修订版本、Schedule、出口凭据和模型用量接口。接收 session 参数的操作使用 `app_sdk.transaction(runtime.storage)`。要在同一事务中创建 Thread 或提交消息，先调用 `threads.prepare_admission(session, runtime, workspace_id, principal_id, app_id=...)`，再锁定 App 状态，并将返回值传给 `threads.create_thread_in_transaction` 或 `threads.submit_message_in_transaction`。执行身份是具有 `run` 权限的工作区服务账号。为每个请求使用带 App 前缀的稳定幂等键，将受理回执和业务状态写入同一个事务。

使用 `threads.update_thread_resources` 更新根 Thread 和共享 Environment 的后代 Thread，包括动态子 Thread。传入 `environment_template_id` 会将替换环境的创建操作入队，并通过 `UpdatedThreadResources` 返回新环境的 `environment_id` 和根 Thread 的 `browser_id`。可在同一事务内将这些 ID 与 App 状态一起保存。同一工作区有多个 App 实例时，在列出 Schedule 和统计活跃 Schedule 时均传入实例的 `labels`。匹配的 Schedule 即使已归档，只要还有待处理的触发记录，也计入活跃数。

App 自己维护的令牌通过 `egress.create_app_credential`、`rotate_app_credential` 和 `revoke_app_credential` 管理。操作归因于组织内当前有效的身份，工作区 Runner 账号无需提升为 `write`。运维人员可以在 Console 查看凭据所有者并请求同步 Session。**滚动升级时，先升级所有提供 API 的副本，再启用会创建 App 凭据的功能。** 旧副本不会执行新的修改限制。

要让凭据变更和 App 状态一起提交，请在同一个 Service 事务内调用 `egress.create_app_credential_in_transaction`、`egress.rotate_app_credential_in_transaction` 或 `egress.revoke_app_credential_in_transaction`。轮换和撤销只在事务提交后通知活跃 Session。

使用 `threads.update_app_egress_secrets` 修改空闲的共享 Thread 子树的主绑定。`set_secrets` 接收 App 自有凭据的 `egress.SecretReference`，`remove_envs` 接收要移除的环境变量名。每个 Thread 保留自己的出口目标策略和其他所有者的引用。如果绑定既没有出口模式，也没有出口策略，添加引用时必须显式传入 `initial_destinations`。例如，使用 `a13n_envd_client.eip.v1` 中的 `AllowlistDestinations(mode="allowlist", hosts=("api.example.com",))`，即可为该主机初始化受控出口。只有明确允许访问公网目标时，才使用 `PublicDestinations(mode="public")`。这两种设置都不会覆盖现有策略。移除引用会保留出口目标策略；要让活跃 Session 中的凭据失效，请撤销该凭据。

## 持久化业务数据

App 需要在宿主 PostgreSQL 数据库中建表时，使用 `AppDatabase`。每个 App 在 `public` 中拥有独立的迁移历史。App 使用普通 SQLAlchemy 模型和现有 Runtime Storage，可以直接关联查询原生 Service ORM 行，无需另建连接池。前缀划分迁移归属；App 代码仍需执行租户范围限制和授权。

在 `models.py` 等模块中声明数据库和模型，再从 `app.py` 导入该模块，并将同一数据库对象传入 `AppDefinition(database=database)`。部署配置必须在导入模型前解析：

```python
import os

from a13n_service.app_database import AppDatabase
from a13n_service.tenancy.tables import WorkspaceRow
from sqlalchemy import ForeignKey
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Optional deployment override. Leave unset for a stable App-ID-derived prefix.
database = AppDatabase(
    "acme.reports",
    migrations="acme_reports:migrations",
    prefix=os.environ.get("ACME_REPORTS_TABLE_PREFIX") or None,
)
database.reference(WorkspaceRow.__table__)  # Read-only foreign-key target, never App-owned DDL.


class Base(DeclarativeBase):
    metadata = database.metadata


class Note(Base):
    __tablename__ = database.name("notes")

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    body: Mapped[str]
```

默认物理表名为 `app__acme_report_8b627b7edb3d__notes`。标签由可读短名和完整、精确 App ID 的稳定摘要组成，因此 `acme.reports` 与 `acme_reports` 获得不同命名空间，与安装顺序无关。长 App ID 也无需额外配置。可选显式标签须使用小写 snake case，以字母开头，且不超过 24 字节。例如，`ACME_REPORTS_TABLE_PREFIX=reports_1` 选择 `app__reports_1__notes`。通过 `database.name(...)` 获取名称，不要硬编码物理名称。同一部署的 migration、control 和 worker 进程须使用相同配置；编写迁移时可以使用默认前缀，因为生成的标识符支持跨前缀部署。Service 拒绝重复 App ID 和冲突的显式前缀。数据库初始化后，保留的 App ID/前缀绑定会阻止意外改指其他命名空间。修改配置不会搬迁现有数据；迁移已部署的命名空间需要显式审查的数据库迁移。

1. 在兼容 Service 的开发环境中以 editable 方式安装 App。在 App 项目根目录执行以下编写命令。当前项目只有一个明确的 App 时，可省略 `--app`。

   ```bash
   a13n-service apps db init --app acme.reports
   a13n-service apps db revision --app acme.reports -m "create notes" \
     --scratch-url "$APP_TEST_POSTGRES_URL"
   a13n-service apps db check --app acme.reports \
     --scratch-url "$APP_TEST_POSTGRES_URL"
   ```

`init` 只创建声明的包内迁移目录。`revision` 和 `check` 在新建的临时数据库中回放 Core 及目标 App 的历史，不会升级配置中的业务数据库。`--scratch-url` 可选择本地或 CI PostgreSQL 服务器；省略时使用 Service 配置中的服务器。所选角色须具有创建和删除临时数据库的权限。不要将开发检查指向生产服务器。

审查生成的 Alembic 草稿，包括破坏性变更、锁、数据迁移和回滚行为。生成的标识符通过 `migration_name("notes")` 在执行时解析所选前缀，因此同一包内历史支持不同部署的前缀。默认值和数据字符串保持原样。手写 SQL、表达式和其他数据库对象需要显式使用 `migration_name`，并手动检查可移植性。将 `migrations/versions/*.py` 包含在 wheel 和源码包中；先运行 `apps db check`，再发布。

部署时，在各进程安装相同的 App wheel 并配置相同前缀。默认情况下，`all` 和 `control` 启动时依次升级 Core 和所有已安装 App 的数据库。采用独立迁移任务时运行：

```bash
a13n-service migrate
a13n-service apps db status --app acme.reports
# Apply only this App after Core is current:
a13n-service apps db upgrade --app acme.reports
```

`migrate` 先升级 Core，再逐个升级所有已安装 App；`migrate --no-apps` 只升级 Core。各 App 的历史与版本表相互独立。使用独立迁移任务时，在服务副本中设置 `A13N_DATABASE__AUTO_MIGRATE=false`。如果只自动升级 Core、单独管理 App 升级，则保持总开关开启并设置 `A13N_DATABASE__AUTO_MIGRATE_APPS=false`。这两个配置只影响启动，显式 CLI 命令仍执行迁移。`migrate --check` 对部署数据库执行只读 Core/App 一致性检查，不需要 `CREATE DATABASE` 权限；`apps db check` 用于临时数据库上的开发和 CI 回放。所有角色在 App 回调前校验已安装 App 的绑定和精确版本，包括组织尚未启用的 App。worker、readiness 和组织启用操作都不会执行 DDL。卸载包会保留数据、版本表和命名空间绑定。

业务查询使用 `a13n_service.infra.db` 中的 `transaction(runtime.storage)` 或 `short_session(runtime.storage)`。每个会话只围绕一次短数据库操作。原生关联查询使用同一个 ORM 会话：

```python
from a13n_service.infra.db import short_session
from sqlalchemy import select

async with short_session(runtime.storage) as session:
    rows = (
        await session.execute(
            select(Note.body, WorkspaceRow.name)
            .join(WorkspaceRow, Note.workspace_id == WorkspaceRow.id)
            .where(WorkspaceRow.id == authorized_workspace_id)
        )
    ).all()
```

测试中可复用 `a13n_service.migrations.scratch.scratch_database(development_database_settings)`：在其作用域内调用 `a13n_service.migrations` 中 Core 的 `runner.upgrade(scratch, OSS)` 和 App 的 `apps.upgrade(scratch, OSS, database)`，再打开普通 `Storage(scratch.url.get_secret_value())`。在退出作用域前关闭 Storage。这样测试会执行包内历史，并在结束后删除临时数据库。CI 加入 `apps db check`；`status` 检查部署中的版本状态，`check` 则从全新回放验证迁移与模型一致。

## 上报自定义指标

App 插件复用 Service 的进程级 metrics provider 和私有 `/metrics` 监听器。使用标准 `opentelemetry-api` 包；App 导入该包时，应将 `opentelemetry-api>=1.39,<2` 声明为直接依赖。无需在 `AppDefinition` 中注册，也无需另建 exporter。运维人员按照[监控指南](monitoring.md#metrics)启用 `telemetry.metrics_port`，并抓取每个 control 和 worker 进程。

指标名称使用 App 自己的前缀。只有 meter scope 不足以隔离 Prometheus 序列，因为 Service exporter 不导出 scope 标签。以下代码扩展 [Reports 示例](https://github.com/converge-ai-labs/agent-foundation/tree/main/examples/service-app-plugin)的 `/preview` handler，统计完成的预览次数，并测量成功 handler 的耗时：

```python
from time import perf_counter

from opentelemetry import metrics

meter = metrics.get_meter("acme.reports")
previews = meter.create_counter("acme.reports.previews", description="Report previews completed")
preview_duration = meter.create_histogram(
    "acme.reports.preview.duration",
    unit="s",
    description="Successful report preview handler duration",
    explicit_bucket_boundaries_advisory=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 5),
)


@router.post("/preview", name="preview_acme_report")
async def preview(body: PreviewRequest, workspace_id: WorkspaceId, actor: Actor, runtime: CurrentRuntime) -> Preview:
    started = perf_counter()
    async with short_session(runtime.storage) as session:
        await workspace_scope(session, actor, workspace_id, "write")
    result = Preview(workspace_id=workspace_id, title=body.title)
    # Host admission and the action's write check both succeeded.
    previews.add(1, {"result": "completed"})
    preview_duration.record(perf_counter() - started, {"result": "completed"})
    return result
```

现有示例提供路由、请求与响应类型、授权相关导入；应替换其中的 handler，不要重复注册 `/preview` 路由。Prometheus 导出 `acme_reports_previews_total` 和 `acme_reports_preview_duration_seconds` histogram。计数器测量成功执行的 handler 调用，不表示独立报告数量或浏览器已收到结果。宿主 HTTP 指标已经覆盖被拒绝和失败的请求。组织 sweep 已通过 `sweep="app:acme.reports:<name>"` 获得宿主的扫描、失败和新鲜度指标；只补充这些信号无法回答的业务测量。自定义指标使用独立 Grafana 面板和告警；内置看板覆盖 Service 指标。

遵循以下规则：

- Service 负责 meter provider、监听器和进程关闭。App 不应调用 `set_meter_provider()`、`serve_metrics()` 或 `start_http_server()`。关闭 metrics 时，标准 API 不执行导出。直接调用 `build_app()` 的嵌入式 Host 负责初始化遥测。
- 可以在模块导入时创建 meter 和 instrument。应在可执行程序完成初始化后，从 startup 或已获准的业务工作中记录观测；provider 初始化前的观测会被丢弃。`startup` 钩子在进程启动时执行一次，因此其指标反映进程级加载状态；按组织启用的业务指标请在运行期上报。
- 使用固定的指标名称，以及代码中定义的有限属性值。指标标签不得包含组织、workspace、用户或 run ID、模型名称、报告标题、原始 URL、凭证或载荷。宿主不会自动清洗 App 自定义属性。
- 耗时使用秒，并为具体操作选择 histogram bucket。持久化业务事实只能在事务提交后计数，使用现有 `after_commit(session, callback)` 边界。操作尝试指标可以统计每次尝试，包括重试；名称应明确表达这一口径。
- Observable gauge 回调在抓取期间执行，只能读取内存缓存快照。通过有界、已获准的后台工作刷新外部系统、SQL 或 Redis 状态，并为库存快照同时发布新鲜度时间戳。禁用 App 会阻止后续组织回调，不会清除已经导出的计数值或缓存样本；看板查询应排除过期样本。

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

`useApp()` 提供 `appId`、`organizationId`、`workspaceId`、带作用域的 `queryKey(...)` 和 `request(path, init)`。请求路径以 `/` 开头，规范化后仍位于 `/api/v1/apps/{appId}/` 下。Host 添加所选 workspace 请求头，并使用已有认证、CSRF 处理和传输层。业务请求需要选择 workspace。

在组织设置中，Host 选择记住的可访问 workspace；没有记住的 workspace 时，选择第一个活跃 workspace。如果只有已归档 workspace 可用，Host 选择第一个。没有可用 workspace 时，`workspaceId` 为 undefined。设置的 scope 决定设置项的显示位置；App 业务 HTTP 请求仍以 workspace 为作用域。

页面位于 `/workspace/{workspaceId}/apps/{appId}/{pageId}/*`。页面 ID 以字母开头，使用小写字母、数字和连字符。可选的 `description` 和 `permission` 字段用于描述页面，并控制其在 Console 中的显示和路由访问。业务端点仍自行执行授权。标签和描述可以是字符串，也可以是包含 `en` 和可选 `zh-CN` 的对象。`settings` 条目使用相同字段，并添加 `scope: "workspace"` 或 `"organization"`；Host 中的设置 key 为 `app.{appId}.{sectionId}`。

权限过滤后，如果有多个页面可见，Console 会将它们分组显示在 App 名称下。可通过 `navigation: { label: { en: "Reports", "zh-CN": "报告" }, icon: "chart" }` 自定义分组。支持的图标 key 为 `puzzle`、`robot`、`chart`、`calendar` 和 `lightning`，默认为 `puzzle`。只有一个可见页面时直接显示链接；没有可见页面时不显示导航项。

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

使用 `ChoiceField` 提供带标签的选择字段，使用 `SearchPicker` 提供可搜索的选项，或使用 `SegmentedControl` 在原位切换模式。用 `Field` 及其配套组件组合自定义字段，用 `Select` 搭配 `SelectTrigger`、`SelectValue`、`SelectPopup` 和 `SelectItem` 组合自定义下拉菜单。使用 `ReadOnlyField` 显示带标签的只读值。

### 为独立 App 启用类型检查

Service 从 0.2.2 起随 Console 提供 TypeScript 声明。App 模板的 `make setup` 安装锁定版本的 Service 并导出声明，供编辑器补全使用。`make typecheck` 针对该版本检查 App；`make verify` 和生产前端构建也执行同一检查。

已有独立 App 可在安装或升级 Service 后，在 App 的 Python 环境中运行：

```bash
python -m a13n_service.console_sdk frontend/.console-sdk/index.d.ts
```

在 `frontend/tsconfig.json` 的 `compilerOptions.paths` 中将 `@a13n/console` 映射到 `./.console-sdk/index.d.ts`，不要提交生成的声明，并使用模板中的前端开发依赖解析声明引用的类型。打包时仍需将 `@a13n/console` 保持为外部依赖。不需要安装 Console SDK npm 包，也不应在 App bundle 中引入第二套 React 运行时。使用较新的桥接导出时，请锁定构建与测试所用的 Service 版本。

列表使用 `Page`、`Toolbar` 和 `ResourceTable`，详情使用 `DetailPage` 和 `DetailHeader`，编辑表单使用 `FormSection`、`FormField` 和 `FormActions`。模板展示了这些组件的组合，并继承宿主的浅色和深色主题。

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

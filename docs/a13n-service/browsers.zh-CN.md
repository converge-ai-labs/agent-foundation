---
title: 浏览器
description: 接入已有浏览器或预留托管浏览器，再绑定到原生 agent 工具。
---

浏览器资源让多个 run 和 thread 共享活跃浏览器。使用 `managed` 让 provider 分配浏览器，或使用 `external_cdp` 接入已有浏览器。Service 独立于[环境](environments.md)管理访问、标签页归属和逻辑选择。

> [!IMPORTANT]
> 外部 CDP 接入无需浏览器 provider。内置发行版尚无真实托管浏览器供应商或 Console 接管界面。托管分配需要部署方注册 provider，可通过 `GET /api/v1/provider-types/browser` 查询。人工控制需要兼容的托管 provider 和接入应用。

## 预留并绑定浏览器

在同一工作空间内使用[已认证的 HTTP 请求](http.md)。创建、绑定和使用浏览器需要工作空间 `run` 权限；释放需要工作空间 `write` 权限。Provider 账号仍受自身权限约束。

1. 按已注册类型的 schema，通过 `POST /api/v1/browser-providers` 创建浏览器 provider 账号。凭证只通过该 API 保存，不要放入 agent 消息。
2. 通过 `POST /api/v1/browsers` 预留浏览器，设置 `provider_id`、`name` 和 provider 的 `recipe`。省略 `prepare` 时只预留标识，不分配供应商会话。
3. 启用 agent 修订版本的 `browser` 工具集，只选择必要的工具和[权限](agents-and-runs.md#tool-permissions)。
4. 读取 thread 的当前 ETag。向 `PUT /api/v1/threads/{thread_id}/browser` 发送该 `If-Match` 及下方绑定。应在提交需要使用该浏览器的 run 之前完成绑定。

```json
{
  "browser_id": "brw_example",
  "tab_access": "thread",
  "preparation": "first_use"
}
```

将示例 ID 替换为返回的浏览器 ID。通过 `GET /api/v1/threads/{thread_id}/browser` 读取期望绑定；向 PUT 端点发送 JSON `null` 可解除绑定。绑定变更对后续 run 生效，已接受的 run 保留原绑定。子 thread 和 fork 得到的 thread 需要显式绑定。

原生工具支持观察、读取、导航、操作、等待、标签页、对话框、截图及下载记录查看。在 provider 提供经过授权的传输边界之前，上传不可用。浏览器所在主机的文件路径不是 run 所挂载环境中的路径。

## 接入已有浏览器

使用指向同一稳定 Chromium 实例的浏览器级 `ws://` 或 `wss://` debugger 端点。HTTP 发现 URL 和页面级 socket 会被拒绝。URL 和可选请求头均为敏感信息，只通过接入 API 提交，不要放入 agent 消息。Service 对其加密，普通读取不会返回这些信息。更换端点需要新建接入资源并显式重新绑定。

Service worker 必须能访问该端点。Loopback 指向该进程的网络环境。注册及每次连接都会检查目标策略；普通 WS 必须在部署的 `providers.http_origins` 中显式允许对应 HTTP origin。Run 的 `allowed_hosts` 限制同样适用。连接拒绝重定向。

以下流程使用 `curl`、`jq`、[HTTP 认证变量](http.md#authentication)、已有且空闲的 `THREAD_ID`，以及修订版本启用了 `toolsets.browser.enabled` 的 `AGENT_ID`。将完整敏感端点保存在 `CDP_URL`，可选的授权值保存在 `CDP_AUTHORIZATION`。

1. 注册接入，不建立连接也不分配浏览器。响应包含 `source: "external_cdp"`、`provider_id: null`、`status: "reserved"` 和 `idle_seconds: null`。

```sh
BROWSER_ID=$(jq -n --arg url "$CDP_URL" --arg auth "${CDP_AUTHORIZATION:-}" \
  '{name: "Existing browser", cdp_url: $url, headers: (if $auth == "" then {} else {Authorization: $auth} end)}' | \
  curl --fail-with-body -sS "$A13N_URL/api/v1/browsers/attach" \
    -H "Authorization: Bearer $A13N_API_KEY" -H 'Content-Type: application/json' --data-binary @- | jq -r '.id')
```

2. 提交工作前绑定该资源。使用 `global` 查看已有页面；`thread` 只看到该 thread 创建的标签页及其弹出页。

```sh
THREAD_ETAG=$(curl --fail-with-body -sS "$A13N_URL/api/v1/threads/$THREAD_ID" \
  -H "Authorization: Bearer $A13N_API_KEY" | jq -r '"\"" + .id + ":" + (.version|tostring) + "\""')
jq -n --arg id "$BROWSER_ID" '{browser_id: $id, tab_access: "global", preparation: "first_use"}' | \
  curl --fail-with-body -sS -X PUT "$A13N_URL/api/v1/threads/$THREAD_ID/browser" \
    -H "Authorization: Bearer $A13N_API_KEY" -H "If-Match: $THREAD_ETAG" \
    -H 'Content-Type: application/json' --data-binary @-
```

3. 使用新的幂等键提交原生工具工作。首次获准的浏览器调用会验证并连接已有浏览器。要提前验证，可调用 `POST /api/v1/browsers/$BROWSER_ID/prepare`；准备操作不会创建浏览器。

```sh
jq -n --arg agent "$AGENT_ID" \
  '{agent_id: $agent, payload: {content: [{type: "text", text: "Use browser_tabs to list pages. Select the relevant tab explicitly, then use browser_observe to inspect it. Do not close existing tabs."}]}}' | \
  curl --fail-with-body -sS "$A13N_URL/api/v1/threads/$THREAD_ID/inbox" \
    -H "Authorization: Bearer $A13N_API_KEY" -H "Idempotency-Key: $(uuidgen)" \
    -H 'Content-Type: application/json' --data-binary @-
```

4. 按[读取结果](connect-application.md#read-the-result)跟踪返回的 entry 和 run。Run 完成后，外部进程及页面保持存活。等待活跃借用结束后再释放。
5. 如果后续 run 不应继续借用，使用新的 thread ETag 和 JSON `null` 解除期望绑定。解除绑定不会关闭页面。
6. 读取新的浏览器 ETag，释放 Service 访问权限。释放会清除加密连接信息，包括从未准备过的接入资源；外部进程、标签页及登录状态保持不变。

```sh
BROWSER_ETAG=$(curl --fail-with-body -sS "$A13N_URL/api/v1/browsers/$BROWSER_ID" \
  -H "Authorization: Bearer $A13N_API_KEY" | jq -r '"\"" + .id + ":" + (.version|tostring) + "\""')
curl --fail-with-body -sS -X POST "$A13N_URL/api/v1/browsers/$BROWSER_ID/release" \
  -H "Authorization: Bearer $A13N_API_KEY" -H "If-Match: $BROWSER_ETAG"
```

网络故障报告 `browser_connection_failed`，不会据此宣告实例丢失。后续连接重试同一端点，恢复精确的目标归属和选择。操作前重新观察；失败或结果未知的操作不会重放。网关静默替换浏览器时，无法保证连续性。外部资源的 viewer、接管及归还请求在控制状态变化前返回 `browser_viewer_unsupported`。

## 选择准备和保留策略

| 选项                                                             | 行为                                                        |
| ---------------------------------------------------------------- | ----------------------------------------------------------- |
| `preparation: "first_use"`                                       | 默认值。首次获准的浏览器调用触发准备；外部资源只建立连接。  |
| `preparation: "run_start"`                                       | 开始执行时准备浏览器。                                      |
| 创建时设置 `prepare: true`，或调用 `POST /browsers/{id}/prepare` | 在 agent 使用前显式准备。                                   |
| `idle_seconds: 1800`                                             | 托管默认值。达到可释放的空闲时长后释放；允许 60–604800 秒。 |
| `idle_seconds: null`                                             | 外部默认值。保留到显式释放或托管供应商会话过期。            |

准备与保留策略相互独立。读取浏览器资源的 `status`、`control`、`expires_at` 和 `failure`。`reserved` 表示尚无就绪的分配；`creating` 表示准备结果未确定；`ready` 表示已确认活跃会话。元数据读取不会触发分配，并发准备会收敛到同一分配。托管创建失败后，准备会检查原标识。只有供应商确认请求未分派时，才会重试创建。如果结果持续未确定，应显式释放以完成供应商清理隔离，再预留并绑定新浏览器。

活跃 run、尚未结算的浏览器调用、人工控制和有效的待处理归还通知会阻止空闲释放。供应商到期仍可能终止会话。`lost` 或 `deleted` 的浏览器不会自动重启。需要替换时，创建并绑定另一个浏览器。

Run 完成和解除绑定都会保留浏览器及标签页。要释放 Service 访问权限，先读取浏览器的当前 ETag，再带 `If-Match` 调用 `POST /api/v1/browsers/{id}/release`。活跃使用导致释放不安全时，Service 会拒绝请求。托管释放会终止供应商会话，不保存可恢复的 profile。外部释放保留已有浏览器。借用者及活跃 claim 结束后，显式释放可以终止处于自动化控制的资源标识，即使旧调用的结果仍未知。历史仍保持未知；外部操作可能仍在运行。已释放的标识不能重用。期望绑定指向 lost 或 deleted 浏览器时，后续 run 会被拒绝，必须显式解除绑定或绑定另一个浏览器。

## 选择标签页访问范围

默认使用 `thread`：原生工具只能看到该 thread 拥有的精确标签页及其弹出页。`global` 暴露实时标签页清单，包括其他 thread 的标签页。两种模式共享 cookie 和登录状态；thread 模式限制工具目标，不隔离账号。

每个 thread 的标签页选择独立于浏览器原生焦点。选中的标签页关闭后，需要显式选择或创建另一个标签页。Service 不会仅凭相同 URL 或标题选择另一页面。Worker 重连恢复精确归属和选择，不恢复旧的可操作引用；操作前应重新观察。

## 接管并归还控制权

对于支持 viewer 的托管资源，接入应用通过直接 HTTP 调用实现下列流程。接管、续期、归还和交互视图访问不进入 MCP 发现。

1. 读取浏览器的当前 ETag，带 `If-Match` 和 `{"thread_id": "<bound-thread-id>", "lease_seconds": 300}` 调用 `POST /api/v1/browsers/{id}/takeover`。 控制租约从 Service 关闭准入时开始计时，包含 draining 阶段。
2. 查询浏览器，直到 `control` 变为 `human`。`draining` 表示 Service 已关闭浏览器工具准入，正在等待此前获准的工作结束。结果未知的调用不会因超时被当作成功完成。
3. 以控制方主体调用 `POST /api/v1/browsers/{id}/viewer`。返回的 URL 是敏感信息，请遵守有效期和响应的 `no-store` 策略。
4. 应用认为用户仍在操作时，向 `POST /api/v1/browsers/{id}/renew` 发送响应中的 `generation`、`episode` 和可选的 `lease_seconds`。在 `control_expires_at` 前续期，包括 draining 阶段。续期不需要 `If-Match`。控制方必须仍有浏览器权限和工作区 `run` 权限。
5. 完成人工操作后，读取新的 ETag，带 `If-Match` 调用 `POST /api/v1/browsers/{id}/handback`。
6. 等待 `control: "automation"`。Service 会先撤销交互视图输入、同步精确标签页并使旧引用失效，再开放工具。

如果接管仍停留在 `draining`，且能够确认尚未分派任何 viewer grant，可在活跃 claim 结束后通过归还取消意图。取消会抑制待处理的归还通知，不会宣称浏览器已可用。结果未知的调用仍阻止工具；借用者结束后，可显式释放资源标识。如果 viewer grant 可能已经发生，归还仍须正常撤销它。

人工控制期间，该浏览器的所有浏览器工具都会被阻止，包括读取和等待。其他工具、模型工作和其他浏览器继续运行。受阻工具立即返回，并登记其 thread 为归还通知接收方；agent 应继续无关工作或结束，而不是轮询。

归还只向实际发生过受阻调用的 thread 发送内部可用性通知。现有 inbox 和 run 边界决定何时纳入该通知。等待审批的 run 继续等待，失败或取消的 run 保留原有的显式继续规则。停止或归档 thread 会抑制其待处理通知。受阻或结果未知的操作绝不会自动重放。

每次接管和续期都可传入正整数 `lease_seconds`，范围由部署配置 `control.browser_control_min_seconds` 和 `control.browser_control_max_seconds` 决定（默认分别为 60 和 3600）。任一操作省略该字段时，使用 `control.browser_control_seconds`（默认 300）。超出配置范围的请求会被拒绝且不改变控制状态，不会静默裁剪。三项配置必须都是正整数并满足 `min <= default <= max`，否则启动失败。每次成功续期返回实际截止时间，取原截止时间与数据库当前时间加请求租期中的较晚者。较短的续期请求不会缩短控制时间。每个请求都是新的 keepalive，不是幂等键重放。响应丢失后先读取当前浏览器，只做有界重试，不要无限重试。过期或 returning 的周期不能复活；必须等待归还完成后重新接管。

例如，在 `service.toml` 中配置部署策略（接管和续期使用同一策略）：

```toml
[control]
browser_control_min_seconds = 60
browser_control_max_seconds = 3600
browser_control_seconds = 300
```

应用自行判断用户是否仍在操作。Service 不根据 viewer 打开状态或浏览器连接推断活动。应用停止续期后，控制租约到期，Service 按显式归还的安全流程收尾。Provider 被禁用、控制方失权、交互视图过期或无法维持输入授权也会触发归还。撤销或清点失败时，工具保持阻止，状态停留在 `returning`，直到清理成功。借用者与活跃 claim 结束后，具有工作空间写入权限的用户也可使用当前 ETag 显式释放托管浏览器。释放抑制可用性通知并终止供应商会话；终止失败时保持 `deleting` 以便重试，不表示归还成功。浏览器丢失不会产生可用性通知。

应用续期只更新 PostgreSQL。Control 角色的维护流程会在 provider 的实际授权到期前，延长同一 viewer grant。Viewer URL 可能早于 `control_expires_at` 过期；必须同时遵守两个期限，并按需重新获取当前 viewer。Provider 的输入授权寿命不足以覆盖维护窗口时，Service 会安全归还，不承诺无限维持人工控制。Provider adapter 必须实现 `ensure_viewer(state, grant_id, *, expires_at)`，保证截止时间单调，以及撤销永久阻止重新授权，包括尚未完成的请求和现有输入连接。终止后迟到的 ensure 也不得复活该分配。

请求可到达任意 Service API 副本。PostgreSQL 行锁和数据库时间决定续期与到期、归还的先后。Control 副本持久认领 supplier 工作，在 I/O 期间释放事务，发布结果前重新检查 claim、generation、episode、阶段和截止时间。Claim 过期允许其他副本接续，不表示旧 supplier 调用已经停止。外部输入安全依赖 provider 对同一 grant 的单调保证和永久撤销屏障。不需要 sticky session 或进程本地计时器。

受阻调用登记跟随当前控制周期，包括长时间 draining 和 returning，不会仅因过去 24 小时而过期。验证归还成功后，Service 在排入通知的同一事务中开始有限通知期限（`control.browser_notice_seconds`，默认 86400 秒）。投递和消费时重新验证当前权限、绑定和周期；重试不会重复创建通知。

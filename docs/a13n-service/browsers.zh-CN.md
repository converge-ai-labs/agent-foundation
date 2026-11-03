---
title: 浏览器
description: 选择浏览器、连接 agent，并管理浏览器的保留策略和人工接管。
---

为 agent 配置浏览器，让它访问网站、读取页面、填写表单和截图。同一个浏览器可以跨 run 复用，也可以由多个 thread 共享。浏览器与 agent 的[环境](environments.md)分别管理。

## 选择浏览器

自托管选 **Docker**；希望浏览器运行在云端，选 **Browserbase、Kernel 或 Browser Use Cloud**；已有自行运维的浏览器，使用**外部 CDP** 接入。**Native** 用于在 Service 主机上调试。

六种方式都支持相同的核心 agent 浏览器工具。下表列出 Service 已集成的附加能力。

| 方案              | 浏览器运行位置          | 停止后启动，保留 profile | Console 查看与接管 | 上传/下载 |
| ----------------- | ----------------------- | ------------------------ | ------------------ | --------- |
| Docker            | 自有 Linux 主机的容器内 | 支持                     | 支持               | 支持      |
| Native            | 自有 Linux Service 主机 | 支持                     | 支持               | 支持      |
| Browserbase       | Browserbase 云端        | 不支持                   | 不支持             | 不支持    |
| Kernel            | Kernel 云端             | 不支持                   | 不支持             | 不支持    |
| Browser Use Cloud | Browser Use 云端        | 不支持                   | 不支持             | 不支持    |
| 外部 CDP          | 已有 Chromium 实例      | 由外部管理               | 不支持             | 不支持    |

Docker 和 Native 停止后保留 cookie 和登录状态。云浏览器集成使用临时 profile，随供应商会话结束而删除。外部 CDP 的浏览器生命周期和 profile 由你管理。

## 使用 Console

1. **启用浏览器工具。** 在 agent 修订版本中启用 `browser` 工具集，并选择所需的[工具权限](agents-and-runs.md#tool-permissions)。
2. **添加浏览器。** 使用托管浏览器时，先在 **Providers → Browser providers** 配置账号，再到 **Browsers** 创建浏览器。外部 CDP 则直接在 **Browsers** 接入端点，无需 provider 账号。
3. **绑定 thread。** 打开 thread 的 **Browser** 操作，选择浏览器并保存。访问该 thread 创建的标签页时保留 `thread`；需要访问已有标签页时选择 `global`。
4. **提交任务。** 默认的 `first_use` 在首次调用浏览器工具时准备浏览器。让 agent 打开或选择标签页，观察页面，再执行任务。

使用 Docker 或 Native 时，还可以选择 **Take control**，等待进入人工控制状态，再打开 viewer 亲自操作。完成后选择 **Return control**。仅关闭 viewer 不会归还控制权。

Run 完成后，浏览器可以继续复用，保留时间取决于保留策略和供应商超时。需要暂停 Docker 或 Native 并保留 profile 时使用 **stop**；不再需要浏览器时使用 **release**。

## 配置与 API 细节

以下按主题说明 provider 配置、HTTP 接入和生命周期选项。如果已经能在 Console 中使用浏览器，按需查阅相关章节即可。

### 配置云浏览器

Browserbase、Kernel 和 Browser Use Cloud 负责运行浏览器，Service 主机无需安装 Docker 或 Chromium。通过 Console 或 `POST /api/v1/browser-providers` 创建账号，将 API 密钥保存在 `credential` 中，并在 Service 出站策略中允许供应商 API 和安全浏览器端点。

| 账号字段                 | Browserbase              | Kernel                                           |
| ------------------------ | ------------------------ | ------------------------------------------------ |
| `type`                   | `browserbase`            | `kernel`                                         |
| `config`                 | `{"project_id": "..."}`  | `{"organization_id": "...", "project_id": null}` |
| `credential`             | `{"api_key": "..."}`     | `{"api_key": "..."}`                             |
| 浏览器 `recipe` 超时字段 | `timeout`，默认 300 秒   | `timeout_seconds`，默认 300 秒                   |
| 超时含义                 | 供应商会话的最长存续时间 | 待机超时；连接中的客户端或 viewer 可延长会话     |

使用项目范围的 Kernel 密钥时，将 `project_id: null` 替换为密钥实际所属的项目 ID。Service 在使用会话前验证密钥的组织和项目。Kernel 的 recipe 还接受 `headless`，默认值为 `true`。

使用 Browser Use Cloud 时，选择类型 `browser_use`，将 `config` 设为 `{"project_id": "..."}`，将 `credential` 设为 `{"api_key": "..."}`。recipe 的 `timeout_minutes` 为供应商会话超时（1–240 分钟，默认 5 分钟）。Service 在生命周期操作前验证密钥所属项目。

供应商超时与 Service 的[空闲保留策略](#choose-preparation-and-retention)相互独立。延长 Service 保留时间不会延长供应商会话。不要将 API 密钥或浏览器连接 URL 放入 agent 消息。

### 配置自托管浏览器

#### Docker

在同一 Linux 主机上运行 Service 和本地 Docker Engine。设置一个持久、私有且 Service 账号可写的目录：

```toml
[browsers]
root = "/var/lib/a13n/browsers"
docker_host = "unix:///var/run/docker.sock"
```

创建 `type: "docker"`、`config` 为空的 provider。默认使用 `ghcr.io/converge-ai-labs/a13n-browser:latest`，拉取策略为 `pull_policy: "always"`。浏览器 `recipe` 为空时使用默认桌面尺寸；可通过 `width` 和 `height` 修改。

要构建并使用本地镜像：

```sh
make image-browser BROWSER_IMAGE=a13n-browser:local
```

```json
{
  "image": "a13n-browser:local",
  "pull_policy": "never"
}
```

将上述 JSON 用作 provider 的 `config`。拉取策略支持 `always`、`if_missing` 和 `never`。`always` 在新建容器前拉取镜像，拉取失败则创建失败。重启保留的浏览器会复用原容器和镜像。Profile 保存在该容器中，因此不要清理仍需保留的容器。

#### Native

在 Linux 主机上安装 Chromium、`Xvfb`、`openbox`、`xauth`、`x11vnc` 和 `websockify`，确保配套可执行文件可通过 PATH 找到。显式启用 Native：

```toml
[browsers]
root = "/var/lib/a13n/browsers"
native_enabled = true
native_chromium = "/usr/bin/chromium"
```

创建 `type: "native"`、`config` 为空的 provider。Native 以 Service 账号运行，不提供租户隔离。Service 退出可能终止 Native 浏览器；后续获准启动会在旧进程停止后复用其 profile。Docker 浏览器在仅 Service 退出时继续运行。

#### 部署与网络

- 管理自托管浏览器的 control 和 worker 进程必须位于同一主机，使用相同的浏览器目录和 Docker Engine。不支持远程 Engine 或多主机调度。
- 容器化 Service 使用 Docker 浏览器时，需要挂载 Engine socket 和浏览器目录，并使用主机网络。浏览器端口发布在 Engine 主机的 loopback 接口；桥接网络中的 Service 容器无法通过自己的 loopback 访问它们。
- 保持 CDP 和 viewer 端口私有。Docker Engine 访问具有高权限，同一浏览器 Docker 网络中的容器也能访问其 CDP 端点。这些访问权限应只授予受信任进程。
- 反向代理须转发 WebSocket upgrade。Console 通过 Service 的认证转发连接浏览器，在 HTTPS 下使用 `wss`。
- 在部署网络边界限制浏览器的出站访问，包括内部服务和 metadata 端点。Service 的 provider 端点策略不限制浏览器页面导航。

### 预留并绑定浏览器

在同一工作空间内使用[已认证的 HTTP 请求](http.md)。创建、绑定和使用浏览器需要工作空间 `run` 权限及 provider 账号访问权限。停止或释放浏览器需要工作空间 `write` 权限。

1. 通过 `GET /api/v1/provider-types/browser` 查看可用类型及 schema，再通过 `POST /api/v1/browser-providers` 创建账号。
2. 向 `POST /api/v1/browsers` 提交账号的 `provider_id`、浏览器 `name` 和 `recipe`。默认只预留浏览器 ID，准备时才分配浏览器。
3. 启用 agent 修订版本的 `browser` 工具集。
4. 读取 thread 的当前 ETag，再将以下请求体发送到 `PUT /api/v1/threads/{thread_id}/browser`，并在 `If-Match` 中携带该 ETag。将 `brw_example` 替换为返回的浏览器 ID。

```json
{
  "browser_id": "brw_example",
  "tab_access": "thread",
  "preparation": "first_use"
}
```

完成绑定后再提交 run。绑定变更对后续 run 生效，已接受的 run 保留提交时捕获的绑定。子 thread 和 fork 得到的 thread 需要分别显式绑定。

通过 `GET /api/v1/threads/{thread_id}/browser` 查看期望绑定。解除绑定时，携带新的 thread ETag 向 PUT 端点发送 JSON `null`。解除绑定不会释放浏览器或关闭标签页。

### 接入已有浏览器

外部 CDP 将 Service 连接到由你运维的稳定 Chromium 实例。提供 Service worker 可访问的直接**浏览器级** `ws://` 或 `wss://` debugger 端点。不支持 HTTP 发现 URL 或页面级 socket。

使用 [HTTP 认证变量](http.md#authentication)，将敏感端点保存在 `CDP_URL` 中，可选的授权值保存在 `CDP_AUTHORIZATION` 中，然后接入：

```sh
jq -n --arg url "$CDP_URL" --arg auth "${CDP_AUTHORIZATION:-}" \
  '{name: "Existing browser", cdp_url: $url, headers: (if $auth == "" then {} else {Authorization: $auth} end)}' | \
  curl --fail-with-body -sS "$A13N_URL/api/v1/browsers/attach" \
    -H "Authorization: Bearer $A13N_API_KEY" \
    -H 'Content-Type: application/json' --data-binary @-
```

[绑定返回的浏览器 ID](#reserve-and-bind-a-browser)。如果 agent 需要访问已有页面，使用 `tab_access: "global"`。首次浏览器调用会连接端点。需要提前测试连接时，调用 `POST /api/v1/browsers/{id}/prepare`；这不会创建新浏览器。

Service 加密保存端点和请求头，普通读取不会返回它们。更换端点时需新建接入资源并重新绑定。释放接入资源会移除 Service 访问权限和保存的连接密钥；外部进程、标签页及登录状态保持不变。

接入时和每次连接时都会检查目标地址，包括 run 的 `allowed_hosts` 限制。普通 WS 需要在 `providers.http_origins` 中显式允许对应 HTTP origin；连接拒绝重定向。注意，loopback 地址指向 worker 所在的网络环境。

### 选择准备和保留策略

准备策略决定**何时让浏览器就绪**。保留策略决定**浏览器空闲后如何处理**。

#### 准备时机

绑定默认使用 `preparation: "first_use"`。选择 `run_start` 可在开始执行时准备浏览器。需要显式准备时，在创建请求中设置 `prepare: true`，或调用 `POST /api/v1/browsers/{id}/prepare`。

读取浏览器元数据不会启动浏览器。`reserved` 表示尚未准备，`creating` 表示准备正在进行或结果尚未确定，`ready` 表示浏览器已可用。

#### 保留策略

| 来源                                     | 默认空闲处理                        | 处理后的 profile |
| ---------------------------------------- | ----------------------------------- | ---------------- |
| Docker / Native                          | 1800 秒后停止；保留至显式释放       | 保留             |
| Browserbase / Kernel / Browser Use Cloud | 1800 秒后释放                       | 随供应商会话删除 |
| 外部 CDP                                 | 保留 Service 访问权限，直到显式释放 | 由外部管理       |

Docker 和 Native 可分别设置 `stop_after_seconds` 与 `delete_after_seconds`。两者都接受 60–604800 秒或 `null`，从最后一次获准使用开始计时。停止不会重置删除计时；两项操作同时到期时，优先删除。

`idle_seconds` 则选择空闲后永久释放。云浏览器默认值为 1800，外部 CDP 默认为 `null`。不要将它与停止/删除设置混用。即使 Service 认为云浏览器仍在使用，供应商超时也可能终止会话。

使用该浏览器的已接受、运行中或等待中的 run、尚未结束的调用、人工控制和待完成的归还义务，都会阻止自动停止或空闲释放。

#### 停止与释放

调用 `POST /api/v1/browsers/{id}/stop` 或 `POST /api/v1/browsers/{id}/release`，并在 `If-Match` 中携带当前浏览器 ETag。

- **停止**保留 Docker 或 Native 浏览器的 ID、绑定和 profile。下次获准使用或调用 `prepare` 时重新启动。Runtime 重启会推进 generation，并清除旧引用和标签页归属；恢复的标签页不会按 URL 或标题重新分配。
- **释放**请求终结资源：Service 删除托管浏览器的 runtime 和 profile，或断开与外部浏览器的连接。仍有活跃使用、无法安全释放时，Service 会拒绝请求。托管浏览器的清理可能一直保持 `deleting`，直到供应商确认会话已终止；释放请求不保证立即完成。

`lost` 或 `deleted` 的浏览器无法重新启动。向 thread 提交更多工作前，先解除绑定或绑定替代浏览器。

### 选择标签页访问范围

使用 `thread` 访问该 thread 创建的标签页及其弹出页。使用 `global` 访问浏览器的完整标签页清单，包括已有页面和其他 thread 的标签页。两种模式共享 cookie 和登录状态；需要隔离账号时，请使用不同浏览器。

每个 thread 独立选择标签页。所选标签页关闭后，agent 必须选择或创建另一个标签页。重连到同一个活跃浏览器时会恢复精确的标签页归属和选择，但 agent 必须重新观察以获取新引用。

### 传输文件

Docker 和 Native 支持 `browser_files`。上传时，`paths` 使用 `asset:<asset_id>` 引用 run 有权读取的工作空间 asset。主机路径和挂载环境路径不是浏览器上传路径。

已完成且受跟踪的下载通过 run 的 `write` 权限发布为工作空间 asset。重复查看返回同一发布结果。浏览器文件系统与挂载环境保持独立。

### 接管并归还控制权

Docker 和 Native 的接管流程可直接在 Console 中完成。接入应用可使用以下 HTTP 调用；这些调用不进入 MCP 发现。

1. 携带当前浏览器 `If-Match` 和 `{"thread_id": "<bound-thread-id>", "lease_seconds": 300}`，调用 `POST /api/v1/browsers/{id}/takeover`。
2. 等待 `control: "human"`。处于 `draining` 时，Service 阻止新的浏览器调用，并等待已获准的工作完成。
3. 以控制方身份调用 `POST /api/v1/browsers/{id}/viewer`，打开返回的 viewer。
4. 在 `control_expires_at` 前，向 `POST /api/v1/browsers/{id}/renew` 发送当前 `generation`、`episode` 和可选的 `lease_seconds`。续期无需 `If-Match`，在 draining 阶段也可进行。
5. 完成后，携带新的浏览器 `If-Match` 调用 `POST /api/v1/browsers/{id}/handback`。等待 `control: "automation"` 后再使用浏览器工具。

人工控制会阻止 agent 对该浏览器的全部浏览器工具调用，包括读取和截图。其他工具及浏览器仍然可用。确认归还后，Service 会通知实际发生过受阻调用的 thread；不会重放其操作，也不会自动恢复失败、取消或等待审批的工作。停止或归档 thread 会抑制其待处理通知。

#### 控制租约

默认租期为 300 秒，允许范围为 60–3600 秒。部署可在 `service.toml` 中调整：

```toml
[control]
browser_control_min_seconds = 60
browser_control_max_seconds = 3600
browser_control_seconds = 300
```

三项值必须都是正整数，并满足 `min <= default <= max`。接管和续期使用同一策略。省略 `lease_seconds` 时使用配置的默认值；超出范围的请求会被拒绝。

租约从接管开始阻止新浏览器调用时计时，包含 draining 阶段。续期不会缩短截止时间，且只有当前仍有权限的控制方才能续期。打开 viewer 本身不会续期，应用自行判断何时发送续期请求。租约到期会触发归还；已到期或正在 returning 的控制周期必须先完成归还，才能重新接管。

#### Viewer 接入

将 viewer URL、密码和 token 视为敏感信息，并遵守 `Cache-Control: no-store`。Viewer 可能早于控制租约到期；同时遵守两个期限，并在需要时重新获取 viewer。

对于 `protocol: "vnc"`，使用返回的同源 WebSocket URL 和密码，并提供 `binary` 与 `a13n.browser.<token>` 子协议。对于 `protocol: "iframe"`，使用返回的 URL。自托管 viewer 即使在 Service 离线时，也会执行到期和撤销。

### 故障排查

选择恢复操作前，先查看浏览器的 `status`、`control`、`expires_at` 和 `failure`。

- **云端创建结果未知。** 只有确认原请求未发送，或已被明确拒绝且未创建会话时，Service 才会重试创建。否则，在创建替代浏览器前先检查供应商账号：原会话可能仍在运行并产生费用。未收到会话 ID 时，Kernel 无法自动定位或释放会话。Browserbase 和 Browser Use Cloud 可按精确分配 metadata 查找，但没有匹配结果时，清理仍无法确认。
- **外部连接失败。** `browser_connection_failed` 本身不会将浏览器标记为丢失。重连同一实例，再重新观察。网关若静默替换浏览器，就无法保持连续性。失败或结果未知的操作不会自动重放。
- **接管停留在 `draining`。** 此前获准的工作尚未结束，超时不能证明操作已完成。在能够确认尚未发生任何 viewer 授权、且活跃工作允许的情况下，可通过归还取消接管；取消不会宣告浏览器可用，也不会清除结果未知的调用。
- **归还停留在 `returning`。** Viewer 撤销或标签页同步尚未完成，因此浏览器工具继续被阻止。Provider 禁用、控制方失权或 viewer 到期也可能触发归还。Run 和活跃操作结束后，具有工作空间写入权限的用户可改为释放托管浏览器。终止失败时保持 `deleting`，可重试释放。
- **某次操作结果持续未知。** Run 和活跃操作结束后，可通过释放终止处于自动化控制的浏览器资源。这不会确定旧操作的结果，也不会撤销外部影响。已释放的浏览器 ID 不可复用。

生命周期操作的取消预算默认为 120 秒，由 `browsers.operation_seconds` 设置。清理和阻塞的 provider I/O 可能超过该预算，因此它不是硬响应时限。

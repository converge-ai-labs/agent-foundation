---
title: 多租户身份认证与 RBAC 权限管理
description: 管理 Service 组织与工作空间、成员邀请、服务账号（Service Accounts）、RBAC 角色（runner/builder/admin）及工作空间 API key。
---

授予访问权限时，先选择工作空间和角色。通过 Console 邀请用户；为应用创建服务账号和 API 密钥。本页带你完成这两条路径，再检查和移除访问权限。

用户或服务账号称为**主体**。**授权** 为主体分配组织或工作空间中的角色。登录会话或 API 密钥用于认证主体；主体当前的授权决定请求可以执行哪些操作。

```mermaid
flowchart TB
    User["人员：用户账号"] --> Login["登录会话"]
    User --> Key["工作空间 API 密钥"]
    App["应用：服务账号"] --> Key
    Login --> Grants["当前授权"]
    Key --> Grants
    Grants --> Workspace["工作空间权限"]

    class User,App app
    class Login,Key,Grants,Workspace a13n
```

## 组织与工作空间

先确定用户或应用将在哪个工作空间开展工作。每个 agent、会话、provider、模型、连接和环境都属于一个工作空间。组织管理这些工作空间及其成员。

[初始化](get-started.md#register-your-administrator-account)会创建首个组织、工作空间和管理员。要添加工作空间，请以组织管理员身份登录，打开 **组织设置 → 工作区**。对应 API 是 `POST /api/v1/organizations/{organization_id}/workspaces`，请求体包含 `name` 字段。API 不支持创建其他组织。

管理访问权限时使用工作空间 ID。普通资源请求中，登录会话通过 `X-Workspace-ID` 选择工作空间；API 密钥已绑定工作空间。参阅[工作空间选择](http.md#workspace)。

管理员可以重命名组织和工作空间，并设置 PNG、JPEG 或 WebP 图标。只有工作结束后才应归档工作空间：`POST /api/v1/workspaces/{workspace_id}/archive` 是永久操作，需要组织管理员权限。归档会撤销待处理邀请，资源仍可读取。修改请求返回 `disabled`，移除访问权限的操作仍然可用。

## 角色

根据接收者要完成的工作选择角色：

| 角色      | 权限                    | 适用任务                           |
| --------- | ----------------------- | ---------------------------------- |
| `viewer`  | read                    | 查看配置、对话和结果。             |
| `runner`  | read, run               | 使用已有 agent 并引导运行。        |
| `builder` | read, run, write        | 创建和修改 agent 与资源。          |
| `admin`   | read, run, write, admin | 管理访问权限、工作空间设置和审计。 |

工作空间角色授予该工作空间的访问权限。用户需要在组织内的**每个工作空间**拥有该角色时，再授予组织角色。工作空间授权还允许读取所属组织。

组织授权与工作空间授权的权限会合并。例如，工作空间的 `viewer` 授权不会降低组织 `builder` 授权带来的权限。要降低权限，必须修改提供较高权限的授权。读取工作空间的 `permissions` 列表，可以看到当前请求凭据实际拥有的操作权限。

## 邀请

邀请团队成员时，以目标范围的管理员身份登录：

1. 在工作空间或组织设置中打开 **邀请**。
2. 输入对方邮箱并选择角色。只需要访问一个工作空间时，发送工作空间邀请即可。
3. 发送邀请。[配置 SMTP](configuration.md#identity-and-mail) 后，Service 会通过邮件发送链接。未配置 SMTP 时，复制一次性显示的邀请链接并分享给接收者。
4. 请接收者打开链接。新用户设置名称和密码；已有用户使用自己的密码确认。接受后，用户会自动登录并获得邀请中的角色。
5. 请用户打开工作空间，检查所需操作是否可用。

对应 API 是 `POST /api/v1/workspaces/{workspace_id}/invitations`，请求体为 `{email, role}`；组织邀请使用对应的组织路径。邮件发送的响应为 `delivery: "queued"`；手动分享的响应为 `delivery: "manual"`，并一次性提供 `invitation_url`。发送和重发需要登录会话；API 密钥可以撤销邀请，但不能发送邀请。

接受邀请会替换该范围内已有的授权。邀请在 `auth.invitation_seconds` 后过期，默认七天。重发会替换链接并重新计算有效期。不再需要授权时，撤销邀请。通过 API 重发或撤销时，需要邀请当前的 [If-Match 值](http.md#concurrency-control)。

接受邀请返回 `409 conflict` 时，检查邀请是否已使用、撤销或过期，以及发送者是否失去管理权限。后台会清理过期和已撤销的邀请，之后链接返回 `404`。同一邮箱在每个范围内最多有一条待处理邀请。新账号通过邀请或初始化创建，没有公开注册入口。

## API 密钥

以你的身份执行的脚本使用个人密钥；应用使用[服务账号密钥](#service-accounts)。每个密钥限定在一个工作空间内。每次请求都读取主体当前的授权，因此角色变更也会影响已有密钥。即使密钥属于组织管理员，该密钥在组织本身也最多只有 `read` 权限。

创建个人密钥：

1. 登录后打开侧边栏的 **API 密钥**。
2. 选择你至少拥有 `read` 权限的工作空间，填写密钥名称，并按需设置过期时间。
3. 关闭结果前复制密钥值。API 在 `secret` 中一次性返回密钥值，在 `key` 中返回元数据；Service 只存储其哈希。
4. 将 `A13N_URL` 设为 Service 基础 URL，`A13N_API_KEY` 设为密钥值，`WORKSPACE_ID` 设为该密钥的工作空间 ID。先验证工作空间权限，再读取 agent：

```sh
curl "$A13N_URL/api/v1/workspaces/$WORKSPACE_ID" \
  -H "Authorization: Bearer $A13N_API_KEY"
curl "$A13N_URL/api/v1/agents" \
  -H "Authorization: Bearer $A13N_API_KEY"
```

工作空间响应中的 `permissions` 应与预期角色一致。agent 列表确认资源可访问；新工作空间的列表可能为空。访问权限正确后，继续[应用集成](connect-application.md)。

签发 API 为 `POST /api/v1/users/me/keys`，请求体是 `{name, workspace_id, expires_at?}`。签发任何密钥都需要登录会话，API 密钥不能创建更多密钥。在 **API 密钥** 中列出或撤销自己的密钥，或者使用 `GET /api/v1/users/me/keys` 和 `DELETE /api/v1/users/me/keys/{key_id}`。工作空间管理员通过 **成员密钥** 或 `/api/v1/workspaces/{workspace_id}/keys` 管理该工作空间的所有密钥。

通过 API 撤销密钥时，需要根据其 `id` 和 `version` 得到当前 [If-Match 值](http.md#concurrency-control)。轮换密钥时，先签发新密钥并验证应用，再撤销旧密钥。`last_used_at` 可帮助检查使用情况，最多每分钟更新一次。

## 服务账号

应用需要独立于个人密钥的访问权限时，创建服务账号。以工作空间管理员身份登录，打开 **工作区设置 → 服务账号**：

1. 创建账号并填写明确的名称。使用已有 agent 时选择 `runner`；应用还需要创建资源时选择 `builder`。
2. 打开该账号并创建 API 密钥。从一次性结果中复制密钥值。
3. 将密钥提供给应用，并运行上面的两条[验证请求](#api-keys)。开始工作前检查实际权限。

通过 API 自动化时，调用 `POST /api/v1/workspaces/{workspace_id}/service-accounts`，请求体如下：

```json
{"name": "Support application", "description": "Runs the support agent", "role": "runner"}
```

然后调用 `POST …/service-accounts/{account_id}/keys`，传入 `{name, expires_at?}` 签发密钥。签发需要管理员的登录会话。账号在整个生命周期内属于该工作空间，也只能获得该工作空间中的角色。

按需要选择停止访问的方式：

- **暂停：** 调用 `PATCH …/service-accounts/{account_id}`，传入 `{"status": "disabled"}`。密钥仍保留，但不能认证。将 `status` 改回 `active` 后，未过期且未撤销的密钥恢复可用。
- **停用：** 调用 `DELETE …/service-accounts/{account_id}`，移除授权、撤销密钥并禁用账号。账号记录保留供审计。恢复时需要启用状态、重新授予角色并签发新密钥。

更新和删除都需要账号当前的 [If-Match 值](http.md#concurrency-control)。先读取账号以获取 `ETag`。更新还可修改名称、描述或角色。在已归档的工作空间中，禁用和停用仍然可用，恢复访问则不可用。

## 成员与授权

通过授权调整已有成员的访问权限；通过[邀请](#invitations)引入新成员。组织管理员可以调用 `GET /api/v1/organizations/{organization_id}/members` 查找成员，并按 `kind=user` 或 `kind=service_account` 筛选。

工作空间授权使用 `/api/v1/workspaces/{workspace_id}/grants`：

1. 用 `POST` 和 `{principal_id, role}` 创建授权。每个主体在同一范围内最多有一条授权。
2. 用 `PATCH …/grants/{grant_id}` 和 `{role}` 修改角色。角色变更会替换授权；后续操作使用响应返回的新 ID。
3. 用 `DELETE …/grants/{grant_id}` 移除访问权限。同时检查组织授权：组织授权提供的权限在该工作空间中仍然生效。

组织授权使用对应的 `/api/v1/organizations/{organization_id}/grants` 路径。授权路由不接受 `If-Match`。最后一位仍活跃的手动授权组织管理员不能被移除、降级或禁用；这类请求返回 `409 conflict`，原因为 `last_organization_admin`。移除服务账号的最后一条授权会停用该账号并撤销其密钥。

## 登录与登录会话

Console 负责处理登录 cookie 和 CSRF token。使用邮箱和密码登录后，在 **个人设置 → 登录会话** 中检查活跃登录，并撤销不再使用的登录会话。退出登录会结束当前登录会话。

自定义客户端调用 `POST /api/v1/auth/login`，传入 `{email, password}`；Service 设置登录 cookie 并返回 CSRF token。使用 cookie 认证的状态修改请求需要发送 `X-CSRF-Token`。浏览器源必须匹配 Service 的公共源；回环部署接受 `localhost` 和 `127.0.0.1`。参阅 [HTTP 认证](http.md#authentication)。

HTTPS cookie 名为 `__Host-a13n_session`，带有 `Secure`、`HttpOnly` 和 `SameSite=Strict`。明文 HTTP 使用不带 `Secure` 的 `a13n_session`。登录会话在闲置 `auth.session_seconds` 后过期，默认 12 小时；登录尝试按客户端地址和邮箱限流。

`GET /api/v1/auth/session` 读取当前登录会话和 CSRF token；`POST /api/v1/auth/logout` 结束当前登录会话，两者都需要会话 cookie。`GET /api/v1/users/me/login-sessions` 列出你的登录会话供管理。这些操作拒绝 API 密钥，返回 `403 forbidden`。

### 配置 OIDC 登录

1. 先升级所有 Service API 副本，再启用 OIDC。初始化本地管理员，并复制新用户要加入的工作空间 ID。
2. 在身份提供方注册机密 Web OIDC 客户端。将回调地址设为 `{server.public_url}/api/v1/auth/oidc/{provider_id}/callback`（省略 `server.public_url` 末尾的斜杠）。选用 `team` 等提供方 ID，并在配置与回调路径中使用同一 ID。除本机开发环境外，请使用 HTTPS。
3. 在部署的 Secret 中将 `A13N_AUTH__OIDC` 设为下面的 JSON，然后重启 Service API 副本。把工作空间 ID、签发方、客户端 ID 和客户端密钥替换为实际值。

```json
{
  "defaults": [{"workspace_id": "ws_replace_with_your_workspace_id", "role": "runner"}],
  "providers": {
    "team": {
      "label": "Team Identity",
      "issuer": "https://identity.example.com/realms/team",
      "client_id": "a13n-console",
      "client_secret": "replace-at-deploy-time"
    }
  }
}
```

`A13N_AUTH__OIDC` 会替换 `service.toml` 中整段 `[auth.oidc]`。请在 JSON 中填入全部提供方及所有非默认的 OIDC 设置。`[auth]` 中的其他字段仍由各自的环境变量或 TOML 提供。如果改用 TOML 配置，则把工作空间 ID 写在 `[auth.oidc]`，把提供方配置写在 `[auth.oidc.providers.team]`。

每增加一个 `[auth.oidc.providers.<id>]` 表，登录页就会多一个按钮。提供方须支持 Discovery、带 PKCE S256 的授权码流程、签名 ID token（`RS256` 或 `ES256`）以及 `openid profile email` 权限范围。默认使用 HTTP Basic；如果提供方要求 `client_secret_post`，请设置 `token_endpoint_auth_method = "client_secret_post"`。签发方为 `https://accounts.google.com` 时，Console 显示内置的 Google 图标；签发方为 `https://login.microsoftonline.com/<租户 UUID>/v2.0` 时显示 Microsoft 图标；其他签发方显示盾牌。如果受信任的身份代理始终将用户转到同一家提供方，可以将 `brand` 设为 `"google"`、`"microsoft"` 或 `"generic"` 来指定图标。请为各提供方设置易于辨认、互不重复的标签。

Service 接受两种 `aud` 格式：配置的客户端 ID 字符串，或只包含该 ID 的数组。如果 ID token 提供 `azp`，它也必须匹配同一个 ID。ID token 的 `iat`、`nbf` 可比当前时间晚最多 30 秒，`exp` 也有 30 秒的到期宽限。请允许 Service 出站访问签发方的 Discovery 端点，以及 Discovery 返回的 token 和 JWKS 端点。除本机开发外，这些请求必须使用 HTTPS，不跟随重定向，只接受 `Content-Encoding: identity` 或不带内容编码的响应。浏览器需要访问 Discovery 返回的授权端点。请将反向代理访问日志配置为省略回调 URL 的查询字符串。

OIDC 发起请求有独立的 Redis 限流预算：同一客户端地址访问各提供方共用 `auth.oidc` 下的 `start_limit`（默认 30 次）和 `start_window_seconds`（默认 60 秒）。共享一个 NAT 地址的客户端共用预算；反向代理部署请配置 `server.trusted_proxies`，按可信代理转发的客户端地址计数。Redis 不可用时，Service 会跳过限流检查，但仍须先保存一次性流程才能发起浏览器登录。

凡是身份提供方允许向该客户端授权的用户，都可以登录。请先在身份提供方设置应用访问规则，再发布登录按钮。对于 Google Workspace，请在组织拥有的项目中创建 Web 客户端，并将 **Google Auth Platform → Audience → Internal** 设为仅本组织可用（参见 [Google 的受众指南](https://support.google.com/cloud/answer/15549945)）。External 应用可以允许组织外用户登录；Service 不会按邮箱域名限制准入。

在 Microsoft Entra 员工租户中，`email` 是提供方报告的邮箱，`xms_edov` 验证的是域名所有者，并不验证用户邮箱。[可选声明参考](https://learn.microsoft.com/en-us/entra/identity-platform/optional-claims-reference)没有把 `email_verified` 列为 Entra 的标准可选声明，另见 [ID token 声明参考](https://learn.microsoft.com/en-us/entra/identity-platform/id-token-claims-reference)。如果身份提供方或受信任的上游支持标准 `email_verified` 声明，应仅在实际验证用户邮箱后签发布尔值 `true`。没有该声明时，Service 仍使用签名 token 中的主体标识和显示名，邮箱留空。

如需在 Entra 中限制应用访问，请[启用分配要求并分配指定用户或组](https://learn.microsoft.com/en-us/entra/identity-platform/howto-restrict-your-app-to-a-set-of-users)。Entra 全局管理员不受此分配要求限制。

未设置规则的提供方会在首次登录时创建独立的 Service 用户，并写入 `defaults` 指定的普通授权。身份提供方验证过的邮箱仅用于显示，即使某个密码账号也使用同一邮箱。请通过[成员与授权](identity.md#members-and-grants)管理权限。以后登录时，Service 会保留现有授权，不会恢复已移除的角色。

### 按 ID token 声明同步角色

在提供方配置中添加 `role_rules`，按组织或工作区同步角色。每个授权目标填写 `organization_id` 或 `workspace_id`（二选一）以及已注册的角色。以下示例给 `console-admins` 组组织管理员权限，其他人则获得默认工作区的 Runner 权限。请选择由身份提供方控制、用户不能自行修改的声明。

```json
{
  "defaults": [{"workspace_id": "ws_replace_with_your_workspace_id", "role": "runner"}],
  "providers": {
    "team": {
      "label": "Team Identity",
      "issuer": "https://identity.example.com/realms/team",
      "client_id": "a13n-console",
      "client_secret": "replace-at-deploy-time",
      "extra_scopes": ["groups"],
      "role_rules": [
        {"match": {"groups": "console-admins"}, "organization_id": "org_replace_with_your_org_id", "role": "admin"}
      ]
    }
  }
}
```

一条规则里的所有声明都要匹配；单个声明可配置一个字符串或多个候选字符串。Service 优先查找同名顶层键，找不到才按点分路径查找嵌套声明，例如 `realm_access.roles`。比较区分大小写；token 中的值须是字符串或全由字符串组成的数组。缺失、格式错误或标明分布式的声明不匹配。请用 `extra_scopes` 请求所需 scope，并在提供方配置中把声明写入 ID token。

启用规则后，Service 每次成功登录都会同步该提供方的授权：有匹配时只使用匹配规则；无匹配时使用默认授权。提供方省略 `defaults` 会继承全局值，设置 `defaults: []` 则禁用回退；没有规则命中、也没有可回退的授权时，拒绝登录并保留上一次同步结果。每个提供方至少要有可用默认授权或一条规则。配置的组织或工作区不存在时，本次登录失败且原授权不变。首次登录不能授予已归档工作区的权限。

在组织或工作区的**成员 → 更改角色**中，选择**手动设置角色**可以覆盖当前范围的单点登录默认角色。选择**使用单点登录默认角色 → 恢复单点登录默认角色**，可清除手动覆盖，让最近同步的授权立即生效。没有单点登录默认角色的普通授权仍可直接更改或移除。请保留至少一名活跃的手动授权组织管理员，以便恢复访问。

已有浏览器会话、用户 API 密钥和 Worker 在下次鉴权时使用最新角色。服务账号密钥继续使用其自身账号的授权。去掉提供方的全部规则并保留可用默认授权，用户再次登录后会清除该提供方以前同步的授权；直接删除提供方不会清除现有授权。

### 切换到仅 OIDC 登录

1. 让预定的管理员通过身份提供方登录一次。使用本地管理员会话打开 **组织 → 成员 → 添加成员**，选中新账户并授予**管理员**角色。选择列表也包含只有工作空间角色的成员。修改或撤销现有角色时，使用该成员所在行的操作菜单。
2. 在独立的浏览器会话中通过身份提供方登录，确认新管理员可以打开 **组织 → 成员** 并管理成员。
3. 将 Service API 副本的环境变量设为 `A13N_AUTH__PASSWORD_ENABLED=false`，然后重启各副本。这会覆盖 `service.toml` 中的 `password_enabled`。现有密码会话失效，密码登录、创建和重发邀请以及密码找回停用。管理员仍可查看和撤销邀请，API 密钥继续有效。

OIDC 会话在 `auth.oidc.session_seconds` 后到期（默认 12 小时），操作不会延长有效期。移除某人的身份提供方访问权时，如需立即撤销本地访问，还要使用 `--principal-id` 禁用对应的 Service 用户。需要恢复密码登录时，将环境变量设为 `A13N_AUTH__PASSWORD_ENABLED=true`，然后重启 API 副本。原有密码账户仍然保留。

## 你的账号

修改个人资料、密码和邮箱，管理登录会话以及禁用账号，都使用登录会话。API 密钥可以读取其主体的个人资料，但不能执行这些账号修改。

| 任务           | 操作结果                                                                                                                                                |
| -------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 修改个人资料   | 更新名称，或上传 PNG、JPEG、WebP 头像。                                                                                                                 |
| 修改密码       | 提供当前密码。新密码至少八个字符；其他登录会话会结束。                                                                                                  |
| 重置遗忘的密码 | 使用登录页的密码重置流程。SMTP 发送有效期为 `auth.link_seconds` 的一次性链接；重置后结束所有登录会话。                                                  |
| 修改邮箱       | 提供新地址和当前密码。SMTP 向新地址发送确认链接；打开后修改生效，并结束所有登录会话。                                                                   |
| 禁用账号       | 调用 `POST /api/v1/users/me/disable` 并提供当前密码，会停止认证，撤销尚未使用的密码重置和邮箱修改链接。授权和密钥仍保留。只有运维人员可以重新启用账号。 |

当前密码检查共享按客户端地址和账号计算的限流额度。被禁用用户启动的运行会在下次权限检查时停止。

## 运维账号控制

具有部署访问权限的运维人员可以禁用或重新启用用户，包括自行禁用账号的用户。使用部署配置运行所需命令：

```sh
a13n-service --config service.toml user disable --email person@example.com
a13n-service --config service.toml user enable --email person@example.com
```

这是两种供选择的操作。禁用会停止用户凭据；已接收的运行会在下次权限刷新时停止。启用后，保留的授权及未过期、未撤销的密钥恢复可用。这些命令不需要组织或工作空间角色。审计事件没有 actor，并记录 `details.authority = "operator"`。

## 审计

修改访问权限后，通过审计记录检查执行者、目标和结果。安全与访问权限变更会产生不可变事件，包含 `actor_id`、`action`、`outcome`（`ok`、`denied` 或 `failed`）及有界详情。被拒绝的管理修改显示为 `denied`。

| 范围               | 读取入口                                                                        | 所需权限       |
| ------------------ | ------------------------------------------------------------------------------- | -------------- |
| 组织及其工作空间   | **组织设置 → 审计**、`GET /api/v1/organizations/{organization_id}/audit-events` | 组织管理员     |
| 单个工作空间       | **工作区设置 → 审计**、`GET /api/v1/workspaces/{workspace_id}/audit-events`     | 工作空间管理员 |
| 你的操作和账号事件 | `GET /api/v1/users/me/audit-events`                                             | 你的登录会话   |

事件按从新到旧排列。使用返回的游标继续读取较早的事件。

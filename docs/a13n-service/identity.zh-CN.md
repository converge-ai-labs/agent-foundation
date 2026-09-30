---
title: 身份与访问
description: 组织、工作空间、角色、授权、登录会话、API 密钥和服务账号。
---

每个请求都以一个**主体** 的身份执行：通过邮箱和密码登录的用户，或供应用使用的服务账号。主体通过组织或工作空间中的**授权** 获得**角色**，并使用登录会话或 API 密钥认证。

## 组织与工作空间

**组织** 是管理边界，包含成员和工作空间。**工作空间** 是工作边界：agent、会话、provider、模型、连接、环境和其他资源都只属于一个工作空间。

新 Service 可在 Console 中或通过 `bootstrap` 命令完成[初始化](get-started.md#create-the-first-administrator)，创建首个组织、工作空间和管理员。没有创建其他组织的 API。组织管理员可以在 Console 的 **Organization settings → Workspaces** 中创建工作空间，或调用 `POST /api/v1/organizations/{organization_id}/workspaces`，请求体为 `{name}`。

管理员可以重命名组织或工作空间，并设置图标（PNG、JPEG 或 WebP）。组织和工作空间通过 ID 标识：工作空间管理路径指定工作空间 ID，其他请求则作用于凭据选定的工作空间（参阅 [HTTP 约定](http.md#workspace)）。

**归档** 工作空间（`POST /api/v1/workspaces/{workspace_id}/archive`，仅组织管理员可用）是永久操作，并撤销待处理邀请。归档后仍可读取工作空间，但所有修改均以 `disabled` 拒绝，只有退出管理操作例外：删除授权、停用或禁用服务账号、撤销 API 密钥。

## 角色

| 角色      | 操作权限                | 典型用途                               |
| --------- | ----------------------- | -------------------------------------- |
| `viewer`  | read                    | 查看配置、对话和结果。                 |
| `runner`  | read, run               | 使用已有 agent 开始和引导对话。        |
| `builder` | read, run, write        | 创建和修改 agent 与资源。              |
| `admin`   | read, run, write, admin | 管理成员、邀请、密钥、服务账号和审计。 |

一次授权在一个范围内授予角色：

- 组织授权适用于该组织及其所有工作空间。
- 工作空间授权适用于该工作空间。在组织层面只提供 `read`，以便工作空间成员查看所属组织。

主体的权限是其在组织中所有授权的并集。资源视图包含 `permissions` 列表，显示你当前在该资源上拥有的操作权限。

## 成员与授权

组织成员包括在组织中拥有授权的主体，以及组织各工作空间的服务账号。组织管理员通过 `GET /api/v1/organizations/{organization_id}/members` 列出成员（可按 `kind=user` 或 `kind=service_account` 筛选）。

管理员通过 `/api/v1/organizations/{organization_id}/grants` 和 `/api/v1/workspaces/{workspace_id}/grants` 管理各范围的授权：

- `POST` 传入 `{principal_id, role}`，为已有成员授予角色。每个主体在同一范围内最多拥有一次授权。
- `PATCH …/grants/{grant_id}` 传入 `{role}`，替换授权：响应包含新的授权 ID，旧 ID 不再有效。
- `DELETE …/grants/{grant_id}` 移除授权。没有任何授权的服务账号会被停用。

最后一位活跃的组织管理员不能被移除、降级或禁用（`409 conflict`，原因为 `last_organization_admin`）。授权路由不接受 `If-Match`。

## 邀请

邀请尚未加入组织的用户，或在新范围内为已有用户授予角色。在 Console 中使用工作空间或组织设置中的 **Invitations**；通过 API 调用 `POST /api/v1/workspaces/{workspace_id}/invitations`（或对应的组织端点），传入 `{email, role}`。

- 发送或重发邀请需要登录会话；API 密钥不能执行。
- [配置 SMTP](configuration.md#identity-and-mail)后，邀请链接通过邮件发送（`delivery: "queued"`）。未配置时，响应通过 `invitation_url` 一次性返回链接（`delivery: "manual"`），需自行分享。
- 链接打开 Console 的邀请页面。新用户设置名称和密码；已有用户使用自己的密码确认。接受后自动登录，并在该范围获得被邀请的角色，替换原有授权。
- 邀请在 `auth.invitation_seconds` 后过期（默认七天）。**Resend** 生成新链接并重新计时；**Revoke** 撤销邀请（API 密钥可以撤销）。同一地址和范围只能有一个待处理邀请。
- 邀请已接受、撤销或过期，或者邀请人不再有该范围的管理权限时，接受操作返回 `409 conflict`。过期和已撤销的邀请由后台清理，之后链接返回 `404`。

没有自助注册：账号只能通过初始化或接受邀请创建。

## 登录与登录会话

Console 使用 `POST /api/v1/auth/login` 和 `{email, password}` 登录。Service 设置 `__Host-a13n_session` cookie（`Secure`、`HttpOnly`、`SameSite=Strict`），并返回 CSRF token；公共 URL 使用明文 HTTP 时，cookie 名为 `a13n_session`，且不设置 `Secure`。会话从最后一次使用起保留 `auth.session_seconds`（默认 12 小时）。登录尝试按客户端地址和邮箱限流。

通过 cookie 认证的状态修改请求必须在 `X-CSRF-Token` 中发送 CSRF token；浏览器 `Origin` 必须等于 Service 的公共源（回环公共 URL 可以使用 `localhost` 或 `127.0.0.1`）。参阅 [HTTP 约定](http.md#authentication)。

在 **Personal settings → Login sessions** 或 `GET /api/v1/users/me/login-sessions` 中查看活跃会话，并撤销任一会话。`POST /api/v1/auth/logout` 结束当前会话，`GET /api/v1/auth/session` 重新读取当前会话及 CSRF token；两者均需要会话 cookie，使用 API 密钥会返回 `403 forbidden`。

## 你的账号

账号操作需要登录会话；API 密钥不能执行。

- **个人资料：** 修改名称和头像（PNG、JPEG 或 WebP）。
- **密码：** 修改密码（`POST /api/v1/users/me/password`，提供当前密码）会结束其他登录会话。密码至少 12 个字符。
- **重置密码：** 登录页的 **Forgot your password?** 会发送有效期为 `auth.link_seconds` 的一次性链接。重置会结束所有登录会话。此功能需要 SMTP。
- **修改邮箱：** 提交新地址和当前密码；Service 向新地址发送确认链接，打开后修改生效。确认会结束所有登录会话。此功能需要 SMTP。
- **禁用：** `POST /api/v1/users/me/disable` 提供当前密码后禁用账号，结束其他登录会话，并撤销未使用的密码重置和邮箱修改链接。授权和密钥保留，但不能再以你的身份认证；由你启动的运行会在下次权限检查时停止。只有运维人员可以重新启用。

检查当前密码的操作共享按客户端地址和账号计算的限流额度。

## API 密钥

API 密钥以其主体身份认证，始终**限定在一个工作空间内**。密钥本身不携带权限：每个请求读取主体当前的授权，并限制到该工作空间（组织层面仅有 `read`）。修改或移除授权会立即影响该主体的所有密钥。

在 Console 的 **Workspace settings → My API keys** 中创建个人密钥，或调用 `POST /api/v1/users/me/keys` 并传入 `{name, workspace_id, expires_at?}`。需要登录会话和该工作空间至少 `read` 权限；API 密钥不能创建其他密钥（`403 forbidden`）。响应只返回一次密钥值（`a13n_…`），Service 仅保存其哈希。将密钥作为 bearer token 发送；请求在该密钥所属工作空间中执行：

```sh
curl "$A13N_URL/api/v1/agents" -H "Authorization: Bearer $A13N_API_KEY"
```

在 **My API keys** 中列出和撤销自己的密钥（`GET /api/v1/users/me/keys`、`DELETE /api/v1/users/me/keys/{key_id}`）。工作空间管理员在 **Member keys** 中查看和撤销限定于其工作空间的全部密钥（`/api/v1/workspaces/{workspace_id}/keys`）。`last_used_at` 显示最近使用时间，最多每分钟更新一次。

## 服务账号

服务账号是应用身份，生命周期内始终属于一个工作空间，只能在该工作空间授予角色，并且只能通过 API 密钥认证。工作空间管理员在 **Workspace settings → Service accounts** 或 `/api/v1/workspaces/{workspace_id}/service-accounts` 中管理：

- `POST` 传入 `{name, description, role}`，创建账号和工作空间授权（默认 `runner`）。
- `POST …/service-accounts/{account_id}/keys` 传入 `{name, expires_at?}`，为已登录管理员签发密钥。请从一次性响应中复制密钥值。
- `PATCH` 修改名称、描述或角色，或将 `status` 设为 `disabled` 或 `active`。禁用账号保留密钥，但密钥不再能认证。
- `DELETE` 停用账号：移除授权、撤销密钥并禁用。记录保留用于历史审计。

## 运维账号控制

拥有部署访问权限的运维人员可以禁用或重新启用任何用户，此操作不受租户权限限制：

```sh
a13n-service --config service.toml user disable --email person@example.com
a13n-service --config service.toml user enable --email person@example.com
```

禁用会停止用户的所有凭据；已接收的运行在下次权限刷新时停止。启用会恢复账号原有的授权和密钥。两种操作均记录审计，actor 为空，且 `details.authority = "operator"`。

## 审计

访问权限和账号安全修改会记录为不可变的审计事件：执行者（`actor_id`）、`action`、目标、`outcome`（`ok`、`denied` 或 `failed`）以及有界的详情。被拒绝的管理修改记为 `denied`。

| 记录范围                   | 入口                                                                                          | 可访问者             |
| -------------------------- | --------------------------------------------------------------------------------------------- | -------------------- |
| 组织及其工作空间           | **Organization settings → Audit**、`GET /api/v1/organizations/{organization_id}/audit-events` | 组织管理员           |
| 单个工作空间               | **Workspace settings → Audit**、`GET /api/v1/workspaces/{workspace_id}/audit-events`          | 工作空间管理员       |
| 你自己的操作及账号相关事件 | `GET /api/v1/users/me/audit-events`                                                           | 使用登录会话的你本人 |

事件按从新到旧的顺序列出，使用游标分页。

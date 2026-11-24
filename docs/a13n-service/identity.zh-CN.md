---
title: 身份与访问控制
description: 管理多租户组织、工作区、成员邀请、服务账号、OIDC 单点登录、RBAC 角色（runner、builder、admin）与 API 密钥。
---

Service 通过浏览器登录会话或工作区作用域的 API 密钥认证**主体（Principal）**（人类用户与自动化服务账号），并在每次请求时校验该主体在组织与工作区上的实时**授权（Grant）**。

## 组织与工作区

- **组织（Organization）：** 在[初始化引导](get-started.md#register-your-administrator-account)时创建的顶层租户边界，统一归属成员与多个工作区。
- **工作区（Workspace）：** 隔离智能体、线程、环境、提供方、连接与 API 密钥。组织管理员可在 **Organization settings → Workspaces**（`POST /api/v1/organizations/{organization_id}/workspaces`）创建工作区，并通过 `POST /api/v1/workspaces/{workspace_id}/archive` 归档已停用工作区。登录会话通过 `X-Workspace-ID` 选择工作区；API 密钥天然绑定单一工作区。

## 角色

| 角色      | 有效权限动词                    | 适用场景                                 |
| --------- | ------------------------------- | ---------------------------------------- |
| `viewer`  | `read`                          | 查看配置、对话记录与运行结果             |
| `runner`  | `read`, `run`                   | 运行现有智能体、引导运行并使用环境       |
| `builder` | `read`, `run`, `write`          | 创建和修改智能体、模板与提供方           |
| `admin`   | `read`, `run`, `write`, `admin` | 管理成员授权、密钥、工作区设置与审计日志 |

组织级授权对该组织下**所有工作区**生效，并与工作区授权取并集。

## 邀请、成员与授权

### 邀请

管理员在 **Invitations**（`POST /api/v1/workspaces/{workspace_id}/invitations` 或组织级同路径，需登录会话）填入 `{email, role}` 发送邀请。[配置 SMTP](configuration.md#identity-and-mail) 后自动发送邮件，否则返回一次性 `invitation_url`。

### 成员与授权

通过 `GET /api/v1/organizations/{organization_id}/members` 查询成员，并对 `/api/v1/workspaces/{workspace_id}/grants`（或组织级 `/grants`）执行 `POST` / `PATCH` / `DELETE`（`{principal_id, role}`）管理已有主体的角色。

## API 密钥

在侧边栏 **API keys**（`POST /api/v1/users/me/keys`，传入 `{name, workspace_id, expires_at?}`，需登录会话）签发个人密钥，复制一次性返回的 `secret` 并通过 `Authorization: Bearer $A13N_API_KEY` 调用：

```sh
curl "$A13N_URL/api/v1/workspaces/$WORKSPACE_ID" -H "Authorization: Bearer $A13N_API_KEY"
```

携带 `If-Match` 调用 `DELETE /api/v1/users/me/keys/{key_id}`（工作区管理员可用 `/api/v1/workspaces/{workspace_id}/keys`）吊销密钥。

## 服务账号

在 **Workspace settings → Service accounts**（`POST /api/v1/workspaces/{workspace_id}/service-accounts`，如 `{"name": "Support app", "role": "runner"}`）为后端应用创建独立服务账号，并通过 `POST …/service-accounts/{account_id}/keys` 签发密钥。携带 `If-Match` 执行 `PATCH {"status": "disabled"}` 可暂停访问，`DELETE` 可永久停用并吊销其密钥。

## 登录会话与 OIDC 单点登录

- **登录会话：** 基于 Cookie 的浏览器状态修改请求（`/api/v1/auth/login`、`/session`、`/logout`）须携带 `X-CSRF-Token`。用户可在 **Personal settings** 管理活跃会话、密码、邮箱或停用个人账号。
- **OIDC 单点登录（`[auth.oidc]` / `A13N_AUTH__OIDC`）：** 在 IdP 注册回调地址 `{server.public_url}/api/v1/auth/oidc/{provider_id}/callback`（支持 PKCE S256、`RS256`/`ES256`、`openid profile email`），配置默认授权与可选的基于声明同步的 `role_rules`：

```json
{
  "defaults": [{"workspace_id": "ws_example", "role": "runner"}],
  "providers": {
    "team": {
      "label": "Team Identity",
      "issuer": "https://identity.example.com/realms/team",
      "client_id": "a13n-console",
      "client_secret": "replace-at-deploy-time",
      "extra_scopes": ["groups"],
      "role_rules": [
        {"match": {"groups": "console-admins"}, "organization_id": "org_example", "role": "admin"}
      ]
    }
  }
}
```

确认 OIDC 组织管理员账号可用后，设置 `A13N_AUTH__PASSWORD_ENABLED=false` 即可切换为仅 OIDC 登录。

## 运维账号控制

运维人员可通过 CLI 停用或恢复任意账号（OIDC 账号使用 `--principal-id usr_...`）：

```sh
a13n-service --config service.toml user disable --email person@example.com
a13n-service --config service.toml user enable --email person@example.com
```

## 审计

在 **Organization settings → Audit**（`/api/v1/organizations/{organization_id}/audit-events`）、**Workspace settings → Audit**（`/api/v1/workspaces/{workspace_id}/audit-events`）或 `GET /api/v1/users/me/audit-events` 查看不可变的访问与安全审计记录（含 `actor_id`、`action`、`outcome`）。

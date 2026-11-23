---
title: 身份、角色与访问控制
description: 在 Service 中管理组织、工作区、RBAC 角色、邀请、服务账号、API 密钥与审计日志。
---

Service 通过\*\*组织（Organization）**和**工作区（Workspace）\*\*隔离资源，并对人类用户与自动化服务账号统一执行基于角色的访问控制（RBAC）。

## 组织与工作区

- **组织：** 顶层租户边界，管理成员、配额与工作区。
- **工作区：** 隔离智能体、线程、运行环境、提供方、连接与 API 密钥。

## 角色

| 角色      | 权限                                                      |
| --------- | --------------------------------------------------------- |
| `viewer`  | `read` —— 查看智能体、运行记录与资源                      |
| `runner`  | `read` + `run` —— 执行智能体、管理线程并使用运行环境      |
| `builder` | `read` + `run` + `write` —— 创建/编辑智能体、模板与提供方 |
| `admin`   | 完整的工作区/组织管理、成员授权与安全策略                 |

## 成员与授权

在 **Workspace settings → Members** 或 **Organization settings → Members** 中管理成员角色。移除授权后，后续请求会立即失效。

### 邀请

管理员在 **Organization settings → Invitations** 中发送邀请。受邀用户登录后会自动获得已分配的组织与工作区权限。

## 服务账号

为后端应用与 CI 流水线创建专用**服务账号**，避免复用个人凭据。按最小权限原则分配角色（例如仅执行已发布智能体的应用分配 `runner`）。

## API 密钥

在 **API keys** 中创建工作区作用域的 API 密钥（个人或服务账号），请求时通过 `Authorization: Bearer $A13N_API_KEY` 传递。请将 API 密钥保留在服务端，切勿嵌入浏览器前端代码。

## 运维账号控制

运维人员可通过 CLI 禁用或重新启用异常账号：

```sh
a13n-service user disable --email user@example.com
a13n-service user enable --email user@example.com
```

## 审计

Service 会在工作区与组织审计日志中记录资源变更、成员调整与安全事件（**Settings → Audit** 或 `/api/v1/audit-events`）。

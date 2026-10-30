---
title: Service API 参考
sidebarTitle: API 参考
description: 查找 HTTP 操作、准备请求，并使用与你的 Service 部署对应的 schema。
---

当你已经确定要读取或修改哪类 Service 资源时，使用本参考。导航按 API 领域组织操作；每个操作页展示参数、请求体、响应和请求示例。首次集成请先阅读[连接应用](../connect-application.md)，再回到这里查阅具体字段。

1. 找到对应资源和任务的操作。核对 HTTP 方法、路径、必填参数和请求体。
2. 按照 [HTTP 约定](../http.md)处理认证、更新前置条件、幂等、分页和错误。使用具有目标工作空间或资源访问权限的凭据。
3. 将示例替换为你的部署 URL 和资源 ID。生成的示例使用本地快速入门地址 `http://127.0.0.1:8080`。
4. 结合工作流指南阅读响应 schema。例如，[agent 与 run](../agents-and-runs.md)介绍如何在提交后跟进 run，以及处理等待或恢复。

要生成客户端或将 API 导入其他工具，请使用目标部署的 OpenAPI 文档。提供 API 的 Service 进程在 `/api/v1/openapi.json` 发布该文档。可下载的[完整 OpenAPI JSON](/reference/service-openapi.json)和这些操作页使用随本文档发布的仓库导出；公共文档站版本跟随 `main`。

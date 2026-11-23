---
title: 浏览器
description: 在 Docker、Kubernetes 或云提供方中为 Service 智能体分配托管 Chromium 浏览器，支持实时 VNC 观察与持久化配置文件。
---

当任务需要 JavaScript 渲染、交互式网页表单或已登录浏览器会话时，为智能体启用 `browser` 工具集。

## 快速开始

1. 在 **Workspace settings → Providers → Browser** 添加浏览器提供方（Docker、Kubernetes 或云浏览器提供方）。
2. 创建浏览器模板，并按需将其设为智能体的默认浏览器。
3. 在智能体版本中启用 `browser` 工具集，并配置[工具权限](agent-configuration.md#tool-permissions)。
4. 新建对话，让智能体打开指定 URL 并截取截图或提取页面内容。

## Kubernetes

在 Kubernetes Pod 中运行托管 Chromium 浏览器：

1. 按 [Helm 配置](configuration.md#kubernetes-browsers)启用 Kubernetes 浏览器（`kubernetesBrowsers.enabled=true`）。
2. 在 Console 打开 **Providers → Browser providers**，使用 Helm 输出的集群配置与命名空间创建 Kubernetes 浏览器提供方并验证连接。
3. 保持 CDP 与 VNC 端点仅在集群内部可达，并通过 Kubernetes `NetworkPolicy` 限制浏览器出站访问。

## 配置文件与实时视图

- **持久化配置文件：** 绑定持久存储卷以跨会话保留 Cookie 与站点登录态。
- **实时视图：** 在 Console 的浏览器面板中实时观看或接管正在运行的浏览器会话。

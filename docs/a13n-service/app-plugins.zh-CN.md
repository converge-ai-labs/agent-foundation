---
title: Service 应用插件
description: 构建并安装 Service App 插件，扩展 API 路由、Console 界面视图、Harness 插件工厂与自定义指标。
---

**Service App** 在复用 Service 认证、工作区与执行运行时的基础上，为平台扩展领域专属 API、Console 页面、持久化存储以及 Harness 中间件插件。

## 定义并安装应用

打包 `AppDefinition` 并通过 Python Entry Point 注册，使 Service 在启动时自动发现：

- **`factory_keys`：** 声明归属的 Harness 插件工厂键（注册在 `a13n_harness.plugins` 下，见[插件与扩展](../a13n-harness/plugins.md#publish-a-plugin-factory)）。Service 在启动时加载这些工厂，并按已受理运行快照控制实例启用。
- **路由与 Console 界面：** 暴露工作区作用域的 HTTP 端点，并将静态 Console 资源打包进 App wheel。
- **迁移与数据：** 将应用表保留在由 App 包管理的独立 Schema 中。

## 上报自定义指标

已安装的 App 插件可通过 Service 现有的 OpenTelemetry Meter Provider 上报自定义业务指标。请使用应用专属指标前缀与有界属性；HTTP 路由与后台扫描已由宿主自动埋点。

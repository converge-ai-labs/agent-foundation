---
title: 扩展 Provider 目录
sidebarTitle: Provider 插件
description: 选择可信 Provider 插件，或发布固定目标的环境执行适配器。
---

Host 组装 Provider 目录或实现 Provider 时使用本指南。内置运行时设置见 [Provider 与运行时](providers.md)。

## Provider 目录与插件

使用 `ProviderCatalog` 精确允许 Host 支持的定义。同一不可变目录适用于 Model、Web、Connector、Memory 和 Environment 领域。

安装发布入口 `acme` 和环境类型 `acme_sandbox` 的插件后，将它与选定的内置定义一起加载：

```python
from a13n_harness.providers.catalog import ProviderCatalog
from a13n_harness.providers.environment.builtins import select_builtin_environment_providers
from a13n_harness.providers.plugins import load_provider_plugins

plugins = load_provider_plugins(("acme",))
catalog = ProviderCatalog(
    (
        *select_builtin_environment_providers(("direct_local", "docker")),
        *(item for plugin in plugins for item in plugin.manifest.environment),
    )
)
definition = catalog.require("acme_sandbox")
```

加载器只导入指定名称的入口，空选择不扫描已安装元数据。它报告分发包、版本和导入来源，并拒绝重复、格式错误、缺失或含糊的名称。目录还拒绝重复的 provider 类型，因此插件不能覆盖内置定义。修改插件代码后重启 Host。

发布自己的插件时，在分发包的 `pyproject.toml` 中添加入口：

```toml
[project.entry-points."a13n_harness.providers.plugins"]
acme = "acme_agent_environment:manifest"
```

在 `acme_agent_environment` 中导出包含已定义的 `ACME_SANDBOX` 的 manifest：

```python
from a13n_harness.providers.plugins import ProviderManifest

manifest = ProviderManifest(api_version=1, environment=(ACME_SANDBOX,))
```

按以下顺序构建环境定义：

1. 选择稳定的 `type`、显示名称和可选 HTTPS 设置链接。
2. 分别定义账号配置、凭据和目标配置模型。
3. 提供执行元数据。管理功能使用独立的 Service `EnvironmentManagementDefinition`。
4. 实现纯 `connector_factory`，返回固定目标 `EnvironmentConnector`。接受每次传入的 `session_recovery` 关键字，包括 `None`；Native 实现接受它但不生成 Session 提示。
5. 每次 `open()` 返回新的已就绪 `EnvironmentExecution`，拥有独立 `execution_id` 和操作对象。
6. 验证目标引用而不修改权威状态。创建、启动、替换、续期和销毁交给管理端，放在执行的打开/检查/关闭之外。

继续阅读可运行的 [Provider 插件示例](https://github.com/converge-ai-labs/agent-foundation/tree/main/examples/plugins)和[共享插件契约](../a13n-harness/plugins.md#provider-plugins)。完整的内置管理、状态重新进入与销毁见 [Provider 示例](examples.md)。

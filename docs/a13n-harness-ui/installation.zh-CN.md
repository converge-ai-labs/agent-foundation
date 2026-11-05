---
title: 安装与升级
description: 安装终端和浏览器工作台、检查命令，并升级已有安装。
---

日常使用请选择已发布的安装包。它同时包含终端和浏览器界面，不需要源码检出目录或 Node.js。继续前，先安装 [uv](https://docs.astral.sh/uv/getting-started/installation/)。

## 安装 Harness UI

1. 将 Harness UI 安装到独立的工具环境：

   ```console
   uv tool install a13n-harness-ui
   ```

2. 检查 shell 能否找到命令：

   ```console
   a13n-harness-ui --help
   ```

   应当看到命令用法和子命令。如果 shell 提示找不到命令，运行 `uv tool update-shell`，重启 shell，再试一次。

3. 进入希望 Agent 处理的目录，然后启动：

   ```console
   cd your-repository
   a13n-harness-ui
   ```

   将 `your-repository` 替换为自己的目录。第一次启动时，按[设置第一个 Agent](setup.md)完成向导。该指南最后会发送测试消息，先确认模型连接正常，再使用文件和 shell 工具。

## WebUI

要从浏览器使用同一个安装，运行：

```console
a13n-harness-ui webui
```

打开打印的登录链接，并保持前台服务器运行。WebUI 提供自己的设置向导。Host 原生文件和终端共享默认开启；要关闭它们，使用 `a13n-harness-ui webui --no-share-computer` 启动。向他人开放访问前，先阅读 [WebUI](webui.md)。

### Browser Use 前置条件

可选的 Host 浏览器运行在 Linux 或 macOS WebUI Host 上，与显示 WebUI 的客户端分开。它需要兼容的浏览器，以及对应平台的显示服务或桌面权限。按照 [Host 浏览器设置](host-browser.md#first-time-setup)检查前置条件、安装组件，并完成首次导航。`--no-share-computer` 同时关闭这个浏览器与原生文件、终端共享。

## 升级

共享同一数据根目录的安装需要一起升级：

1. 停止这些安装的 TUI 和 WebUI 进程。

2. 如果通过 uv tool 安装，运行：

   ```console
   a13n-harness-ui update
   ```

   此命令要求 `PATH` 中有 `uv`，不会再次确认。在 Windows 上，命令会打开更新窗口并退出，以便替换自身的可执行文件。等待该窗口报告成功，再按 Enter 关闭窗口。在 macOS 和 Linux 上，等待命令完成。

   需要安装器退出状态的脚本应直接运行 `uv tool upgrade a13n-harness-ui`。Windows 上原命令的零退出状态只表示交接成功。其他安装方式使用原来的包管理器。

3. 所有共享安装都升级完成后，重新启动应用，并刷新已打开的 WebUI 页面。

启动新版本前，先升级共享同一数据根目录的所有安装。当前检查点使用 payload codec `2`；只支持 codec `1` 的旧版应用无法读取新写入的检查点。其他兼容升级可以保留共享访问，但数据库迁移编号较高并不能单独证明检查点兼容。在混用版本或降级前查阅发行说明，并保留跨越不兼容格式边界前的备份。

启动更新检查只显示提示。用 `--no-update-check` 可关闭本次检查；要影响后续启动，在根 YAML 中设置 `process.terminal_update_check: false`。详见[更新与日志](automation-and-troubleshooting.md#logs-updates-and-exit)。

### 依赖兼容性

让包管理器整体升级 Harness UI，并解析兼容的依赖，包括 Harness、Stream Protocol、Envd Client 和日志包。如果依赖解析失败，先检查自行添加的版本约束，不要分别升级这些依赖。

### 沙箱运行时

Full Control 不需要下载 Envd。Sandbox 在需要时获取与已安装 `a13n-envd-client` 匹配的守护进程版本，升级后也是如此。版本为 `0.0.0` 的源码构建需要显式提供经过验证的 `a13n-envd` 可执行文件；见 [Envd 安装](../a13n-envd/installation.md)。

## 从源码运行

开发 Harness UI 或按 `main` 重现文档示例时，使用此方式。先安装 [CONTRIBUTING.md](https://github.com/converge-ai-labs/agent-foundation/blob/main/CONTRIBUTING.md) 中的开发前置工具，再运行：

```console
git clone https://github.com/converge-ai-labs/agent-foundation.git
cd agent-foundation
make a13n-harness-ui
```

Make 目标会同步锁定的依赖、重新生成[内置配置 Skill](skills-and-content-plugins.md#built-in-configuration-skill)，并关闭启动更新检查。切换分支后重新运行。修改文档后只需重新生成 Skill 时，运行 `make a13n-harness-ui-skills`。

这份文档源码跟随 `main`。如果已发布版本存在差异，以该发行版的文档和元数据为准。

## 下一步

[设置第一个 Agent](setup.md)，连接模型账户并确认能收到回复。

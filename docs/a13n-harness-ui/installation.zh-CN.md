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

WebUI 在你的浏览器中打开。可选的 **Browser** 旁栏会在 Linux 或 macOS WebUI Host 上另外启动一个浏览器，供 Agent 操作。

按平台准备 Host：

- **Linux**：安装 Chrome、Chromium 或 Edge，以及兼容的 VNC 服务。容器部署需要将这些程序安装在容器内。TigerVNC 同时提供显示和 VNC 服务。以非 root 用户运行，并保持 Chromium 沙箱开启。
- **macOS**：安装兼容浏览器，以已登录桌面的用户运行 Harness UI。可选的只读预览需要 macOS 14 或更高版本，并给运行 Harness UI 的应用授予屏幕录制权限。预览只显示当前对话拥有的浏览器窗口，包括地址栏。在 Mac 本机操作浏览器前选择 **Pause automation**，完成后选择 **Resume automation**。无需 VNC 服务或密码。

默认 Auto 模式在 WebUI 启动时准备共享浏览器。让 Full Control Run 浏览，或选择 **Browser → Open browser**。Harness UI 自动选择已安装的兼容程序；需要修改 Auto/Disabled 模式或覆盖程序选择时，使用 **Settings → General → Host browser**。[浏览器设置流程](webui.md#first-time-setup)介绍各平台步骤和首次导航。Harness UI 已包含浏览器自动化依赖。需要保持电脑共享开启：`--no-share-computer` 也会关闭 Host 浏览器。

## 升级

共享同一数据根目录的安装需要一起升级：

1. 停止这些安装的 TUI 和 WebUI 进程。

2. 如果通过 uv tool 安装，运行：

   ```console
   a13n-harness-ui update
   ```

   此命令要求 `PATH` 中有 `uv`，不会再次询问。也可运行 `uv tool upgrade a13n-harness-ui`；其他安装方式使用原来的包管理器。

3. 所有共享安装都升级完成后，重新启动应用，并刷新已打开的 WebUI 页面。

升级后的应用会读取已有对话，并按当前检查点格式写入。旧版应用无法读取新写入的检查点，因此共享安装应使用同一升级后的版本。

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

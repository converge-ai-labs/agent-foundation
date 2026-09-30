---
title: 安装与升级
description: 安装、升级 Harness UI，或从源码运行。
---

## 安装 Harness UI

使用 [uv](https://docs.astral.sh/uv/getting-started/installation/) 将 Harness UI 安装到独立的工具环境：

```console
uv tool install a13n-harness-ui
cd your-repository
a13n-harness-ui
```

不需要源码检出目录或 Node.js。如果 shell 找不到命令，运行 `uv tool update-shell`，然后重新启动 shell。

可选的 Bash/Zsh 快捷方式：在 shell 配置文件中加入 `alias anui='a13n-harness-ui'`。

## 升级

通过 uv tool 安装时，运行 `a13n-harness-ui update` 或 `uv tool upgrade a13n-harness-ui`，然后重启 Harness UI。应用内升级命令要求 PATH 中有 uv，且不会再次询问。其他安装方式请使用原来的包管理器。

启动时可以检查更新，但未经确认不会安装。使用 `--no-update-check` 可在本次启动时关闭检查，使用 `process.terminal_update_check: false` 可永久关闭。见[更新与故障行为](automation-and-troubleshooting.md#logs-updates-and-exit)。

### 依赖兼容性

uv 按照已发布的依赖要求解析应用和依赖。Harness UI 独立发布，支持兼容的 Harness、Stream Protocol 和 Environment 依赖版本。如果旧包的依赖要求阻止了更新，应升级 Harness UI 本身，并检查你提供的版本约束，而不要强行安装不兼容版本。

### 沙箱运行时

Full Control 不需要下载 Envd。Sandbox 会在需要时获取与已安装 `a13n-envd-client` 版本匹配的守护进程，升级后也是如此。版本为 `0.0.0` 的源码构建在使用受管理的 Local EIP 时，需要显式指定经过验证的可执行文件。见 [Envd 安装](../a13n-envd/index.md)。

## 从源码运行

使用仓库锁定的环境，不要将本地代码与手动安装的依赖混用：

```console
git clone https://github.com/converge-ai-labs/agent-foundation.git
cd agent-foundation
make a13n-harness-ui
```

开发前置条件见仓库的[贡献指南](https://github.com/converge-ai-labs/agent-foundation/blob/main/CONTRIBUTING.md)。Make 目标会同步依赖并关闭已发布包的启动更新检查。切换分支后，重新运行该目标或运行 `make sync`。修改 UI 文档或导航后，运行 `make a13n-harness-ui-skills` 更新[内置配置 Skill](skills-and-content-plugins.md#built-in-configuration-skill)；启动目标已经包含此依赖。

源码文档跟随 `main`。使用已发布的 wheel 时，如果 API 或设置不同，应查阅对应发行版的文档和元数据。

## 其他界面

安装包包含两种界面。启动浏览器工作台：

```console
a13n-harness-ui webui
```

打开打印的登录链接，即可配置模型、开始对话并与可信协作者一起工作。前台服务器负责正在进行的执行，使用浏览器时需保持服务器运行。宿主机原生文件和终端共享默认开启；使用 `--no-share-computer` 可以关闭。访问控制、协作边界、容器和监听配置见[使用浏览器](webui.md)。

## 下一步

[完成设置](setup.md)，然后[找到你的配置](configuration.md)。

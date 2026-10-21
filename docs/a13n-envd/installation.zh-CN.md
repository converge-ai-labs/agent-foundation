---
title: 安装 Envd
sidebarTitle: 安装
description: 构建或下载与 EIP 客户端匹配的 Envd 二进制文件，或运行沙箱镜像。
---

选择与 EIP 客户端版本匹配的原生守护进程。如果只使用 Harness UI 的本地 Sandbox 模式，Harness UI 会自行获取匹配的二进制文件，无需在该计算机上另行安装。要将另一台计算机作为设备连接到 Harness UI，请在那台计算机上安装 Envd。

## 构建匹配版本的二进制文件

本文档跟随 `main`。从 Python 工作空间所在的同一份检出中构建 `a13n-envd`，使守护进程与客户端通过 Local Envd 的精确版本检查：

```bash
cargo build --locked --package a13n-envd
```

然后让 Local Envd 使用这个二进制文件：

```bash
export A13N_ENVD_EXECUTABLE="$PWD/target/debug/a13n-envd"
```

Python 包 `a13n-envd-client` 不会发现、安装或启动二进制文件。进程生命周期和传输策略由 provider 或 Host 提供。

使用已发布的 Local Envd 时，原生 `a13n-envd` 应与已安装的 `a13n-envd-client` 版本匹配，而不是与独立发版的 Harness 或 Harness UI 匹配。Python RC 版本 `1.2.3rc1` 对应原生版本 `1.2.3-rc.1`。Harness UI 为其 Sandbox 模式管理版本选择和获取；独立 Host 需要明确提供可执行文件。不要将任意已发布的二进制文件与从源码工作空间构建的客户端混用。

## 安装已发布的二进制文件

独立安装脚本从 GitHub Releases 下载原生可执行文件。请另行安装 Python 客户端，并选择匹配的原生版本。

### Linux 和 macOS

需要 POSIX shell、`curl`、`tar`，以及 `sha256sum` 或 `shasum`。自动选择稳定版本还需要 `jq`；显式指定 `--version` 时不需要。

下载并检查脚本后再运行。如果当前 Python 环境已安装**发布版** 客户端，可获取其匹配的原生版本（包括适用的 RC 版本）：

```bash
curl -fsSLo install-a13n-envd.sh \
  https://raw.githubusercontent.com/converge-ai-labs/agent-foundation/main/scripts/install-a13n-envd.sh
# Inspect install-a13n-envd.sh before running it.
ENVD_VERSION="$(python -c 'from importlib.metadata import version; import re; print(re.sub(r"rc([0-9]+)$", r"-rc.\1", version("a13n-envd-client")))')"
sh install-a13n-envd.sh --version "$ENVD_VERSION" --install-dir "$HOME/.local/bin"
"$HOME/.local/bin/a13n-envd" --version
```

### Windows

在 Windows 上使用 Windows PowerShell 5.1 或 PowerShell 7。如果所选 Python 环境已安装**发布版** 客户端：

```powershell
Invoke-WebRequest -Uri 'https://raw.githubusercontent.com/converge-ai-labs/agent-foundation/main/scripts/install-a13n-envd.ps1' -OutFile install-a13n-envd.ps1
# Inspect the downloaded script before running it.
$envdVersion = python -c "from importlib.metadata import version; import re; print(re.sub(r'rc([0-9]+)$', r'-rc.\1', version('a13n-envd-client')))"
& .\install-a13n-envd.ps1 --version $envdVersion --install-dir "$env:LOCALAPPDATA\A13N\bin"
& "$env:LOCALAPPDATA\A13N\bin\a13n-envd.exe" --version
```

按照组织的 PowerShell 执行策略运行脚本；安装程序不会修改该策略。

### 选项与行为

两个脚本接受相同的选项：

| 选项                                 | 环境变量默认值                  | 行为                                                                                                                                                              |
| ------------------------------------ | ------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `--version X.Y.Z`                    | `A13N_ENVD_VERSION`             | 精确的原生版本；`0.0.6-rc.1` 等规范 RC 版本需要显式选择。两者都省略时，从 GitHub 按最新优先排列的发布列表中选择第一个稳定的 Envd 版本，跳过其他组件和预发布版本。 |
| `--install-dir PATH`                 | `A13N_ENVD_INSTALL_DIR`         | 绝对目标目录。POSIX 普通用户默认为 `~/.local/bin`，POSIX root 默认为 `/usr/local/bin`，Windows 默认为 `%LOCALAPPDATA%\A13N\bin`。                                 |
| `--add-to-path` / `--no-add-to-path` | `A13N_ENVD_ADD_TO_PATH=1` / `0` | 两个选项互斥。默认不修改 PATH。显式选项覆盖环境变量值。                                                                                                           |
| `--help`                             | —                               | 显示用法，不执行安装。                                                                                                                                            |

选择修改 PATH 时，只更新当前用户的配置：Bash 的 `.bashrc`（macOS 上为 `.bash_profile`）、Zsh 的 `${ZDOTDIR:-$HOME}/.zshrc`、POSIX shell 的 `.profile`、Fish 的 `${XDG_CONFIG_HOME:-$HOME/.config}/fish/config.fish`，或 Windows 用户 `Path`。重复运行不会重复追加相同条目。完成后打开新的终端。不支持的 shell 可以使用 `--no-add-to-path`，再手动配置 PATH。

安装程序支持 Linux、macOS 和 Windows 上的 x86_64 与 ARM64。它们在**解压前** 根据该版本的 `SHA256SUMS` 验证归档，将可执行文件暂存到目标文件系统，再以原子方式安装或替换。下载、校验、解压或替换失败都不会改动已有可执行文件。SHA256 将归档与 GitHub 提供的清单核对；它不是独立的签名或信任来源。

## 更新已有安装

替换前，先停止使用该安装的守护进程。Envd 从自身可执行文件路径启动 worker，在守护进程运行时替换该路径可能混用版本或导致 worker 无法启动。更新器不会自动停止或重启服务。

```console
a13n-envd update --check
a13n-envd update
```

默认选择最新的 **Envd** 稳定版，跳过仓库中的其他组件和预发布版本。要匹配客户端、安装 RC 或显式降级，请指定精确的已发布版本：

```console
a13n-envd update --version 1.2.3
a13n-envd update --version 1.2.3 --force
```

`--check` 显示当前版本、所选版本和目标路径，不执行安装。`--force` 重新安装所选版本，不能与 `--check` 同时使用。命令更新实际调用的可执行文件，包括通过符号链接调用的情况。它不会修改 PATH 或配置，也不会提权。请使用安装所有者的账号执行。成功后，通过原命令或服务重启 Envd。

Envd 下载匹配的发布归档，验证 `SHA256SUMS`，检查解压后程序的 `--version`，然后替换可执行文件。下载和验证失败会保留已有程序。如果在替换期间中断，请先用 `--version` 检查安装，再决定是否重试。强制终止独立更新器可能在可执行文件旁留下 `.a13n-envd-update-*` 暂存目录；确认更新器已退出后，才可删除本次操作对应的目录。

Host 可通过 `--version ... --install-dir ...` 将另一个精确版本安装到独立的绝对目录，同时保留源安装。`--staging-dir` 接受调用方拥有的已有暂存目录，便于中断子进程退出后清理。Harness UI 使用这些选项维护按版本选择的缓存。`.a13n-envd-version` 标记固定托管目标版本；请通过 Harness UI 更新，不要将缓存条目替换为另一个版本。

对于不支持 `update` 的旧版程序，先用上面的独立安装脚本安装一次支持该命令的版本。替换前同样需要停止守护进程。在 macOS 上，仅当 Envd 报告缺少权限时，按[升级后恢复 macOS 权限](computer-use.md#restore-macos-permissions-after-an-upgrade)操作；权限等待超时错误会附带恢复指引。

## 运行沙箱镜像

需要可直接使用的 agent 开发容器时，使用[沙箱镜像](sandbox.md)。镜像以 root 运行 Envd，而命令和文件操作使用预置的 `1000:1000` 账号，并支持免密码 sudo。shell 命令默认启用，受控出站网络需要主动开启。指南介绍本地构建、stdio 和向外连接 Host、持久化与配置覆盖。

## 下一步

使用 [Local Envd provider](index.md#try-local-envd)；如果由你管理传输和生命周期，则[配置独立守护进程](configuration.md)。运行不可信工作前，先阅读[执行边界](isolation.md)，并选定 Host 的外层边界。

---
title: 安装 Envd
sidebarTitle: 安装
description: 构建或下载与 EIP 客户端匹配的 Envd 二进制文件，或运行沙箱镜像。
---

在实际执行文件、命令或桌面操作的计算机上安装 Envd。Harness UI 会为本地 Sandbox 模式获取匹配的二进制文件。当你要把另一台计算机连接为 Device，或由应用直接管理 Envd 时，使用本指南。

## 构建匹配版本的二进制文件

在仓库中开发时，从同一份检出构建守护进程和 Python 客户端。本文档跟随 `main`；Local Envd 会检查两者的发布版本是否匹配。

1. 安装 Rust，在仓库根目录构建 Envd。
2. 在同一个 shell 中，将 `A13N_ENVD_EXECUTABLE` 设为构建出的二进制文件路径。

```bash
cargo build --locked --package a13n-envd
```

选择构建出的可执行文件：

```bash
export A13N_ENVD_EXECUTABLE="$PWD/target/debug/a13n-envd"
```

构建产物是 `target/debug/a13n-envd`。Provider 或 Host 负责启动它并选择传输方式；`a13n-envd-client` 提供 Python 协议客户端。

使用已发布的 Local Envd 时，让原生版本与已安装的 `a13n-envd-client` 匹配；该客户端独立于 Harness 和 Harness UI 发版。Python RC 版本 `1.2.3rc1` 对应原生版本 `1.2.3-rc.1`。

## 安装已发布的二进制文件

独立脚本从 GitHub Releases 安装原生可执行文件。为 Python 应用选择版本时，先在将使用 Envd 的环境中安装 [Python 客户端](python-client.md#install)。下方命令读取该环境中已安装包的版本。

### Linux 和 macOS

准备 POSIX shell、`curl`、`tar`，以及 `sha256sum` 或 `shasum`。自动选择稳定版本还需要 `jq`；下方显式指定版本的方式不需要。

1. 选择已安装发布版客户端的 Python 环境。
2. 下载并检查安装脚本，再执行安装。
3. 读取客户端版本，安装匹配的可执行文件，并检查 `--version`。

```bash
curl -fsSLo install-a13n-envd.sh \
  https://raw.githubusercontent.com/converge-ai-labs/agent-foundation/main/scripts/install-a13n-envd.sh
# Inspect install-a13n-envd.sh before running it.
ENVD_VERSION="$(python -c 'from importlib.metadata import version; import re; print(re.sub(r"rc([0-9]+)$", r"-rc.\1", version("a13n-envd-client")))')"
sh install-a13n-envd.sh --version "$ENVD_VERSION" --install-dir "$HOME/.local/bin"
"$HOME/.local/bin/a13n-envd" --version
```

### Windows

使用 Windows PowerShell 5.1 或 PowerShell 7。

1. 选择已安装发布版客户端的 Python 环境。
2. 下载并检查安装脚本。
3. 安装匹配版本，并检查输出路径中的可执行文件。

```powershell
Invoke-WebRequest -Uri 'https://raw.githubusercontent.com/converge-ai-labs/agent-foundation/main/scripts/install-a13n-envd.ps1' -OutFile install-a13n-envd.ps1
# Inspect the downloaded script before running it.
$envdVersion = python -c "from importlib.metadata import version; import re; print(re.sub(r'rc([0-9]+)$', r'-rc.\1', version('a13n-envd-client')))"
& .\install-a13n-envd.ps1 --version $envdVersion --install-dir "$env:LOCALAPPDATA\A13N\bin"
& "$env:LOCALAPPDATA\A13N\bin\a13n-envd.exe" --version
```

按照组织的 PowerShell 执行策略运行脚本；安装程序不会修改该策略。

### 选项与行为

用下列选项选择精确版本和目标目录。两个安装脚本使用相同的选项名：

| 选项                                 | 环境变量默认值                  | 行为                                                                                                                                                              |
| ------------------------------------ | ------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `--version X.Y.Z`                    | `A13N_ENVD_VERSION`             | 精确的原生版本；`0.0.6-rc.1` 等规范 RC 版本需要显式选择。两者都省略时，从 GitHub 按最新优先排列的发布列表中选择第一个稳定的 Envd 版本，跳过其他组件和预发布版本。 |
| `--install-dir PATH`                 | `A13N_ENVD_INSTALL_DIR`         | 绝对目标目录。POSIX 普通用户默认为 `~/.local/bin`，POSIX root 默认为 `/usr/local/bin`，Windows 默认为 `%LOCALAPPDATA%\A13N\bin`。                                 |
| `--add-to-path` / `--no-add-to-path` | `A13N_ENVD_ADD_TO_PATH=1` / `0` | 两个选项互斥。默认不修改 PATH。显式选项覆盖环境变量值。                                                                                                           |
| `--help`                             | —                               | 显示用法，不执行安装。                                                                                                                                            |

选择修改 PATH 时，只更新当前用户的配置：Bash 的 `.bashrc`（macOS 上为 `.bash_profile`）、Zsh 的 `${ZDOTDIR:-$HOME}/.zshrc`、POSIX shell 的 `.profile`、Fish 的 `${XDG_CONFIG_HOME:-$HOME/.config}/fish/config.fish`，或 Windows 用户 `Path`。重复运行不会重复追加相同条目。完成后打开新的终端。不支持的 shell 可以使用 `--no-add-to-path`，再手动配置 PATH。

安装程序支持 Linux、macOS 和 Windows 上的 x86_64 与 ARM64。它们在**解压前** 根据该版本的 `SHA256SUMS` 验证归档，将可执行文件暂存到目标文件系统，再以原子方式安装或替换。下载、校验、解压或替换失败都不会改动已有可执行文件。SHA256 将归档与 GitHub 提供的清单核对；它不是独立的签名或信任来源。

## 更新已有安装

使用安装所有者的账号，在安排好的停机重启窗口中更新。Envd 从自身可执行文件路径启动 worker，替换正在运行的守护进程的二进制文件可能混用版本或导致 worker 无法启动。

1. 用 `update --check` 检查所选版本和目标路径。
2. 停止所有使用该可执行文件的守护进程。
3. 执行更新；需要时按下方示例指定精确版本。
4. 验证 `a13n-envd --version`，再通过原命令或服务重启 Envd。

```console
a13n-envd update --check
a13n-envd update
```

默认选择最新的 **Envd** 稳定版，跳过仓库中的其他组件和预发布版本。要匹配客户端、安装 RC 或显式降级，请指定精确的已发布版本：

```console
a13n-envd update --version 1.2.3
a13n-envd update --version 1.2.3 --force
```

`--force` 重新安装所选版本，不能与 `--check` 同时使用。更新器替换实际调用的可执行文件，包括通过符号链接访问的情况。它保留 PATH 和配置，使用当前账号权限，服务的停止和重启由你负责。

Envd 下载匹配的发布归档，验证 `SHA256SUMS`，检查解压后程序的 `--version`，然后替换可执行文件。下载和验证失败会保留已有程序。如果在替换期间中断，请先用 `--version` 检查安装，再决定是否重试。强制终止独立更新器可能在可执行文件旁留下 `.a13n-envd-update-*` 暂存目录；确认更新器已退出后，才可删除本次操作对应的目录。

Host 可通过 `--version ... --install-dir ...` 将另一个精确版本安装到独立的绝对目录，同时保留源安装。`--staging-dir` 接受调用方拥有的已有暂存目录，便于中断子进程退出后清理。Harness UI 使用这些选项维护按版本选择的缓存。`.a13n-envd-version` 标记固定托管目标版本；请通过 Harness UI 更新，不要将缓存条目替换为另一个版本。

对于不支持 `update` 的旧版程序，先用上面的独立安装脚本安装一次支持该命令的版本。替换前同样需要停止守护进程。在 macOS 上，仅当 Envd 报告缺少权限时，按[升级后恢复 macOS 权限](computer-use.md#restore-macos-permissions-after-an-upgrade)操作；权限等待超时错误会附带恢复指引。

## 运行沙箱镜像

使用标准[沙箱镜像](sandbox.md)，获得已启用 shell 命令的 agent 开发容器。Envd 以 root 运行；命令和文件操作使用预置的 `1000:1000` 账号，并支持免密码 sudo。需要受控出站网络时显式开启。镜像指南介绍本地构建、stdio 和向外连接 Host、持久化与配置覆盖。

## 下一步

选好匹配的二进制文件后，试用 [Local Envd Provider](index.md#try-local-envd)，或[配置自己的守护进程](configuration.md)。运行不可信工作前，按[执行边界](isolation.md)选择 Host 的外层边界。

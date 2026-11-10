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

默认构建不包含 VM 后端及其运行时资源。Linux 上可显式启用两个 VM 后端：`BOXLITE_DEPS_STUB=2 BOXLITE_EMBED_RUNTIME=1 cargo build --locked --package a13n-envd --features vm`。该构建还需要 C 编译器及 3.12 或更新版本的 `protoc`。启用 VM 后仍可使用原生执行。

## 从 Console 连接这台电脑

批准后，所选工作空间中拥有运行权限的用户和服务账号都能在已批准的操作权限内使用这台电脑。

macOS 源码开发时，先按上文构建匹配的二进制，再安装可选的链接启动器：

```bash
make envd-app-install
# 升级前先断开现有连接：
make envd-app-install ENVD_APP_ARGS=--replace
```

安装需要 Xcode Command Line Tools。脚本生成原生 `~/Applications/A13N Envd.app`，注册 `a13n-envd://` 链接，并复制所选二进制（可用 `ENVD_BINARY` 指定路径）。更新时会把旧 App 备份到 `~/Applications/.a13n-envd-previous.*`。该开发 App 只做本地签名，不是经过公证的正式安装包，也不会添加登录启动服务。

1. 打开 **Console → 环境 → 实例 → 连接我的电脑**。
2. 如果浏览器询问是否打开外部应用，确认后继续。未自动打开时，点击**打开 A13N Envd**。
3. 首次注册时，在 App 内核对服务地址和工作区，选择起始目录及是否允许桌面操作，然后点击**允许并连接**。文件和 Shell 使用当前操作系统用户权限，所选目录不限制对其他文件的访问。
4. 首次注册时，将 App 显示的六位验证码填入 Console，核对电脑信息并批准。
5. 等待**在线**，再在 Agent 或对话设置中选择该环境。批准本身不会启动任务。

Envd 由 App 管理，不再打开 Terminal。关闭窗口后仍保持连接，可从菜单栏重新打开。点击**断开连接**或退出 App 会停止 Envd；App 异常退出也会停止它。运行中重复打开链接只会回到现有连接，不会再启动第二个进程。需要切换连接时，先断开，再打开新链接。App 会记住最后批准的 Service 配置名、目录和桌面选项，供下次点击**重新连接**；设备凭据不会复制到 App 偏好设置中。从旧启动器升级时，先在旧 Terminal 中按 Ctrl+C 停止进程。桌面功能还需要 macOS 的屏幕录制和辅助功能权限，出现系统提示时需先完成授权。

其他系统或未安装启动器的 macOS，可以使用对话框中的**从终端连接**。复制的 `connect-service --pairing-id` 命令需要 PATH 中有兼容的二进制，并使用本机配置。需要任意 Shell 命令时，在本机设置 `A13N_ENVD_FULL_CONTROL=1`；需要桌面操作时再添加 `--computer-use true`。网页不会安装软件或授予本机权限。配对十分钟后过期，过期后重新发起即可。已保存的配置可以通过 `a13n-envd connect-service <保存的主机名>` 重连，无需 API key。

刷新 Console 后，再点**连接我的电脑**会打开 Envd 检查已有注册。已注册的电脑直接复用，不需要新的验证码或 device ID。在线连接保持运行；离线时可在 Envd 中重新连接。已有凭据缺失或失效会报错，不会自动创建替代实例。

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

安装脚本默认选择不含 VM 的原生归档。Linux 用户可通过 `--variant vm` 或 `A13N_ENVD_VARIANT=vm` 选择较大的 VM 归档。VM 归档在目标名后添加 `-vm`，例如 `a13n-envd-<version>-x86_64-unknown-linux-gnu-vm.tar.gz`。缺少所选 VM 资源时直接失败，不会回退。`0.7.0` 等历史发布保留原有资源；请使用提供拆分构建的新版本。从尚未拆分的旧版本迁移并保留 VM 支持时，请使用安装脚本指定 `--variant vm`；旧更新器会选择普通归档。

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

更新器保留当前 Linux 构建类型：不含 VM 的构建选择普通归档，包含任一 VM 后端的构建选择完整的 `-vm` 归档。需要切换类型时，先停止守护进程，再用独立安装脚本指定 `--variant default` 或 `--variant vm`。

Envd 下载匹配的发布归档，验证 `SHA256SUMS`，检查解压后程序的 `--version`，然后替换可执行文件。下载和验证失败会保留已有程序。如果在替换期间中断，请先用 `--version` 检查安装，再决定是否重试。强制终止独立更新器可能在可执行文件旁留下 `.a13n-envd-update-*` 暂存目录；确认更新器已退出后，才可删除本次操作对应的目录。

Host 可通过 `--version ... --install-dir ...` 将另一个精确版本安装到独立的绝对目录，同时保留源安装。`--staging-dir` 接受调用方拥有的已有暂存目录，便于中断子进程退出后清理。Harness UI 使用这些选项维护按版本选择的缓存。`.a13n-envd-version` 标记固定托管目标版本；请通过 Harness UI 更新，不要将缓存条目替换为另一个版本。

对于不支持 `update` 的旧版程序，先用上面的独立安装脚本安装一次支持该命令的版本。替换前同样需要停止守护进程。在 macOS 上，仅当 Envd 报告缺少权限时，按[升级后恢复 macOS 权限](computer-use.md#restore-macos-permissions-after-an-upgrade)操作；权限等待超时错误会附带恢复指引。

## 运行沙箱镜像

使用标准[沙箱镜像](sandbox.md)，获得已启用 shell 命令的 agent 开发容器。Envd 以 root 运行；命令和文件操作使用预置的 `1000:1000` 账号，并支持免密码 sudo。需要受控出站网络时显式开启。镜像指南介绍本地构建、stdio 和向外连接 Host、持久化与配置覆盖。

沙箱镜像也默认使用不含 VM 的 Envd。需要 VM 时，选择 `vm`、`slim-vm` 或 `desktop-vm`；精确版本标签为 `<version>-vm`、`<version>-slim-vm` 和 `<version>-desktop-vm`，开发标签为 `dev-vm`、`dev-slim-vm` 和 `dev-desktop-vm`。本地源码构建镜像时添加 `--build-arg ENVD_VARIANT=vm`；`SANDBOX_VARIANT` 仍独立选择工具组合。Service 自动获取使用普通归档和镜像标签。已有 Service 版本固定值需在后续发布更新后采用新构建。

## 下一步

选好匹配的二进制文件后，试用 [Local Envd Provider](index.md#try-local-envd)，或[配置自己的守护进程](configuration.md)。运行不可信工作前，按[执行边界](isolation.md)选择 Host 的外层边界。

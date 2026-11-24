---
title: 安装 Envd
sidebarTitle: 安装与升级
description: 下载经校验的 a13n-envd 发布版二进制文件，在标准版、精简版与虚拟机版之间按需选择，或从 Cargo 源码构建。
---

在需要执行文件、命令或桌面操作的机器上安装 `a13n-envd`。Harness UI 本地沙箱模式会自动拉取匹配的二进制文件；当你需要将某台电脑注册为远程设备或直接管理守护进程时，请参考本指南。

## 选择二进制构建变体

Linux、macOS 与 Windows 六大平台均发布 `slim` 与标准 `default` 构建，Linux 额外提供 `vm` 构建。所有变体共享同一套 EIP 协议（可通过 `a13n-envd --build-variant` 查看已安装变体）：

| 构建变体     | 包含能力                                                                                 | 归档后缀 |
| ------------ | ---------------------------------------------------------------------------------------- | -------- |
| `slim`       | 文件、Shell/进程、传输、原生隔离与受控出站、全部传输/TLS、监控导出、设备注册、内置升级器 | `-slim`  |
| `default`    | 在 `slim` 基础上增加原生桌面截图、键鼠输入、剪贴板与无障碍树                             | 无后缀   |
| `vm` (Linux) | 在 `default` 基础上内置轻量级虚拟机（microVM）后端                                       | `-vm`    |

安装脚本默认选择 `default`；无头服务器可传入 `--variant slim`（`A13N_ENVD_VARIANT=slim`），需要 microVM 隔离的 Linux 主机可传入 `--variant vm`（`A13N_ENVD_VARIANT=vm`）。

## 从源码构建匹配版本的二进制

在仓库内开发时，请从同一源码检出构建 `a13n-envd` 与 `a13n-envd-client` 以确保版本严格一致：

```bash
cargo build --locked --package a13n-envd
export A13N_ENVD_EXECUTABLE="$PWD/target/debug/a13n-envd"
```

如需构建 `slim` 版可加 `--no-default-features`；在 Linux 上构建 `vm` 版（需 C 编译器与 `protoc >= 3.12`）可执行 `BOXLITE_DEPS_STUB=2 BOXLITE_EMBED_RUNTIME=1 cargo build --locked --package a13n-envd --features vm`。

## 从控制台连接当前电脑

通过 **Console → Environments → Instances → Connect my computer** 将个人电脑注册到 Service 工作区：

- **macOS 启动器应用（源码检出）：** 先按上节构建二进制，执行 `make envd-app-install`（升级已有应用加 `ENVD_APP_ARGS=--replace`）安装 `~/Applications/A13N Envd.app`，点击网页上的 **Connect my computer**，在弹窗中选择起始目录与是否开启桌面访问，再将显示的 6 位配对码填入控制台审批（开启桌面操控需授予 macOS 录屏与辅助功能权限）。
- **终端命令行配对（Linux、Windows 或未安装启动器的 macOS）：** 在弹窗中选择 **Connect from a terminal**，在 10 分钟有效期内执行复制出的 `a13n-envd connect-service --pairing-id ...` 命令（设置 `A13N_ENVD_FULL_CONTROL=1` 允许任意 Shell 命令；按需加 `--computer-use true`）。已保存的主机配置后续可直接通过 `a13n-envd connect-service <saved-host-name>` 重连。

## 安装已发布的二进制包

请将原生 `a13n-envd` 版本与环境中已安装的 Python [`a13n-envd-client`](python-client.md#install) 对齐（`1.2.3rc1` 对应 `1.2.3-rc.1`）。安装脚本会在原子替换前自动校验 `SHA256SUMS`。

### Linux 与 macOS

```bash
curl -fsSLo install-a13n-envd.sh \
  https://raw.githubusercontent.com/converge-ai-labs/agent-foundation/main/scripts/install-a13n-envd.sh
ENVD_VERSION="$(python -c 'from importlib.metadata import version; import re; print(re.sub(r"rc([0-9]+)$", r"-rc.\1", version("a13n-envd-client")))')"
sh install-a13n-envd.sh --version "$ENVD_VERSION" --install-dir "$HOME/.local/bin"
"$HOME/.local/bin/a13n-envd" --version
```

### Windows (PowerShell)

```powershell
Invoke-WebRequest -Uri 'https://raw.githubusercontent.com/converge-ai-labs/agent-foundation/main/scripts/install-a13n-envd.ps1' -OutFile install-a13n-envd.ps1
$envdVersion = python -c "from importlib.metadata import version; import re; print(re.sub(r'rc([0-9]+)$', r'-rc.\1', version('a13n-envd-client')))"
& .\install-a13n-envd.ps1 --version $envdVersion --install-dir "$env:LOCALAPPDATA\A13N\bin"
& "$env:LOCALAPPDATA\A13N\bin\a13n-envd.exe" --version
```

| 安装参数                             | 环境变量                        | 行为说明                                                                                      |
| ------------------------------------ | ------------------------------- | --------------------------------------------------------------------------------------------- |
| `--version X.Y.Z`                    | `A13N_ENVD_VERSION`             | 指定精确版本号（省略时自动读取已发布的最新稳定版指针）                                        |
| `--install-dir PATH`                 | `A13N_ENVD_INSTALL_DIR`         | 安装目录（普通用户 `~/.local/bin`、root `/usr/local/bin`、Windows `%LOCALAPPDATA%\A13N\bin`） |
| `--variant default\|slim\|vm`        | `A13N_ENVD_VARIANT`             | 选择二进制变体（默认为 `default`）                                                            |
| `--add-to-path` / `--no-add-to-path` | `A13N_ENVD_ADD_TO_PATH=1` / `0` | 是否将安装目录写入当前用户的 Shell 配置文件或用户 `Path`                                      |

## 升级现有安装

升级前请先停止使用该可执行文件的所有 `a13n-envd` 守护进程，防止新旧工作进程混用，然后执行内置升级命令：

```console
a13n-envd update --check
a13n-envd update
# 锁定与客户端匹配的版本号、强制重装或切换构建变体：
a13n-envd update --version 1.2.3
a13n-envd update --version 1.2.3 --variant slim
```

macOS 升级后若提示录屏或辅助功能权限缺失，请按[升级后恢复 macOS 权限](computer-use.md#restore-macos-permissions-after-an-upgrade)处理。容器化运行请参阅[官方沙箱镜像](sandbox.md)（提供 `<version>`、`<version>-vm`、`<version>-slim-vm`、`<version>-desktop-vm` 标签）、[守护进程配置](configuration.md)与[安全隔离边界](isolation.md)。

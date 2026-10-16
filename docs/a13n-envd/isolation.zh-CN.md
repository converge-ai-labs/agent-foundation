---
title: 执行边界与故障排查
sidebarTitle: 执行边界
description: Envd 如何对会话应用身份、文件系统、进程和出站网络策略，以及如何排查失败。
---

Envd 会为每个会话 worker 应用可信的设备策略：原生执行身份、文件系统与进程 Sandbox，以及出站网络模式。命令、文件 RPC 和传输使用相同的边界；目录发现使用相同的授权和身份。Host 选择策略并管理外层容器或虚拟机。Envd 负责 worker 的准备、就绪检查和清理。

## 隔离行为

默认禁用 Sandbox，并继承网络配置，保留启动者的原生身份和权限，包括 root。受限 Sandbox 开放固定授权目录、最小只读系统视图，以及私有可写的 HOME、TMP 和状态目录。Windows 还可通过 `sandbox.full_disk_read: true` 启用广泛磁盘读取。它可以与继承、拒绝或[受控出站网络](egress.md)组合使用。选择 cwd 不会扩大授权或改变网络模式。各会话共享获准访问的文件；Envd 不会将会话彼此隔离。

不能逐条命令覆盖边界，也不会回退到更弱的执行方式。设备和会话描述符公开生效的 `boundary`、后端和不含秘密的策略摘要。只有完成必需的 worker 准备后才会发布会话；这不构成对外层部署的安全证明。

### 受限 Sandbox 示例

先创建所需目录，再启动：

```json
{
  "full_control": true,
  "default_working_directory": "/workspace",
  "sandbox": {
    "mode": "restricted",
    "grants": [
      {"path": "/workspace", "access": "read_write"},
      {"path": "/reference", "access": "read_only"}
    ]
  },
  "egress": {"mode": "deny"}
}
```

授权必须指向已存在的绝对目录，目录之间不能重叠。更改授权需要使用新授权重新启动守护进程，不能通过更新会话完成。允许空授权列表，但 cwd 仍必须在最终视图中可访问。

| 平台  | 受限 Sandbox                                                      | 网络                                                                                                                                   |
| ----- | ----------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| Linux | bubblewrap、私有进程与文件系统视图、移除 capabilities 并阻止提权  | 受限 Sandbox 下，如果用户命名空间可用，inherit/deny 可无 root 运行；禁用 Sandbox 时的 deny 以及 controlled 需要特权后端（root 启动者） |
| macOS | 通过 `sandbox-exec` 使用 Seatbelt、规范路径规则和可继承的进程限制 | 支持 inherit 或 deny；不支持 controlled                                                                                                |

macOS 不提供 Linux 挂载/PID 命名空间，也不声明 `no_new_privs`：`privilege_gain_blocked` 为 false。路径解析可能仍可见父目录元数据，原生进程启动也可读取根目录本身；两者都不允许读取未授权文件的内容。worker 运行期间请保持授权路径稳定。Linux 绑定已检查的目录句柄。不支持的组合会在准备阶段失败。

### 原生 Windows MXC

Windows 受限 Sandbox 使用实验性的 Microsoft eXecution Container（MXC）Process Security Environment 后端（`windows_mxc`），支持 inherit 或 deny 网络。Envd 使用所选网络能力和 peer 策略实际探测原生创建、启动信息准备和关闭操作。仅匹配 Windows 版本号不够：操作系统必须提供所选网络模式需要的 PSEC 能力。不支持的宿主机会失败，不自动降级。WSL 使用 Linux 后端。

默认运行时视图包含只读的 Windows、Program Files 目录和 Envd 可执行文件，以及私有可写的 HOME/USERPROFILE/TEMP/state。`sandbox.full_disk_read: true` 增加 Codex 风格的可用卷广泛读取权限，写入仍限制在可读写授权目录和 worker 私有状态内。这允许读取账户可访问的宿主配置和磁盘凭据；该策略约束写入，不为宿主文件提供保密边界。Envd 显式拒绝访问自身的启动配置、传输凭据和 broker 运行目录。该选项在 Envd 中默认为 false，Linux/macOS 拒绝启用；Harness UI 在 Windows 上默认启用。请保持授权目录的祖先路径稳定。worker 运行期间，Envd 保留已验证且不共享删除权限的目录句柄。PowerShell 启动使用私有桌面和只读注册表访问，不修改 ACL 或提权。`privilege_gain_blocked` 为 false，不声明 Linux 的 `no_new_privs` 保证。

广泛读取排除映射网络驱动器。每个 worker 在启动时固定卷根目录的读取视图；新挂载的本地卷或新建的卷顶层条目需要新开 Session 才能纳入。已有授权目录保留原有访问规则。

原生运行时兼容性取决于应用对 PSEC 的支持。已在广泛读取模式下验证 PowerShell、Git 和 Python；Node.js 24.21 的 `fs.realpathSync.native` 在 MXC 中可能报 `EPERM`。请在目标宿主机验证实际构建工具。

在安装了 Rust、Python、uv 和 PowerShell 的原生 Windows 机器上，从仓库根目录运行源码验收：

```powershell
cargo build -p a13n-envd --locked
cargo test -p a13n-envd native_mxc_probe --locked -- --ignored --nocapture
uv sync --locked --all-packages
$env:A13N_ENVD_TEST_BINARY = (Resolve-Path target/debug/a13n-envd.exe).Path
uv run --no-sync pytest packages/a13n-harness-ui/tests/test_windows_sandbox.py -v
```

原生探测测试验证 IPv4 回环、宿主机支持时的 IPv6 回环、inherit/deny 行为和未授权文件读取拒绝。显式启用的 UI 测试覆盖生产就绪状态、广泛读取与外部写入拒绝、broker 文件访问拒绝、只读 Project 和附件、可写 Thread tmp、文件列表/搜索/复制/移动/传输、junction 写入限制和不跟随链接的删除、私有环境、Session 清理、活动命令期间终止 broker 和授权目录替换拒绝。指定 binary 后，缺少 MXC 支持会导致测试失败。还应在目标 Windows 版本上验证取消 Session 打开；交叉编译无法验证这些内核生命周期。清理未确认时，worker 状态和活动进程计费保留至 Device 重启。

### 原生进程归属

| 平台    | 进程生命周期                                                               |
| ------- | -------------------------------------------------------------------------- |
| POSIX   | 管理进程组、限时终止，分别保留 stdout/stderr                               |
| Windows | 将挂起进程纳入所管理的 Job；退出、强制终止、超时或所有者丢失时清理后代进程 |

Windows Job 的进程管理不提供文件系统或网络隔离。支持的执行功能会报告实际可用能力；强制终止与优雅信号支持是不同功能。使用精确的可执行文件名，包括 Windows 上的 `.exe`。shell 配置属于受信任的启动配置，必须与所选 shell 匹配。命令接口不包含 PTY/ConPTY。

文件路径使用设备命名空间：POSIX 为 `/work/file`，Windows 为 `/C:/work/file` 或 `/UNC/server/share/file`。Windows 的 `/` 是用于发现卷的虚拟目录根节点，不能作为命令 cwd。禁用 Sandbox 时，文件系统权限、符号链接、链接和平台访问规则沿用执行账号的设置；原生 sudo 由现有 sudoers 授权。受限 worker 对每个路径实施平台的授权边界；受控 Linux worker 还使用私有命名空间。

## 在本仓库中验证

```bash
make local-envd-test
make eip-test

# Native macOS worker/grants/network/lifecycle integration:
cargo test -p a13n-envd --test sandbox_macos -- --nocapture
```

检查覆盖真实守护进程操作、设备与会话的消息帧处理、原生资源生命周期、各资源范围内的清理、文件传输和 provider 适配。平台专属测试必须在对应的原生操作系统上运行。本仓库的测试不验证部署的外部沙箱策略。

## 故障排查

- **找不到可执行文件**：向 `resolve_a13n_envd_executable()` 传入绝对路径，设置 Host 的 `A13N_ENVD_EXECUTABLE`，或将 `a13n-envd` 安装到 `PATH`。
- **版本不匹配**：使用版本完全匹配的原生守护进程和 Python 客户端。不要把发布版本与 EIP 线上协议版本混淆。
- **未知启动变量**：启动子进程前移除仅供 Host 使用的 `A13N_ENVD_*` 设置。只使用[配置](configuration.md)中列出的变量。
- **没有命令方法**：启用 Full Control，或配置可信可执行文件根目录或 shell 配置。然后检查 `available_methods`。
- **Sudo 失败**：Linux 受限 Sandbox 按设计阻止提权。禁用 Sandbox 时，`allow_sudo` 默认为 true，但镜像必须提供 sudo、预置执行账号和合适的 sudoers。检查外层 `no_new_privs` 和 `nosuid` 限制；Envd 无法覆盖它们。未设置 UID/GID 时，Linux 执行保留启动者身份，包括 root。使用[可信启动设置](configuration.md#native-identity-and-sudo)选择其他预置账号或禁止进一步提权；禁用提权不会降低已有 root 执行权限。
- **受控会话中的 root HTTPS 失败**：原生 sudo 可能丢弃会话的 CA 环境变量。按 sudoers 配置保留相关 CA 变量，或配置客户端信任库；参见[会话出站网络](egress.md)。
- **工作目录无效**：选择已存在的设备路径，而不是请求方 Host 上的路径。目录发现无需会话，可用于选择目录。
- **某个会话失败，其他会话正常**：检查该会话的结构化错误和原生执行证据。不要为了修复一个适配器而重启健康的共享设备。
- **传输通道断开**：尚未完成的操作结果可能不确定。显式重新附加到同一会话受代次和断线宽限期限制；绝不能自动重放修改操作。
- **文件消失**：正常关闭适配器既不会删除 cwd，也不会删除其他任何文件。检查 Host 的生命周期策略。
- **运行时锁冲突**：一个守护进程管理一个私有运行时目录。不同的启动实例需要独立目录。

## 参考资料

- [环境 provider 指南](../environments/index.md)
- [守护进程配置](configuration.md)
- [会话与输出生命周期](operations.md)
- [Python EIP 客户端](python-client.md)

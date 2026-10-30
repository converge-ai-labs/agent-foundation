---
title: 执行边界与故障排查
sidebarTitle: 执行边界
description: Envd 如何对会话应用身份、文件系统、进程和出站网络策略，以及如何排查失败。
---

启动 Envd 前，先选择执行账号、文件系统授权和网络模式。这些设备设置适用于每个会话的命令、文件操作和传输。目录发现使用相同的身份和授权。Host 管理外层账号、容器或虚拟机；Envd 准备并清理会话 worker。

## 隔离行为

先选择文件系统视图：

- **禁用 Sandbox**：使用执行账号的原生权限。默认账号为启动者，包括 root；网络默认继承。
- **受限 Sandbox**：开放固定授权目录、最小只读系统视图，以及私有可写的 HOME/TMP/state。Windows 还可通过 `sandbox.full_disk_read: true` 启用广泛磁盘读取。

再单独选择继承、拒绝或[受控出站网络](egress.md)，使用下方平台支持的组合。工作目录在该视图内提供默认路径，不会扩大授权或改变网络。各会话共享授权文件，因此彼此不信任的工作负载需要独立的外层部署。

检查设备和会话描述符中的有效 `boundary`、后端和不含秘密的策略摘要。已发布的会话已经完成必需的 worker 准备。Host 需要自行验证外层部署。更改边界需要新运行时；命令无法覆盖边界，不支持的组合会在准备阶段失败，不会回退到更弱的边界。

### 受限 Sandbox 示例

此示例允许命令读写 `/workspace`、只读访问 `/reference`，并禁止外部网络路由。

1. 在设备上创建 `/workspace` 和 `/reference`。
2. 将以下内容保存为守护进程的 JSON 配置。
3. 按[配置步骤](configuration.md#minimal-standalone-configuration)使用该文件启动 Envd，然后在 `/workspace` 打开会话。

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

授权使用已存在的绝对目录，且目录之间不能重叠。更改授权时重新启动守护进程。只要选定的 cwd 在最终视图中可访问，授权列表就可以为空。

| 平台  | 受限 Sandbox                                                      | 网络                                                                                                                                   |
| ----- | ----------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| Linux | bubblewrap、私有进程与文件系统视图、移除 capabilities 并阻止提权  | 受限 Sandbox 下，如果用户命名空间可用，inherit/deny 可无 root 运行；禁用 Sandbox 时的 deny 以及 controlled 需要特权后端（root 启动者） |
| macOS | 通过 `sandbox-exec` 使用 Seatbelt、规范路径规则和可继承的进程限制 | 支持 inherit 或 deny；不支持 controlled                                                                                                |

在 macOS 上，worker 运行期间应保持授权路径稳定。Seatbelt 允许读取路径解析需要的父目录元数据和原生启动需要的根目录；未授权文件内容仍被拒绝。它既不提供 Linux 挂载/PID 命名空间，也不提供 `no_new_privs`，因此 `privilege_gain_blocked` 为 false。Linux 绑定已检查的目录句柄。

### 原生 Windows MXC

Windows 受限 Sandbox 使用实验性的 Microsoft eXecution Container（MXC）Process Security Environment 后端，描述符报告为 `windows_mxc`。它支持 inherit 和 deny 网络。Envd 使用所选网络能力和 peer 策略探测原生创建、启动信息准备和关闭操作。应使用能通过探测的宿主机；Windows 版本号本身不足以证明 PSEC 支持。WSL 使用 Linux 后端。

默认视图包含只读的 Windows、Program Files 目录和 Envd 可执行文件，以及私有可写的 HOME/USERPROFILE/TEMP/state。要允许更广泛的读取，设置 `sandbox.full_disk_read: true`。写入仍限制在可读写授权目录和 worker 私有状态内，但命令可以读取账号有权访问的宿主配置和磁盘凭据。只有确实需要这类读取权限时才选择该选项。Envd 仍会拒绝访问自身的启动配置、传输凭据和 broker 运行目录。

广泛读取在 Envd 中默认为 false，在 Windows 上的 Harness UI 中默认为 true。Linux 和 macOS 拒绝此选项。映射网络驱动器不在广泛读取范围内。卷根目录的读取视图在 worker 启动时固定；要纳入新挂载的本地卷或新建的顶层条目，需要打开新会话。已有授权目录保留原有访问规则。

保持授权目录的祖先路径稳定。Envd 在 worker 存续期间保留已检查且不共享删除权限的目录句柄。PowerShell 启动使用私有桌面和只读注册表访问，不修改 ACL 或提权。描述符报告 `privilege_gain_blocked: false`；MXC 不提供 Linux 的 `no_new_privs` 保证。

在目标宿主机上验证将要使用的构建工具。原生验收测试在广泛读取模式下运行 PowerShell、Git 和 Python。Node.js 24.21 的 `fs.realpathSync.native` 在 MXC 中可能报 `EPERM`。

进行源码验收时，在原生 Windows 宿主机安装 Rust、Python、uv、Git 和 PowerShell，再从仓库根目录运行以下命令：

```powershell
cargo build -p a13n-envd --locked
cargo test -p a13n-envd native_mxc_probe --locked -- --ignored --nocapture
uv sync --locked --all-packages
$env:A13N_ENVD_TEST_BINARY = (Resolve-Path target/debug/a13n-envd.exe).Path
uv run --no-sync pytest packages/a13n-harness-ui/tests/test_windows_sandbox.py -v
```

原生探测检查 inherit/deny 网络、IPv4 回环、可用时的 IPv6 回环，以及未授权文件读取拒绝。UI 测试检查就绪状态、广泛读取与受限写入、受保护的 broker 文件、Project 和 Thread 文件权限、文件操作以及会话和进程清理。测试还覆盖 junction 限制、不跟随链接的删除、私有环境、命令运行期间 broker 丢失和授权目录被替换。指定 binary 后，缺少 MXC 支持会导致测试失败。

还应在目标 Windows 版本上验证取消会话打开。交叉编译无法验证内核清理行为。清理未确认时，worker 状态和活动进程用量继续保留，直到设备重启。

### 原生进程归属

| 平台    | 进程生命周期                                                               |
| ------- | -------------------------------------------------------------------------- |
| POSIX   | 管理进程组、限时终止，分别保留 stdout/stderr                               |
| Windows | 将挂起进程纳入所管理的 Job；退出、强制终止、超时或所有者丢失时清理后代进程 |

根据声明的执行功能选择进程控制方式：强制终止和优雅信号是不同能力。Windows Job 管理进程清理；文件系统和网络隔离由选定的 Sandbox 与出站网络策略提供。使用精确的可执行文件名，包括 Windows 上的 `.exe`，并为所选 shell 配置匹配的 shell profile。命令接口不支持 PTY/ConPTY。

使用设备命名空间寻址文件：POSIX 上为 `/work/file`，Windows 上为 `/C:/work/file` 或 `/UNC/server/share/file`。Windows 的 `/` 用于列出卷以发现目录；命令 cwd 应选择实际目录。

禁用 Sandbox 时，文件系统权限、链接和原生 sudo 遵循执行账号和现有 sudoers。受限 worker 对每个路径实施平台授权。受控 Linux worker 还使用私有命名空间。

## 在本仓库中验证

在已安装仓库开发依赖的源码检出中运行：

```bash
make local-envd-test
make eip-test

# Native macOS worker/grants/network/lifecycle integration:
cargo test -p a13n-envd --test sandbox_macos -- --nocapture
```

这些检查覆盖守护进程操作、设备与会话的消息帧处理、资源生命周期、清理、传输和 provider 适配。平台专属测试应在对应的原生操作系统上运行。部署的外层沙箱策略需要单独验证。

## 故障排查

先查看失败会话的结构化错误，再检查相应设置：

- **找不到可执行文件**：通过 `resolve_a13n_envd_executable()` 选择绝对路径，设置 Host 的 `A13N_ENVD_EXECUTABLE`，或将 `a13n-envd` 安装到 `PATH`。
- **版本不匹配**：让原生守护进程和 Python 客户端的发布版本完全一致。EIP 线上协议版本是另一个标识。
- **未知启动变量**：从子进程环境中移除仅供 Host 使用的 `A13N_ENVD_*` 设置，传入[配置](configuration.md)中列出的变量。
- **没有命令方法**：启用 Full Control，或配置可信可执行文件根目录或 shell profile，再检查 `available_methods`。
- **Sudo 失败**：先检查选定的 Sandbox。Linux 受限 Sandbox 阻止提权。禁用 Sandbox 时，`allow_sudo` 默认为 true，仍需要已安装的 sudo、预置账号和合适的 sudoers。外层 `no_new_privs` 或 `nosuid` 限制仍然生效。使用[原生身份设置](configuration.md#native-identity-and-sudo)选择账号；未设置 UID/GID 时，保留启动者身份，包括 root。禁止进一步提权不会降低 root 权限。
- **受控会话中的 root HTTPS 失败**：检查 sudo 是否移除了会话 CA 变量。按 sudoers 保留这些变量，或配置客户端信任库；参见[会话出站网络](egress.md)。
- **工作目录无效**：通过目录发现选择已存在的设备路径。打开会话前即可使用目录发现。
- **某个会话失败，其他会话正常**：保持共享设备运行，检查该会话的错误和原生执行证据。
- **传输通道断开**：使用原操作证据核对待完成的副作用。重新附加到同一会话需要原代次和尚未过期的宽限期；这不代表允许重放修改操作。
- **文件消失**：检查 Host 的生命周期策略。正常关闭适配器会保留 cwd 和工作区文件。
- **运行时锁冲突**：为每次守护进程启动分配不同的私有运行时目录。

## 参考资料

- [环境 provider 指南](../environments/index.md)
- [守护进程配置](configuration.md)
- [会话与输出生命周期](operations.md)
- [Python EIP 客户端](python-client.md)

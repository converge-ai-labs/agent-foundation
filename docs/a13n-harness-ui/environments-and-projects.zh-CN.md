---
title: 运行环境与项目
description: 组织多根目录代码项目，在 Full Control 与 Envd 操作系统沙箱间切换，并将远程 Envd 设备绑定到 Harness UI。
---

**Environment** 决定智能体在哪里读写文件和执行命令；**Project** 用于归类对话并提供默认工作目录与初始配置。

## 选择运行环境

在项目目录中以任意一种执行模式启动 Harness UI：

```console tab="Full Control"
a13n-harness-ui --environment-mode full-control
```

```console tab="Sandbox"
a13n-harness-ui --environment-mode sandbox
```

- 在 TUI 中使用 `/environment`，或在 WebUI 输入框打开 **Environments** 为后续轮次切换模式。
- 使用 `--environment-profile <id>` 选择自定义配置集（通过 `a13n-harness-ui environment list --format json` 查看）。

## 执行权限

| 内置配置集            | 运行时与权限范围                                     |
| --------------------- | ---------------------------------------------------- |
| `environment-native`  | 直接以宿主机账号运行（无需 Envd 守护进程）           |
| `environment-envd`    | 通过本地 Envd 以 Full Control 运行（关闭计算机使用） |
| `environment-desktop` | 通过本地 Envd 以 Full Control 运行并启用计算机使用   |
| `environment-sandbox` | Envd 操作系统沙箱隔离文件与进程（继承宿主机网络）    |

- **Full Control** 继承当前终端的 `PATH`、已导出环境变量与账号权限。
- **Sandbox** 将写入限制在项目根目录与线程 `tmp/` 内；Linux 与 macOS 同时限制读取，Windows（实验性 MXC）限制写入但允许较宽的本地读取。沙箱就绪失败会明确报错，绝不会静默降级为 Full Control。各平台边界详见 [Envd 隔离](../a13n-envd/isolation.md)。

### 管理本地 envd

在 **Settings → Environments → Local runtime**（或通过 CLI）安装、启动、检查或停止 Sandbox 与本地计算机使用所需的托管 Envd：

```console
a13n-harness-ui environment status
a13n-harness-ui environment check --profile environment-envd
a13n-harness-ui environment configure --profile environment-envd
```

如需启用本地计算机使用，在 **Settings → Environments** 开启 **Desktop capability**，重启 Harness UI 后在宿主机执行 **Check permissions**。

## 项目文件参考

如需管理多个目录或复用默认配置，在根配置旁创建 `projects/work.yaml`：

```yaml title="projects/work.yaml"
schema_version: "1"
kind: project
id: project-work
name: Work
position: 0
roots:
  - path: /absolute/path/to/code
  - path: /absolute/path/to/notes
```

运行 `a13n-harness-ui config validate` 校验。首个目录同时作为 TUI 的启动入口与默认工作目录。

### 新对话默认值

在项目文件中添加 `defaults`（或在 WebUI 的 **Project settings** 中编辑）：

```yaml
defaults:
  agent: agent-reviewer
  environment_profile: environment-native
  harness_plugins: []
  environment_run_extensions: []
  mcp_servers: []
```

新对话优先级：显式启动/界面选择 → 项目默认值 → 智能体默认值 → 根 YAML 默认值。已有对话保持原配置，除非你显式点击 **Apply Project defaults**。

## 添加设备绑定

通过 Envd 接入另一台计算机，让智能体直接操作远程文件与命令。

### 连接并批准 Envd

1. 在远程计算机上运行：

```console
a13n-envd connect https://your-webui-host.example.com --host work
```

2. 在 WebUI 中打开打印的审批链接并确认验证码。
3. 在 **Project settings → Environments**（或 **Conversation details → Configuration**）中挂载已批准的设备，选择工作目录并设置允许的操作。

如需手动定义 HTTP 或反向 WebSocket 设备，可在 `devices/*.yaml` 中使用凭据引用（切勿写入明文 Token）。

### 撤销设备

在 **Settings → Environments → Devices** 中点击 **Revoke** 可立即吊销设备凭据，阻止后续连接；移除本地配置仅删除保存的条目，不会影响远程文件。

## 自定义环境配置集

在 `environment_profiles/*.yaml` 中定义自定义配置集（包含 `schema_version: "1"`、`kind: environment_profile`、`id` 与 `name`），即可接入云沙箱提供方（Docker、E2B、Modal、Daytona、Vercel、Sprites）或自定义 Envd 出站与隔离策略。各提供方配方见[运行环境概览](../environments/index.md)。

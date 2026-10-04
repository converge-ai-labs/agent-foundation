---
title: 操作桌面电脑
description: 让 agent 通过 Envd 查看并操作共享的 macOS、Linux X11 或 Windows 桌面。
---

在 Harness UI 线程中使用 macOS、Linux X11 或 Windows 共享桌面。Envd 直接向模型发送截图，并执行有界的输入操作。WebUI 随工具结果显示截图。守护进程通过反向 WebSocket 向外连接；桌面电脑无需入站监听器。

## 要求与限制

- 在已登录的 macOS 或 Linux X11 图形会话中运行 Envd。Linux 需要已有 X11 服务器，支持 RandR 1.5、XKB 和 XTEST，具有 TrueColor 根 visual，并设置正确的 `DISPLAY` 和 X11 身份验证。不支持 Wayland 和 XWayland。
- 在 macOS 系统设置中，向 macOS 为 Envd 识别的进程或启动者授予屏幕录制和辅助功能权限。修改权限后，按 macOS 要求重启该进程。守护进程报告缺失权限，不会自行授予。
- Windows 上，在预期的已登录、未锁定交互用户会话中运行 Envd。不支持服务、断开的会话、登录/UAC 安全桌面和更高完整性级别的目标。
- 使用禁用的 Sandbox 和继承的出站网络。受限 Sandbox 和拒绝/受控出站网络会拒绝启用电脑操作，不会跨越所选边界控制宿主桌面。
- 选择具有 `dynamic_environment` 工具的 agent 和接受图像的模型。

这是实际共享桌面，不是私有浏览器或虚拟机。输入可以利用已登录用户的权限发送消息、修改文件或调用其他应用。所选工作目录不限制这些效果。截图和点击之间，用户或其他 agent 可能改变焦点或内容。不要无人看管地操作敏感桌面。

## 连接桌面

在 WebUI 中打开**设置 → 环境 → 连接设备**。在桌面的图形会话中运行显示的命令，添加显式启用选项：

```console
a13n-envd connect https://your-harness-ui.example.com --computer-use true
```

使用实际可访问的 WebUI origin。也支持同机回环 HTTP origin；远程 Host 使用 HTTPS。核对验证码，然后在 WebUI 中批准。保持守护进程运行。可以使用相同选项重启已配对的连接；这不会创建新的设备注册。

等效设置是守护进程 JSON 中的 `"computer_use": true` 或 `A13N_ENVD_COMPUTER_USE=true`。该功能默认关闭，独立于 `full_control`。启用电脑操作不会启用 shell 命令，启用 shell 命令也不会启用电脑操作。遵循普通配置优先级；`--computer-use false` 覆盖环境变量中的启用设置。

### 等待 macOS 授权

Host 配对后，Envd 检查**屏幕录制** 和**辅助功能**，请求缺失权限，并等待最多 **120 秒** 。在**系统设置 → 隐私与安全性** 中，向 macOS 为 Envd 识别的进程或启动者授予访问。只有两项检查都成功后，守护进程才启动反向 WebSocket。同样的检查也在 HTTP 监听器和 stdio 协议处理前执行；禁用电脑操作时不会弹窗或等待。

进度和错误写入 stderr，绝不写入 stdio 协议 stdout。超时或 Ctrl+C 会以非零状态退出，不会连接只获部分授权的设备。如果 macOS 要求重启，重启识别出的启动者和 Envd，再次执行命令。拒绝或关闭弹窗不一定能与仍在等待授权区分；Envd 会等待到期限，并报告仍缺失的权限。即使启动成功，运行时撤销权限仍会使观测或输入失败。

需要更长等待时，设置 `A13N_ENVD_COMPUTER_USE_PERMISSION_TIMEOUT_MS=300000`，或在守护进程 JSON 中添加 `"computer_use_permission_timeout_ms": 300000`。这个正数时长本身不会启用电脑操作。

父进程启动 stdio Envd 时，在启动阶段转发或消费 stderr，并将客户端 `initialization_timeout` 设置为大于权限等待加启动开销的时间（默认等待可用 135 秒）。Python 客户端默认的 10 秒初始化超时不是人工授权超时。反向 WebSocket 客户端只在 Envd 连接后初始化；HTTP 客户端必须等到监听器可用。

### Linux X11 就绪检查

在预期 X11 会话的终端中启动，使 Envd 继承 `DISPLAY` 和已设置的 `XAUTHORITY`。它在打开任何 EIP 传输前检查经过身份验证的 X11 访问和必需扩展。它不会启动 Xorg/Xvfb、修改 `xhost` 规则、挂载宿主 socket 或请求提权的输入设备访问。不要为了修复连接失败而禁用 X11 身份验证。

Linux 支持截图、点击、移动、拖动、物理组合键和离散滚轮步数。使用 `computer_describe` 检查 `scroll_units`，再传入 `unit="steps"`，每轴最多 100 步。像素滚动在移动指针前被拒绝。Linux **不提供** `computer_type_text`；不会修改键盘映射或剪贴板来模拟 Unicode 文本输入。物理组合键取决于当前键盘布局，不能替代字面文本输入。`meta` 表示 Super，`alt` 表示 Alt。

X 服务器丢失后需要新会话和新截图；Envd 不会悄悄将旧几何引用连接到替代服务器。Wayland 启动环境或声明 XWAYLAND 的服务器会被拒绝，即使同时设置了 `DISPLAY`。

### Windows 就绪检查与输入

从要控制的桌面上的普通终端启动 Envd。开始任何 EIP 传输前，它会检查当前 Windows 会话是否活跃，以及其桌面是否接收输入。此路径没有通用 Windows 桌面权限弹窗。保持 UAC 和其他 Windows 保护启用；不要仅为消除错误就以管理员运行 Envd。配对和 Harness 操作上限独立于操作系统桌面访问。

Windows 支持截图、指针手势、物理扫描码组合键、字面 Unicode 文本和滚轮步数。检查 `computer_describe` 后使用 `unit="steps"`；像素滚动会在移动指针前被拒绝。`meta` 是 Windows 键。即使启用显示缩放，截图和输入坐标仍使用物理像素。受保护或被排除的内容可能在截图中显示为黑色。

文本使用 UTF-16 Unicode 输入事件，不替换剪贴板，也不猜测键盘布局。CRLF 对作为单个回车提交，避免重复换行。使用原始键盘输入的应用可能不接受 Unicode 输入，单独换行或 Tab 的行为也取决于目标控件。输入后检查应用。所需按键、按钮或文本修饰键已被按住时，会在分派前产生冲突；请让用户释放它们，不要清空用户输入状态。

桌面锁定或断开时，手动重连或解锁，并获取新观测。输入失败本身不能证明是 UIPI 导致，原生成功计数也不能证明应用已接受输入。部分执行或结果不明、传输丢失和清理失败都要求先检查，再执行下一步，不能自动重放。

## 将桌面绑定到线程

1. 在输入区打开**环境**，或在**对话详情 → 配置** 中设置已保存的下一次执行配置。
2. 为已连接桌面添加环境。
3. 选择已有工作目录。
4. 将别名命名为 `desktop`。
5. 保留**允许操作** 下默认的**完全控制**（这是绑定的操作预设，不是守护进程的 `full_control` 设置）。该预设包含文件访问、命令执行，以及设备已启用的桌面观测/控制。**只读**允许文件读取和浏览，不允许修改、命令或桌面访问。旧绑定请显式选择 **完全控制** ，替换原操作上限。
6. 明确选择默认环境，或要求 agent 显式使用别名 `desktop`。
7. 保存所在选择配置，并开始新的执行。

配对只批准设备连接，不会让模型使用桌面；模型能否使用桌面由绑定的允许操作决定。修改下一次执行配置不会改变正在进行的操作。

已保存的项目或线程绑定可以将访问收窄到仅观测：

```yaml
environment_bindings:
  - device_id: device-your-mac
    alias: desktop
    working_directory: /Users/your-account
    permission_ceiling:
      operations:
        - environment.computer.describe
        - environment.computer.observe
default_environment: desktop
```

使用实际配置的 ID 和路径。只有确实需要控制时，才添加六项输入操作（`environment.computer.*` 下的 `click`、`move`、`drag`、`scroll`、`type_text`、`press_keys`）。除非选择替代预设，编辑器会保留已有的精确上限。

## 观测、操作、验证

先尝试范围有限的提示：

> 使用 desktop 环境。描述它的显示器并截取屏幕。暂时不要点击或输入。

模型直接收到图像字节，无需先保存再查看。展开**观测桌面** 工具结果查看截图；打开可获取更大预览。图像是历史证据，不是实时桌面查看器。显示副本保存在该线程的临时文件中，可能按 scratch 保留规则过期。模型续接存储有独立生命周期。显示副本失败绝不会触发再次截图或重复输入。

需要控制时，让 agent 先观测，使用返回的 `observation_id` 和图像像素坐标操作，再次观测以验证。指针引用限定于执行/会话并会过期；引用过旧或布局变化需要新观测。键盘和文本输入使用当前前台焦点，因此输入前先点击目标字段。

支持的工具：

| 工具                              | 用途                                                |
| --------------------------------- | --------------------------------------------------- |
| `computer_describe`               | 列出显示器和原生权限就绪状态                        |
| `computer_observe`                | 截取显示器（默认主显示器），最大尺寸 256–2048 像素  |
| `computer_click`, `computer_move` | 使用返回图像中的位置                                |
| `computer_drag`                   | 完成有界拖动并释放按钮                              |
| `computer_scroll`                 | 按声明的单位滚动；正值表示向右/向下                 |
| `computer_type_text`              | 在当前焦点输入字面 Unicode 文本（macOS 和 Windows） |
| `computer_press_keys`             | 按下并释放组合键，例如 `["meta", "a"]`              |

键名中，`meta` 表示 Command/Super/Windows，`alt` 表示 Option/Alt，还支持 `enter`、`page_up` 和 `page_down`。输入结果报告原生事件效果和清理状态。`executed` 不能证明应用层成功。部分执行或结果不明、释放不完整、断开或执行中断都不能盲目重试；检查桌面后再决定下一步。

截图存储与文件传输共享暂存字节和对象预算。每次截图开始前需要 4 MiB 空闲暂存容量；截图后，仅实际编码图像大小继续占用预算，直到字节释放。及时关闭 reader，避免累积未读取截图。

## Python 客户端

对于已就绪的 `EIPSession`，高层 reader 使用与文件下载相同的有界、SHA-256 验证的原始传输机制：

```python
async with session.observe_computer(max_dimension=1280) as reader:
    screenshot = b"".join([chunk async for chunk in reader])
    observation = reader.opened.observation
# The image reader is closed, while the Session's geometry reference remains usable.
```

输入使用生成的类型化 `computer_*` 客户端调用，并创建新操作上下文。不要用新操作 ID 重试结果不确定的动作。会话构建和传输归属见 [Python EIP 客户端](python-client.md)。

## 验证

可选的 [Linux 桌面 fixture](https://github.com/converge-ai-labs/agent-foundation/tree/main/dev/fixtures/linux-desktop) 在一个临时非 root 容器中运行 Xvfb、Openbox、真实 Tk 应用、原生 Envd、反向 WebSocket 和 Harness UI。无需挂载宿主显示或发布端口。它验证真实 GUI 事件和截图像素变化，不会返回脚本预设的桌面图像。

可移植集成测试通过真实反向 WebSocket 使用脚本化桌面对端，连接真实客户端、Harness、App、实时/历史数据和经身份验证的图像读取。它们不能证明原生 macOS 或 Windows 截图/输入、Retina 或混合 DPI 几何、操作系统权限行为或事件投递。请在 Mac 上验证 macOS 行为：先描述和截图，再在临时文本文档中执行无害输入，检查下一张截图，并测试权限拒绝和显示布局变化。这些步骤不覆盖 Windows 原生行为。不提供生产环境的假桌面模式。

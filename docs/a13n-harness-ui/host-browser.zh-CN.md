---
title: 使用 Host 浏览器
description: 准备 WebUI Host 上的浏览器自动化，与 Agent 浏览网页，并截图或标注。
---

当 Agent 需要操作网站，或你想一起检查网页时，使用 Host 浏览器。先[启动 WebUI](webui.md#start-the-server)。这个浏览器运行在服务器 Host 上，与显示 WebUI 的浏览器分开。

在 Agent 设置中选择**浏览器模式**。标准模式使用单个浏览器工具。可编程模式通过 CodeAct 执行所有浏览器操作。选择“关闭”可禁用该 Agent 的浏览器工具。两种启用模式都需要完全控制权限，并会自动提供对应的内置技能。

## 使用 Host 浏览器

**Browser** 让 Agent 在服务器上使用 Chromium，你可以观看原生窗口。Linux 显示共享的专用桌面，macOS 只显示当前对话拥有的浏览器窗口。浏览器需要 Linux 或 macOS Host、计算机共享和 Full Control Run。Windows 浏览器可以查看支持的 Host，但 Windows 不能承载此桌面。

### 首次设置

1. 在服务器[安装 Host 组件](#install-host-components)，不是在显示 WebUI 的设备上安装。启动 WebUI 时保持计算机共享开启。
2. 使用默认的 `browser.mode: auto` 启动 WebUI。Harness UI 自动探测已安装组件并准备共享浏览器。在 **Settings → Environments → Browser** 查看运行状态和组件。启动失败时，解决错误后点击 **Retry browser startup**。
3. 开始 **Full Control** 对话，让 Agent 浏览。Run 接纳时浏览器已就绪，才会获得浏览器工具；首次导航或创建标签页时打开对话窗口。也可在 Run 运行期间选择 **Browser → Open browser** 手动打开。
4. 首次实时浏览会展示查看器，不移动键盘焦点。已打开 Files/Changes 或使用窄屏时，选择 **View browser**。关闭查看器保留浏览器运行，同一 Run 不再自动展示。
5. Linux 上登录或手动修正时，选择 **Take control**，完成后 **Hand back to Agent**。macOS 上选择 **Pause automation**，在 Mac 本机操作浏览器，再选择 **Resume automation**。

浏览器无需对话级开关或手动添加 Capability。**Settings → Environments → Browser** 提供全局 Auto/Disabled 模式和程序路径覆盖；修改后保存并重启。普通聊天、Files 和 Shell 不创建对话浏览器页面。浏览器启动不会阻塞 Run。浏览器不可用时，Agent 会收到原因，并在没有浏览器工具的情况下继续。恢复后，在后续 Run 使用浏览器；不会向当前 Run 追加工具。

### 安装 Host 组件

**Linux**：按发行版或供应商说明安装 Chrome、Chromium 或 Edge。Debian/Ubuntu 可这样准备 TigerVNC：

```bash
sudo apt-get update
sudo apt-get install --no-install-recommends tigervnc-standalone-server
```

可选的窗口控制和 TigerVNC 尺寸调整组件：

```bash
sudo apt-get install --no-install-recommends openbox x11-xserver-utils
```

这些命令安装桌面组件，不安装 Chromium。Harness 包含 cdp-use，并生成 VNC 密码和 X11 认证，无需另行设置 Node.js、`vncpasswd`、`xauth` 或 websockify。Docker 部署应在服务器镜像/容器中安装。**Manual installation commands** 是供你执行的说明，不由 UI 自动安装。

以非 root 账户运行并保持 Chromium 沙箱开启。root 需要显式 `--browser-no-sandbox` 或 `A13N_HARNESS_UI_BROWSER_NO_SANDBOX=1`，这会移除 Chromium 沙箱保护。`--browser-sandbox` 覆盖该环境变量选择。WayVNC/Sway 本身仍要求非 root 账户。

**macOS：**

1. 安装 Chrome、Chromium 或 Edge，以已登录桌面的用户运行 Harness UI。
2. 选择 **Browser → Open browser** 打开窗口；Chromium 是唯一必需的浏览器组件。
3. 要在 macOS 14 或更高版本使用可选预览，给运行 Harness UI 的应用授予屏幕录制权限。**Open Mac Screen Recording settings** 打开 Host 设置，由你在那里授权。
4. 保持 Mac 解锁且浏览器窗口可见。授权或解锁后选择 **Reconnect**；如 macOS 要求重启 Host 应用，按提示操作。

预览只捕获 Harness UI 为当前对话创建的那个窗口，包括真实地址栏。缺少权限或捕获失败时，自动化仍可使用。查看器始终只读，不转发键盘、鼠标或剪贴板输入。无需屏幕共享或 VNC 密码；以前保存的 VNC 凭据和旧密码环境变量已不再使用。

捕获不会回退到桌面、个人浏览器或其他对话窗口。独立弹出窗口和原生对话框不在预览中。如果绑定窗口已关闭或无法识别，使用 **Reset browser** 建立新预览。可用本地缩放；Viewport 预设禁用，物理显示分辨率保持不变。

### 探测并选择可执行文件

自动选择已安装浏览器，优先 Chrome，再 Chromium，最后 Edge。Linux 搜索服务器 `PATH`，macOS 检查 `/Applications` 和 `~/Applications`。安装缺失程序后点击 **Scan** 或 **Retry browser startup**。更改服务器 `PATH` 后重启服务器。

| Linux 显示方案 | 必需组件                             | 尺寸调整               |
| -------------- | ------------------------------------ | ---------------------- |
| TigerVNC，优先 | `Xtigervnc` 或 `Xvnc`                | 可选 RandR/xrandr 支持 |
| x11vnc         | x11vnc 和 Xvfb                       | 固定桌面               |
| WayVNC         | 兼容 WayVNC、无头 Sway、非 root 账户 | 固定桌面               |

自动探测按此顺序优先选择完整适配器。WayVNC 需要私有 Unix socket 和禁用输入的只读支持。Linux 桌面使用专用显示/运行目录，不连接用户当前桌面。Openbox、Fluxbox、IceWM 或 twm 等 X11 窗口管理器提供拖动和关闭控件；没有也能基本浏览。

覆盖路径时，打开 **Settings → Environments → Browser**，选择探测到的程序，或在 **Other** 输入绝对路径，再保存并重启。**Automatic** 清除覆盖。无效显式路径直接失败，不回退。**Scan** 检查当前草稿并刷新必需/可选组件结果，不保存也不启动。探测成功后仍可能因缺少共享库或显示权限而启动失败。

**Active configuration** 显示当前应用使用的配置。已保存的浏览器修改在重启 Harness UI 后生效。重试使用当前生效的配置，不使用待重启的已保存修改或未保存草稿，也不会打开对话页面。

macOS 设置显示可选的 **Window preview**，不提供 VNC 程序选择器或密码。

共享 profile 位于 `<data_root>/browsers/profile`，默认是 `~/.a13n-harness-ui/data/browsers/profile`，没有独立位置设置。各对话和 Project 共用一个 Chromium 实例及 profile，并有各自窗口/Agent 标签页。不导入个人 profile，也不移动或合并已有 profile 目录。

Linux 受管浏览器使用基础密码存储，避免等待锁定的桌面钥匙环。保存的密码和 Cookie **不受操作系统钥匙环保护**；请像保护凭据一样保护 profile 及备份。系统钥匙环和 macOS 凭据存储不变。升级后重启 Harness UI 应用此行为；profile 会保留，但原先依赖钥匙环加密登录数据的网站可能需要重新登录。

### 启动与控制浏览器

初始只读观看。Linux 上的 **Take control** 暂停所有对话的浏览器工具和页面采集，并聚焦当前对话窗口。一次只有一个查看者控制输入。**Hand back to Agent** 撤销人类输入，并要求重新观察页面。你可以从其他对话交还，包括没有打开标签页的对话。从其他对话接管会转移输入权限并聚焦其窗口，同时保持所有 Agent 浏览器工具暂停。

每次模型请求都会收到简短的 Browser 可用性上下文。接管期间，Agent 可以调用 `browser_notify_on_handback` 登记提醒，无需访问页面或等待。受阻的页面工具调用也会登记同一提醒，重复请求会合并。如果控制权已经交还，工具直接报告可用，不再登记未来提醒。Agent 可以先完成独立工作，或结束当前轮次，无需轮询。交还只通知这些对话，包括已经结束轮次的对话，让 Agent 重新观察并继续仍相关的工作。待审批操作仍需你的决定。停止、归档或清空上下文会取消待发通知。通知只保留在当前服务器和浏览器运行时中；不会恢复已经结束的 Goal。

要更换设备，在另一个浏览器打开同一 Thread，选择 **Take control here**。原控制端失去输入权限，Agent 全程保持暂停；仅打开 Thread 仍为只读。控制端断线后，选择 **Reconnect control** 可在保持 Agent 暂停的同时重连，或选择 **Hand back to Agent** 恢复自动化。

工具栏先显示 **Taking control…**，等待当前操作结束，再显示 **Connecting control…** 和 **Waiting for the first frame…**。出现 **You have control** 后再输入或点击。连接期间可能保留旧画面并显示等待提示；连接超时可重试，等待或失败期间也可交还控制。

macOS 上，在 Mac 本机操作真实浏览器前，先选择 **Pause automation**。这同样会暂停各对话的浏览器工具和页面采集。完成后选择 **Resume automation**；Agent 必须先观察更新后的页面，再继续操作。关闭查看器不会解除暂停。WebUI 预览始终只读。

Linux 上输入前点击远端桌面。本地输入法在你的设备上组合文字，再把确定文字粘贴到远端字段。字段需支持粘贴，Host 也需有相应字体。Linux Unicode 传输需要 TigerVNC 或 WayVNC 等扩展剪贴板服务；已测试的 x11vnc 不支持。

Linux 人工控制期间，聚焦桌面的文本剪贴板同步最多 65,536 字符，需要 HTTPS 或 localhost 及浏览器剪贴板权限。客户端 Command 快捷键映射为 Linux Ctrl 快捷键。交还、断线或关闭查看器停止同步。远端桥接只传文字，不传图片/文件；文件对话框使用 Host 文件。

Linux 支持尺寸调整时，接管后选择 Viewport 预设检查响应式布局。尺寸表示页面 CSS 视口，其他查看者也看到相同大小。调整保持登录且不重载，不模拟手机操作系统、user agent 或触摸设备。Close conversation tabs 和 Reset 保留选择直到服务器重启。

| 操作                                          | 保留状态                                         |
| --------------------------------------------- | ------------------------------------------------ |
| 关闭抽屉、切换对话、结束 Run                  | 浏览器继续运行                                   |
| **Browser options → Close conversation tabs** | 关闭当前窗口，保留 Run 绑定；同一 Run 可再次打开 |
| **Reset browser**                             | 在可见位置重建当前对话窗口                       |
| **Reconnect**                                 | 重连查看器，不重启 Chromium                      |
| 关闭对话最后一个标签页                        | 绑定仍启用；Agent 导航或 Open browser 可新建     |
| 退出 Chromium 或必需显示进程退出              | 停止共享运行时和全部窗口                         |

Reset 或故障恢复创建新的只读会话。共享运行时停止后，选择 **Try again** 重试。发送新提示让受影响对话使用替代窗口；不重放旧动作，等待中的工具会失败而非无限暂停。单对话 Reset 保留其他对话窗口。崩溃可能丢失未保存页面数据。服务器重启后，Auto 模式重新准备共享浏览器。人工控制期间关闭单个标签页后，自动化仍暂停，直到交还。如果当前对话持有控制权，**Close conversation tabs** 也会释放人工控制。

Linux 人工控制者可以查看和操作共享专用桌面的其他窗口。macOS 预览只显示一个窗口，但各对话仍通过 Chromium profile 共享网站登录状态、保存的密码及偏好。浏览器权限和网络属于 Host，不受 Sandbox 隔离。Linux 查看器使用 noVNC，源码/许可证说明位于 `/assets/LICENSE.noVNC`，其他打包依赖说明位于 `/third-party-licenses.txt`。

### 截图与标注

1. 在已连接 Browser 抽屉中点击相机 **Capture and annotate screenshot**。它在本机冻结当前渲染的 Linux 桌面或 macOS 窗口预览，包括浏览器控件；实时浏览器继续运行。
2. 用鼠标、触控笔或触摸裁剪，添加箭头、形状、自由笔迹、文字或马赛克。隐私内容用 **Opaque redaction**；马赛克只是视觉效果。
3. 用 **Select** 或 **Annotations** 选择标注。移动/调整尺寸、Delete 删除，或方向键微调（Shift 增大步长）。Ctrl/Cmd+Z 撤销，Ctrl/Cmd+Shift+Z 重做。Shift 约束形状/箭头角度。
4. Ctrl/Cmd+Enter 完成文字，Escape 取消。文字输入之外，点击 **Copy PNG** 或 Ctrl/Cmd+Enter 复制结果，焦点返回聊天。
5. 明确粘贴到输入框，检查附件，补充指令后发送。

截图无需接管，也不暂停 Agent。编辑器只暂停当前查看器的远端输入和文字同步。复制会等待已发出的文字剪贴板写入完成。截图/复制不会自动上传、附加或发送。

PNG 尺寸取决于原图像素裁剪，不受预览缩放或屏幕密度影响。键盘可编辑已有标注，创建标注/裁剪需要指针。复制/导出失败保留编辑，可重试或使用 **Download PNG**；下载保留编辑器打开。关闭时如会丢弃文字或撤销/重做历史，会先确认。粘贴到聊天沿用普通附件限制。

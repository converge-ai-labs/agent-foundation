---
title: 使用桌面
description: 准备桌面环境，观察桌面并在人与 agent 之间交接控制权。
---

先配置[环境 provider 和模板](environments.md)，再准备部署支持的桌面目标。确认观察能力和权限后启用 Computer 工具。本页说明 agent 操作与人工接管；仅需浏览器自动化时，使用独立的[浏览器指南](browsers.md)。

## Computer Use

桌面任务需要三个条件：兼容的图形环境、agent 已启用的**电脑**工具集，以及线程挂载的电脑权限。先准备桌面，再允许观察或交互。

### 创建 Docker 桌面

1. 配置 Docker provider 的**环境操作通道**和**主机访问地址**。Worker 必须能访问该地址及 Docker 分配的端口；Engine 访问是另一条连接。
2. 创建 Docker 模板，选择**桌面环境**。预设会选择桌面镜像、HTTP 执行方式和桌面就绪要求。
3. 将模板选为 agent 默认环境，或创建实例后添加到会话。
4. 实例运行后，在**环境与记忆**中选择**检查桌面**，再按下文授予权限。

使用回环地址或明确可信的私网 HTTP 路由，并配置对应出站策略。其他主机名需要明确的监听 IP。自定义图形镜像须兼容 Envd HTTP，并设置 `execution: "envd_http"` 和 `desktop: true`。人工桌面控制还需要浏览器可达的查看路由，见下文。

### 自动创建 Vercel 桌面

1. 使用 access token 添加 Vercel provider，选择**加载 Vercel 团队**，再选择该 token 可以访问的团队和项目。
2. 创建模板，选择**桌面环境 → 自动 Envd**。
3. 创建实例。每个新目标的安装可能需要数分钟，Console 显示阶段和已等待时间。离开页面后，可以回到**环境实例**检查进度。
4. 就绪后检查桌面预览，再启用 agent 权限。

使用具有沙箱生命周期权限、有效期适合无人值守运行的 Token。Service 直接在最终持久 Ubuntu 沙箱中安装，并在创建重入时保留其身份，不创建临时构建沙箱或准备快照。精确自定义快照仍可作为预装替代方案；原生停止/恢复快照使用各自的生命周期权限。

### 启用观察和操作

1. 启用 agent 的**电脑**工具集并保存。单个工具的审批仍然生效。
2. 在**环境与记忆**中打开挂载的**电脑操作权限**。允许观察可获取截图，允许交互可使用鼠标和键盘。
3. 发送新消息，要求 agent 描述屏幕。展开其工作步骤，在观察步骤选择**查看截图**。
4. 执行交互任务时，检查下一张截图，确认应用发生了预期变化。

电脑权限默认关闭。修改对新接收的运行生效，`workspace` 也遵循此规则。桌面就绪状态与权限独立：**检查桌面**只探测运行中的实例，不截图、不发送输入、不授予权限，也不启动已停止实例。已保存的步骤截图是历史画面；实时查看使用桌面控制。

API 客户端通过 `PATCH /api/v1/threads/{thread_id}/environments/{name}` 更新挂载，并携带线程 ETag。以下请求体只允许电脑观察：

```json
{"permission_ceiling":{"operations":["environment.computer.describe","environment.computer.observe","environment.computer.zoom"]}}
```

动作列表会替换整个权限集合。请包含需要保留的文件和执行动作；Console 预设会保留它们。电脑权限控制 Computer 工具，Shell 和其他工具仍使用各自的权限。

### 原生无障碍权限

Computer 工具集包含语义读取，以及分组的 `computer_ax`、`computer_uia` 和 `computer_atspi` 修改工具。通过 `computer_accessibility_describe` 发现后端，检查目标实际支持的能力，再通过回读或截图验证变更。原生后端就绪状态与权限独立。

观察权限包含七项 `environment.computer.a11y_*` 读取动作。精确修改上限使用平台动作，例如 `environment.computer.uia_invoke` 或 `environment.computer.atspi_do_action`；选择分组工具不等于授权全部操作。保留的节点和文本范围引用绑定原挂载和执行。判断节点不存在前，检查有界结果的完整性。

语义读取和修改使用同一 Computer 接管放行边界。交还后，执行原生修改前必须取得新截图，再由后续模型决策；语义读取、投影和修改回读不能满足截图新鲜度要求。原生修改结果不明时，必须先核对状态再尝试。

### Computer 坐标模式

模型返回相对位置时，添加以下 agent 配置片段：

```json
{"toolsets":{"computer":{"enabled":true,"config":{"coordinate_space":"normalized_1000"}}}}
```

直接传入 0 到 1000 的位置。工具将其转换为截图像素，包括拖拽的两个端点。返回像素的模型使用默认 `pixels` 模式。滚动距离和键盘输入保持不变。每次操作后都检查应用结果。

### 桌面访问前提

在 recipe 中启用 `desktop: true` 和 `desktop_assistance: true`。这些设置准备人工查看和 Computer 控制，不会添加 agent 工具。Service 不提供内置桌面协助请求工具；应用可以自行提供请求流程。agent 的 Computer 工具和挂载权限需单独配置。按 [provider 指南](../environments/providers.md)准备兼容的桌面访问 v2 端点。

Docker 需要配置 `desktop_viewer_url`，例如 `wss://desktop.example/{container_id}/websockify`，并路由到各容器的 6081 端口。Engine 和 Envd 访问不能提供这条浏览器路由。只有回环地址接受明文 WS。E2B 需要自动准备或兼容的自定义桌面模板，仅有普通 `desktop` 模板还不够。其他 provider 使用已准备的 supervisor 及各自的路由。打开查看器不会启动环境或延长环境寿命。

### 获取与归还桌面控制权

1. 在可交互会话中选择**浏览器**旁的**桌面**图标。右侧面板自动以只读方式显示所选环境挂载的桌面。
2. 选择**接管**申请 Computer 输入控制。Service 阻止后续 Computer 调用，授权后自动连接输入。已放行的调用可以继续完成，包括发送输入。
3. 直接操作桌面，文件、Shell、进程、脚本、其他桌面和 agent 的模型工作继续运行。
4. 需要更多时间时选择**续期控制权**。默认租约为五分钟；运维者通过 `control.desktop_control_seconds`、`control.desktop_control_min_seconds` 和 `control.desktop_control_max_seconds` 配置默认值和请求范围。
5. 完成后选择**归还控制权**。Service 撤销输入并确认按键和鼠标按钮清理，再重新开放 Computer 通道。

关闭查看器或页面只会断开该查看器，控制权保持到显式归还或租约过期。短期连接授权与人工租约独立续期。同一原生桌面同时只有一个主体控制，即使多个环境别名或 thread 共享它。只读查看器保持独立。Browser 控制有自己的生命周期。

每次正常模型请求都会包含已知原生桌面挂载的当前 Computer 放行状态，与权限和就绪状态分别说明。agent 看到人工控制状态后，可以调用 `computer_notify(alias="workspace")` 登记交还通知，无需先尝试一个会被阻止的操作。接管期间受阻的 Computer 调用也会自动登记同一通知。登记按 thread 和本次接管去重；交还后才登记会返回 `already_available`，不会订阅未来的接管。agent 可以继续其他工作或结束当前回合，无需轮询。本次接管结束时，包括输入授权前取消，现有收件箱向仍符合条件的调用方投递一次通知。后续接管不会抹掉已产生的事件。通知要求 agent 检查当前 Computer 放行状态，不保证桌面此刻仍可用，也不会自动登记后续接管。agent 必须观察当前桌面并进行新的模型决策，之后才能发送输入；旧手势不会重放。已结束的子 agent 不会重新启动。待处理问题和审批仍需按原有流程提交完整响应批次。

人工查看器的授权撤销或按键、鼠标按钮清理未确认时，Computer 访问保持阻塞，等待核对。agent 的 Computer 调用失败或失去执行方不会延迟接管。该控制边界覆盖受管理的 Computer 调用，包括 CodeAct 内的调用。它不会冻结任意访客进程、Shell 启动的界面自动化或独立客户端。

### 升级桌面控制

[按协调升级步骤切换桌面控制](upgrade-notes.md#desktop-control-cutover)。

### 自动创建 Sprites 桌面

1. 添加 Sprites provider，填写组织和允许归属标签、执行、删除、URL 设置的 token。
2. 创建模板并启用**桌面环境**。
3. 创建实例。每个新 Sprite 都安装 Envd、桌面依赖和 Chrome。Console 显示进度，可以稍后返回检查，或在确认失败后重试。
4. 就绪后，选择**查看桌面**获取截图。交互访问使用会话中的桌面控制。

已有 Sprite 在休眠后保留文件和安装结果，新 Sprite 则重新安装。安装失败会保留所属目标，供显式重试或删除。Sprites 不支持显式停止。浏览器访问使用短期授权和 VNC 认证，云 API Token 保留在 Service。

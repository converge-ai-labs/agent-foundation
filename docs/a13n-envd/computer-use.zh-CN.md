---
title: 桌面计算机使用
description: 通过 Envd 驱动 Linux、macOS 与 Windows 图形桌面，支持屏幕截图观察、键鼠操作、剪贴板读写与原生无障碍树。
---

在 `a13n-envd` 中启用 `computer_use`，让支持图像理解的智能体观察桌面画面、发送鼠标键盘输入、检查原生无障碍树并读写剪贴板。

## 平台要求

| 平台    | 桌面环境要求                                                                          | 输入支持与注意事项                                                     |
| ------- | ------------------------------------------------------------------------------------- | ---------------------------------------------------------------------- |
| macOS   | 已登录图形会话，并授予**屏幕录制**与**辅助功能**权限                                  | 鼠标指针、快捷键组合、`computer_type_text`、滚轮                       |
| Linux   | 已认证 X11 会话（`DISPLAY` + `XAUTHORITY`，含 RandR 1.5、XKB、XTEST；不支持 Wayland） | 鼠标指针、按键组合、滚轮步数；输入文本请配合剪贴板粘贴而非 `type_text` |
| Windows | 已登录且未锁屏的交互式用户桌面                                                        | 鼠标指针、扫描码组合键、`computer_type_text`、滚轮步数                 |

计算机使用要求关闭 Envd 操作系统沙箱（`sandbox.mode = "disabled"`）并使用 `inherit` 出站模式。

## 连接并绑定桌面

1. 在目标图形会话的终端中携带 `--computer-use true` 启动配对：

```console
a13n-envd connect https://your-harness-ui.example.com --computer-use true
```

2. 在 WebUI（**Settings → Environments**）核对验证码并批准，同时完成系统桌面权限授权（默认最多等待 `A13N_ENVD_COMPUTER_USE_PERMISSION_TIMEOUT_MS` 即 120 秒）。
3. 将该设备绑定到项目或线程，选择 **Full control**（或仅授予 `environment.computer.describe`、`.observe`、`.zoom` 等只读观察权限）。

### 升级后恢复 macOS 权限

更新 `a13n-envd` 二进制后，即使系统设置中开关仍显示开启，旧的 TCC 授权也可能失效：

1. 停止 Envd，打开**系统设置 → 隐私与安全性 → 辅助功能**与**屏幕录制**。
2. 移除旧的 `a13n-envd`（或启动器）条目后重新添加并开启，随后重启 Envd。

## 屏幕、输入与剪贴板工具

| 工具                                                                  | 用途                                                                      |
| --------------------------------------------------------------------- | ------------------------------------------------------------------------- |
| `computer_describe` / `computer_observe`                              | 列出显示器并截取屏幕（返回 `observation_id`，最大边长 256–2048 像素）     |
| `computer_zoom`                                                       | 基于 `observation_id` 裁剪局部高清画面；后续点击仍使用全屏坐标            |
| `computer_click`、`computer_move`、`computer_drag`、`computer_scroll` | 基于最新 `observation_id` 坐标执行指针操作                                |
| `computer_type_text`、`computer_press_keys`                           | 输入 Unicode 文本（macOS/Windows）或触发组合键（如 `["meta", "v"]`）      |
| `computer_read_clipboard`、`computer_write_clipboard`                 | 读写最大 16 KiB 的 UTF-8 剪贴板文本（`environment.computer.*_clipboard`） |

## 原生无障碍能力

Envd 在 macOS（AX）、Windows（UIA）与 Linux X11（经 `org.a11y.Bus` 的 AT-SPI）上提供有界语义化界面树读取与原生控件操作：

- **读取工具：** `computer_accessibility_describe`、`_roots`、`_inspect`、`_find`、`_read`、`_range`、`_range_read`（需授予对应的 `environment.computer.a11y_*` 权限）。
- **修改工具：** `computer_ax`、`computer_uia`、`computer_atspi`——具体原生动作须在挂载 `permission_ceiling` 中逐项显式放行（如 `environment.computer.ax_perform_action` 或 `uia_invoke`）。

## Python EIP 客户端示例

```python
from a13n_envd_client.eip.v1 import ComputerRegion

async with session.observe_computer(max_dimension=1280) as reader:
    screenshot = b"".join([chunk async for chunk in reader])
    obs = reader.opened.observation

async with session.zoom_computer(
    observation_id=obs.observation_id,
    region=ComputerRegion(left=0, top=0, right=obs.width // 2, bottom=obs.height // 2),
) as reader:
    detail = b"".join([chunk async for chunk in reader])
```

---
title: 计算机使用
description: 在 macOS、Linux（X11）与 Windows 上启用 Envd 的桌面截图、键鼠输入及原生无障碍树检查。
---

当智能体需要查看桌面画面、执行点击或输入、或读取原生界面无障碍树时，在 `a13n-envd` 中启用 `computer_use`。

## 启用计算机使用

传入 `--computer-use` 或设置 `A13N_ENVD_COMPUTER_USE=true`（需关闭操作系统沙箱并使用继承出站网络）：

```sh
A13N_ENVD_COMPUTER_USE=true a13n-envd connect https://host.example.com
```

Envd 会在启动传输前检查桌面就绪状态（超时由 `computer_use_permission_timeout_ms` 控制，默认 120 秒）。

## 原生无障碍能力

除截图外，Envd 还暴露各平台原生无障碍树（`computer_accessibility_describe`，以及 macOS 的 `computer_ax`、Windows 的 `computer_uia` 和 Linux 的 `computer_atspi`）。在挂载权限上限中授予对应的 `environment.computer.a11y_*` 操作即可启用语义化界面检查与交互。

## 升级后恢复 macOS 权限

替换 `a13n-envd` 二进制后若 macOS 提示缺少**屏幕录制**或**辅助功能**权限：

1. 打开**系统设置 → 隐私与安全性 → 屏幕录制**与**辅助功能**。
2. 移除旧的 `a13n-envd` 条目，重新添加新版二进制（或关闭后重新开启），然后重启 Envd。

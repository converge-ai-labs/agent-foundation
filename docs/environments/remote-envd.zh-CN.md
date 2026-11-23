---
title: 远程 Envd
description: 通过 HTTP(S) 或反向 WebSocket 将 Harness、Harness UI 或 Service 连接到运行 a13n-envd 的外部机器。
---

当智能体需要在你已有的机器（构建机、工作站或虚拟机）而非新创建的容器上工作时，使用**远程 Envd**。

## 连接到已有 HTTP 守护进程

1. 在目标机以 `A13N_ENVD_TRANSPORT=http`、稳定的 `A13N_ENVD_DEVICE_ID` 和权限为 `0600` 的凭据文件启动 `a13n-envd`（回环地址之外要求 HTTPS）。
2. 在宿主配置 TLS 信任（`SSL_CERT_FILE` 或私有 CA）；明文 HTTP 仅允许回环地址或显式信任的私有链路。
3. 在 Harness 中用 [`PreparedSource`](../a13n-harness/environments.md#supply-a-source) 包装已连接客户端，或在 Service 中完成注册。

## 连接到 Service

- **出站连接（NAT/防火墙后推荐）：** 运行 `a13n-envd connect-service` 或在 Console（**Environments → Instances → Connect my computer**）配对，见[连接客户端计算机](../a13n-service/environments.md#connect-a-client-computer)。
- **直连 HTTP：** 通过 `POST /api/v1/environments` 注册 HTTPS 地址与 Token，见[注册外部目标](../a13n-service/environments.md#register-an-external-target)。

## 连接到 Harness UI

- 运行 `a13n-envd connect https://your-webui-host.example.com` 并在 WebUI 批准（[连接并批准 Envd](../a13n-harness-ui/environments-and-projects.md#connect-and-approve-envd)）。
- 将设备绑定到项目或线程（[添加设备绑定](../a13n-harness-ui/environments-and-projects.md#add-device-bindings)）。
- 可随时在 **Settings → Environments → Devices** 撤销信任（[撤销设备](../a13n-harness-ui/environments-and-projects.md#revoke-a-device)）。
- 通过 `A13N_ENVD_FULL_CONTROL=1` 或显式 Shell 配置启用命令（[启用命令](../a13n-envd/configuration.md#enable-commands)），并在重启间保留[已保存的实例与宿主](../a13n-envd/configuration.md#saved-instances-and-hosts)。

## 会话出站与凭据引用

在会话配置中设置 `egress_mode: controlled`，即可限制出站目标域名并在 HTTPS 请求头中自动注入密钥，避免在环境变量中暴露明文 Token。详见[会话出站](../a13n-envd/egress.md)。

## 状态、并发与恢复

宿主可保存会话恢复提示，在同一守护进程世代内于空闲或最大存活期过期前重新附着会话。重新附着不会重放命令；若守护进程已重启或会话过期，需打开新会话。

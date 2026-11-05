---
title: 云端出站网络历史验证
sidebarTitle: 出站网络历史验证
description: 2026 年 9 月云平台探测的范围与限制，不代表当前 Provider 支持矩阵。
---

本记录保留早期测试活动的证据。新部署接纳工作前，按[当前出站网络前置条件与验证](egress.md#verify-a-sandbox-image)执行检查。

## 云平台验证记录

这份历史记录涵盖截至 **2026-09-22** 完成的探测，五个云平台使用同一个 Linux x86_64 二进制文件。测试使用生成的凭据；provider 凭据保留在会话之外。

**该矩阵早于 2026-09-22 引入的原生身份和 sudo 变更，尚未针对该实现重新运行。** 当前部署采用[受控出站网络的 root 启动者要求](egress.md)。旧的非 root 启动、只读系统树检查和命名 socket 拒绝行为描述的是被测二进制文件。当前行为见[文件、socket 与生命周期](egress.md#files-sockets-and-lifetime)。记录覆盖这些 Envd 沙箱探测，不代表 Provider/Service 集成验证或整个平台的支持保证。

现有记录未保存完整源码 revision 或镜像 digest，不能据此重建被测二进制。重复测试时同时记录 revision、二进制校验和、镜像 digest 与下列运行时信息。

### 被测二进制文件的云平台结果

| 平台 / 运行时     | 观测到的内核           | 被测 Envd 启动身份    | 结果与覆盖范围                                                                  |
| ----------------- | ---------------------- | --------------------- | ------------------------------------------------------------------------------- |
| E2B               | `6.1.158+`             | 默认账号，UID 1000    | 完整 HTTPS fixture 和受控会话生命周期通过。                                     |
| Runloop           | `6.18.32`              | 默认账号，UID 1000    | 完整 HTTPS fixture 和受控会话生命周期通过。                                     |
| Vercel Sandbox    | `6.18.49`              | 通过 `sudo` 使用 root | 完整 HTTPS fixture 和受控会话生命周期通过。                                     |
| Daytona           | `6.8.0-138-generic`    | 通过 `sudo` 使用 root | GitHub 请求头 fixture 和受控会话生命周期通过；外部回显站点被阻止。              |
| Modal 默认 gVisor | 报告为 `4.19.0-gvisor` | Root                  | 普通会话/就绪检查、shell 执行和文件读写通过。受控会话创建返回 `unsupported`。   |
| Modal VM Sandbox  | `7.2.6`                | Root                  | 普通 Envd 流程、完整 HTTPS fixture、受控会话生命周期和 VM 内 TCP/UDP 回显通过。 |

**完整 HTTPS fixture** 检查替换后的 Basic Auth 请求头、回显响应中精确秘密值的脱敏，以及不含真实秘密的会话元数据。**受控会话生命周期** 检查策略更新、重放、该二进制文件的文件系统/socket 限制、配额复用及 broker 死亡后对已分离 `setsid` 后代进程的清理。两个 Modal 运行时还运行了普通会话 shell/文件冒烟检查；其他平台运行受控 fixture。

通过 `--local-network-fixture`，特权本地 Linux 容器和 Modal VM 的 TCP/UDP 回显通过。回显服务位于外层测试环境内。外部互联网 TCP/UDP，以及 E2B、Runloop、Vercel 和 Daytona 的 TCP/UDP 均未测试。Sprites 和未列出的平台不在本轮验证范围内。

### Daytona 网络限制

绕过 Envd 的直接请求可以访问 `api.github.com`，但访问 `httpbin.org`、`httpbingo.org` 和 `example.com` 失败。Envd allowlist 无法扩大外层平台允许的目标范围。

GitHub fixture 因不支持的 `Accept` 值收到 HTTP 415。将占位标记替换为 `application/json` 后得到 HTTP 200，再从响应 `Content-Type` 中脱敏该值。这验证了请求头替换和响应头脱敏；Basic Auth/响应体回显仍被阻止。

### 选择 Modal 运行时

被测默认 gVisor 运行时拒绝受控 `session.open`，返回 `unsupported` 和 `unshare: Operation not permitted`；独立执行 `nft list ruleset` 返回 `Protocol not supported`。root 启动也未提供缺失能力。

被测 VM Sandbox 运行时通过测试，使用了以下 Modal SDK 选择：

```python
sandbox = modal.Sandbox.create(
    app=app,
    image=image,
    experimental_options={"vm_runtime": True},
)
```

这个 SDK 设置选择外层运行时。它既不是 Envd 字段，也不是新增的内置 Modal Provider 选项。使用新镜像或二进制文件时，准备当前前置条件，并重新运行[镜像验证](egress.md#verify-a-sandbox-image)。

### 测试准备与限制

探测补装了缺失的网络工具和 CA 证书。Vercel 使用账号接受的超时值；Daytona 省略了与默认快照不兼容的资源覆盖。Modal 的宿主 Python 客户端使用代理支持依赖和显式 CA bundle。这些是测试准备调整，生产 Provider 代码保持不变。

所有临时沙箱均已终止。原生 Provider 实例已核对为不存在，五个 Modal 实例（包括诊断尝试）也已确认停止。重复验证时，一并记录日期、镜像、运行时、内核、启动身份和 fixture 覆盖范围，包括仍待测试的行为变更。

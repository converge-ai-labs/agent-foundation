---
title: 会话出站网络
description: 为每个会话设置目标策略，通过 broker 注入 HTTPS 凭据，并在命令持续运行时轮换凭据。
---

受控出站网络让会话访问获准服务，同时让命令持有凭据占位标记，而非真实秘密。Envd broker 检查访问目标，并向允许的 HTTPS 请求头注入秘密。先准备 Linux 沙箱，再启用 controlled 模式，并为每个会话提供策略。

设置 `A13N_ENVD_EGRESS_MODE=controlled`、传入 `--egress-mode controlled`，或将以下守护进程 JSON 放在工作目录之外：

```json
{
  "full_control": true,
  "default_working_directory": "/workspace",
  "egress": {"mode": "controlled"}
}
```

守护进程需要具有 mount/PID/IPC/network 命名空间权限的 root 启动者。准备 Linux 6.1.2 或更新版本、seccomp、pidfd、挂载树克隆、系统 CA bundle，以及 `/usr/bin/unshare`、`/usr/sbin/ip`、`/usr/sbin/nft`、`/usr/bin/ldd`。缺少这些能力会阻止启动或会话准备。[沙箱镜像](sandbox.md)提供用户态工具和 root 启动者；默认仍为 `egress.mode: inherit`，所需权限由外层运行时授予。

会话使用原生账号。未配置执行 UID/GID 时，会话保留 root 启动者身份。要使用其他账号，先预置账号，再配置[执行身份和 sudo](configuration.md#native-identity-and-sudo)。两种身份都受出站网络边界约束。Sandbox 授权独立配置：`restricted` 开放授权目录和最小只读系统视图；`disabled` 保留原生文件系统访问。

## 创建受控会话

1. 初始化设备连接。
2. 使用初始化返回的设备身份和代际，向 `session.open.params` 添加 `egress`。
3. 通过经过身份验证的 EIP 传输通道发送请求。真实秘密应避开守护进程配置、命令参数和请求日志。

```json
{
  "expected_device_id": "device-example",
  "expected_generation": 123,
  "working_directory": "/workspace",
  "egress": {
    "destinations": {"mode": "allowlist", "hosts": ["api.github.com"]},
    "secrets": [
      {
        "env": "GH_TOKEN",
        "value": "<real token supplied by the trusted caller>",
        "inject_hosts": ["api.github.com"]
      }
    ]
  }
}
```

每个受控会话都必须提供显式目标策略。使用 `inherit` 或 `deny` 的设备会拒绝会话出站网络策略。返回的会话描述符包含修订号和秘密元数据，例如 `{"env":"GH_TOKEN","sentinel":"a13n_7d4e9c2a_GH_TOKEN","inject_hosts":["api.github.com"]}`；秘密的 `value` 只可写入。

命令使用 `GH_TOKEN` 中提供的占位标记：

```bash
curl https://api.github.com/user -H "Authorization: Bearer $GH_TOKEN"
```

内核路由将会话流量送入 worker 外部的 broker，客户端无需设置 `HTTP_PROXY` 或 `HTTPS_PROXY`。在 HTTPS 443 端口上，broker 只针对该秘密的 `inject_hosts` 替换请求**头字段值**中的占位标记。URL 和请求体保持原样。broker 验证上游 TLS。

```mermaid
flowchart TB
    Caller["可信调用方"] -->|"session.open 携带秘密值"| Envd["Envd"]
    subgraph Session["受控会话"]
        Command["命令：GH_TOKEN 中是占位标记"]
    end
    Envd --> Command
    Command -->|"所有流量，由内核路由"| Broker["Envd broker"]
    Broker -->|"允许的主机：请求头字段值换成真实秘密"| Upstream["api.github.com"]
    Broker -->|"其他主机或非公共地址"| Refused["拒绝"]

    class Caller app
    class Envd,Broker a13n
    class Upstream ext
    class Refused danger
```

Envd 通过 `SSL_CERT_FILE`、`REQUESTS_CA_BUNDLE`、`CURL_CA_BUNDLE` 和 `NODE_EXTRA_CA_CERTS` 提供包含系统信任与会话 CA 的私有 bundle。使用证书固定或独立信任库的客户端需要显式集成。原生系统 CA bundle 仍可由 `update-ca-certificates` 更新。

sudo 通常会移除这些变量。按现有 sudoers 策略保留 root HTTPS 客户端所需的变量，或配置该客户端的信任库：

```bash
sudo --preserve-env=SSL_CERT_FILE,CURL_CA_BUNDLE curl https://api.github.com/
```

客户端 TLS 信任配置有误时，目标检查仍然生效。Envd 保持 sudoers 和全局信任库不变。

broker 会在响应头、响应体字节和 trailers 中脱敏与秘密值完全相同的内容，包括跨块匹配。编码或转换后的值不在字面值脱敏范围内。支持 HTTP/1.1；协议升级和压缩响应会被拒绝。请求压缩保持原样。HTTP 80 端口拒绝带有占位标记的请求头，也不注入凭据。其他 TCP 端口和 UDP 转发获准流量，不替换秘密。

## 目标策略

为必填的 `destinations` 选择一个值：

| 必填的 `destinations`                             | 行为                                           |
| ------------------------------------------------- | ---------------------------------------------- |
| `{"mode":"public"}`                               | 允许公共 DNS 名称，不允许直接 IP 连接。        |
| `{"mode":"allowlist","hosts":[]}`                 | 拒绝外部访问。                                 |
| `{"mode":"allowlist","hosts":["api.github.com"]}` | 只允许明确列出的 DNS 名称或显式列出的公共 IP。 |

使用精确名称：通配符、后缀匹配和 URL 模式均无效。秘密的 `inject_hosts` 不能为空，使用 allowlist 时还必须是 `hosts` 的子集。public 模式下，`inject_hosts` 仍限制该秘密的发送目标。会话策略就是 Envd 的目标列表；外层平台限制独立生效。

broker 拒绝私有、回环、链路本地、元数据及其他非公共上游地址。DNS 响应只要包含非公共地址就会被拒绝。直接 IP 连接需要显式 allowlist 条目，且始终不会注入凭据。

会话拦截路径使用 IPv4；broker 可以连接 DNS 解析出的 IPv4 或 IPv6 上游地址。命令发起的原生 IPv6 连接没有外部路由。本地服务仍可通过会话自身的回环地址访问，该回环与外层沙箱分离。

## 无需重启更新策略

从会话元数据读取当前修订号，再携带会话选择器发送 `egress.update`。以下示例在一次更新中添加 `example.com` 并轮换 `GH_TOKEN`：

```json
{
  "jsonrpc": "2.0",
  "id": 7,
  "eip_session": "session-example",
  "method": "egress.update",
  "params": {
    "expected_revision": 1,
    "destinations": {"mode": "allowlist", "hosts": ["api.github.com", "example.com"]},
    "set_secrets": [
      {"env":"GH_TOKEN","value":"<replacement token>","inject_hosts":["api.github.com"]}
    ],
    "remove_secrets": []
  }
}
```

更新经过验证后原子发布，再递增 `revision`。若返回 `conflict`，先通过 `environment.describe` 或 `session.attach` 读取当前元数据，再决定新的更新内容。省略的字段保留原值。空 allowlist 拒绝外部访问；public 模式恢复公共域名访问。设备身份、Sandbox 授权和网络模式保持固定。

`set_secrets` 添加或替换绑定。轮换保留占位标记，因此已使用 `$GH_TOKEN` 的进程会在下一次 HTTPS 请求中使用新值。`remove_secrets` 停用绑定；删除后重新添加会创建新标记。拦截请求头包含已停用标记时，请求会被拒绝。移除获准主机时，在同一次更新中删除或修改它的注入绑定。

新命令接收当前绑定。运行中的进程保留原环境：添加绑定不会为它们新增变量，删除绑定也会让旧标记留在其内存中。命令环境设置不能覆盖或移除绑定的秘密或 CA 变量。重试匹配使用原命令请求，因此修改策略不会创建新操作。

## 文件、socket 与生命周期

命令和文件操作共用同一个原生身份 worker。设置 `sandbox.mode: disabled` 时，软件安装、服务账号、权限、ACL、链接器/CA 更新及系统文件修改都作用于外层沙箱的原始可写系统树。这些修改跨会话保留。`/tmp`、`/run`、设备/内核视图和共享内存使用私有挂载；位于 `/tmp` 下的工作目录会重新绑定到原始底层目录。

接收工作前，Envd 为 broker 和启动流程准备不可变的私有运行时。worker 使用原始系统树，其中的 Envd 配置、传输凭据、broker 运行时和启动账号的 `.a13n` 目录已被遮蔽。遮蔽在克隆 worker 前完成，因此重命名祖先目录不会向后续会话暴露这些文件。受保护文件存在额外硬链接，或工作目录与受保护路径重叠时，准备会被拒绝。将无关凭据存放在工作目录和镜像之外。

禁用 Sandbox 时，默认按现有 sudoers 启用原生 sudo。Linux 受限 Sandbox 则移除 capabilities 并阻止提权，包括 UID 0。两者都保留受控出站网络限制：sudo 后仍不能修改命名空间/挂载、使用 raw/packet/VSOCK 网络、网络管理 capabilities、不安全的设备/内核控制或 io_uring 绕过。会话内 Unix socket、socketpair、PTY 和低端口服务可正常使用。Host 提供 CPU/内存限制，并负责外层沙箱的生命周期。

### 部署前置条件

使用专用、可丢弃的沙箱。共享树中应排除无关特权服务、执行载荷可写文件的 cron/systemd 作业，以及外部特权或网络代理 socket。否则，worker 外部的服务可能执行被修改的文件，或转发策略之外的流量。这包括 Docker socket、外部 SSH-agent 和数据库代理。

每次启动守护进程都应提供可信启动文件。管理快照保护运行中的 broker 不受后续系统修改影响；若系统树已被 root 工作负载修改，重启前应恢复或重建可信文件。彼此敌对的会话应使用独立部署。受控出站网络约束 worker 流量，不覆盖共享机器上的所有进程。

分离连接后，worker 和策略保留到会话过期；重新附加返回当前策略元数据。关闭、过期、broker 失败或守护进程死亡会停止受控执行和网络。真实秘密与会话 CA 私钥保留在 broker 内存中。

每个会话在创建时按配置预算预留设备总额。如果空闲会话在达到 `max_sessions` 前耗尽设备总额，应提高相应总额。确认 worker 死亡后释放容量。如果无法确认暂存文件清理完成，其预留会一直计费到守护进程销毁。

## 验证沙箱镜像

1. 安装上述前置条件，并预置执行账号。
2. 使用可丢弃的沙箱，启动者与部署时的 root 启动者一致。
3. 运行仓库的公开 EIP 集成测试：

```bash
python3 crates/a13n-envd/tests/egress_linux.py /path/to/a13n-envd

# Additional native sudo, package, identity, file and cleanup checks.
# Requires a disposable Debian/Ubuntu sandbox, sudo, ACL tools and UID/GID 1000
# with a test-only NOPASSWD sudoers rule. This test changes the sandbox system.
python3 crates/a13n-envd/tests/execution_linux.py /path/to/a13n-envd --controlled
```

egress fixture 使用生成的测试凭据访问 `httpbin.org`；`--http-host httpbingo.org` 可选择兼容端点。它检查 HTTPS 身份验证、响应脱敏、策略更新、重放、文件系统/socket 行为、容量复用和 broker 死亡清理。若网络只允许开发服务，可用 `--http-host api.github.com` 通过媒体类型验证检查请求头替换和响应头脱敏，替代 Basic Auth/响应体回显检查。

仅在可丢弃的特权 Linux 沙箱中添加 `--local-network-fixture`。它在外层测试命名空间创建临时公共地址别名，用于 TCP/UDP 回显检查。

## 云平台验证记录

见[云端历史验证记录](egress-validation.md#cloud-platform-validation-record)。这些结果不构成当前二进制文件的支持证明。

### 被测二进制文件的云平台结果

见[云端历史验证记录](egress-validation.md#cloud-results-for-the-tested-binary)。这些结果不构成当前二进制文件的支持证明。

### Daytona 网络限制

见[云端历史验证记录](egress-validation.md#daytona-network-restrictions)。这些结果不构成当前二进制文件的支持证明。

### 选择 Modal 运行时

见[云端历史验证记录](egress-validation.md#modal-runtime-selection)。这些结果不构成当前二进制文件的支持证明。

### 测试准备与限制

见[云端历史验证记录](egress-validation.md#test-preparation-and-limits)。这些结果不构成当前二进制文件的支持证明。

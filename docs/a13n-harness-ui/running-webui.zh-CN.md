---
title: 运行与重启 WebUI
description: 配置实例访问与反向代理，更新服务器，并检查恢复与关闭过程。
---

为自己或可信协作者运行 Harness UI 实例时，使用本指南。先[启动 WebUI 并验证一次对话](webui.md#start-the-server)。实例访问共享工作台权限；需要受管理的用户与跨 worker 持久执行时，使用 [Service](../a13n-service/index.md)。

## 认证与密钥保存

启动输出普通地址。使用生成密钥时还输出密钥和 fragment 登录链接。浏览器移除 fragment，把成功使用的密钥保存在同 origin localStorage，用于认证 API 请求。**Log out** 移除密钥并关闭受保护视图。静态资源不含密钥。

密钥优先级为 `--apikey`、`A13N_HARNESS_UI_API_KEY`、新生成的进程密钥。提供的密钥不回显，但命令行参数可能对操作系统可见。空密钥和重复的冲突值会被拒绝。

`--dangerous-skip-permissions` 只关闭 Web 认证；Agent 权限和共享检查仍生效，且不能与提供的密钥并用。`--api-key` 和 `--dangerously-bypass-permission` 保留为兼容别名。开放监听器前检查 [CLI 选项](command-reference.md#webui)。

## 反向代理与公开地址

1. 在选定根 YAML 添加外部地址。将示例替换为实际协议、主机和端口：

   ```yaml
   webui:
     allowed_origins:
       - "https://workbench.example.com:8090"
   ```

2. 配置 TLS 终止和 WebSocket 升级。保留外部 `Host`（含端口）及浏览器 `Origin`，通过 `X-Forwarded-Proto: https` 转发 HTTPS 协议。

3. 如果代理对端不是可信回环 `127.0.0.1`，在 WebUI 进程环境用 `FORWARDED_ALLOW_IPS` 设置该进程/容器实际看到的代理 IP/网段。让代理覆盖客户端传入的转发头。

4. 重启 WebUI，打开外部地址登录，并检查对话和交互式连接。

Origin 准入匹配协议、域名、端口，示例不同时允许 HTTP 或 443。默认 `[]` 保留监听地址限制，`allowed_origins: ["*"]` 允许任意地址。两者都不关闭认证或开启跨 origin API 访问。只信任实际代理对端，不要改头为回环或无限制信任转发头来绕过准入。见[允许的 origin](configuration.md#webui-allowed-origins)。

Device 配对返回相对根路径，按配对的 Host URL 解析并保留公开 HTTPS 和端口。批准本身不代表 Device 已连接或有桌面权限。

## 重启或更新服务器

1. 保存私人编辑器修改。
2. 按一次 Ctrl+C 或发送 SIGTERM，等待 **WebUI stopped** 和进程退出。
3. 更新时，在退出后按[安装流程](installation.md)安装新包。
4. 使用相同数据目录启动替代进程。检查中断对话及恢复错误。

优雅关闭保存根/子检查点、完成 Environment 收尾，再记录重启交接。下次启动只消费一次，并在新 Run 中继续兼容的中断工作，不重发提示词或重放保存的工具批次。

`shutdown_timeout_seconds` 默认 60 秒，限制等待安全边界，不限制整个进程退出时间。批次无法完成或检查点/收尾失败时，不登记该批次自动恢复。取消和清理仍需要时间；旧进程结束后再启动替代进程。

强制终止或重建失败后，先检查保存历史/日志，再重试不确定工作。待决策仍未回答。原生终端、旧 Shell handle、共享草稿和未保存编辑器不恢复。

## 服务器日志与关闭

用 `process.log_level` 和 `process.log_format` 控制日志。INFO 报告普通 API 活动，DEBUG 增加资源、健康探测和清理阶段。API 记录路由模板、状态、耗时、错误码及生成 ID，不含请求体、密钥、查询值或原生路径。

| 观测                                                                   | 操作                                               |
| ---------------------------------------------------------------------- | -------------------------------------------------- |
| **Stopping WebUI**                                                     | 等待清理；**WebUI stopped** 确认完成               |
| 关闭缓慢                                                               | 检查 DEBUG 阶段；连接排空超时不是 App 清理截止时间 |
| **Connection interrupted**                                             | 等待重连或 **Retry now**；服务器已停则先重启       |
| `thread_history_continuation_changed` / `thread_continuation_conflict` | 刷新对话                                           |
| `object_payload_incompatible`                                          | 检查存储警告的对象/版本/校验详情，保留数据目录     |

重连刷新观测，不重放失败保存或提交。浏览器断线不停止 Run 或终端。WARNING 隐藏普通进度。

## 监听器与应用生命周期

即使没有浏览器，运行的服务器也拥有 App。非回环监听向可信网络共享实例权限，应配合 TLS 和合适访问控制；不提供租户隔离。无需认证的 `/healthz`、`/readyz` 暴露有界存活/就绪状态；没有 Model 时也可表示已就绪、可开始设置。

兼容的 TUI/WebUI 进程可跨包升级使用同一本地数据库。较旧兼容读取方保留未知迁移历史并检查所需表/列；更高迁移号本身不会拒绝读取或阻止活动保存。真实存储、续接/版本和 payload 不兼容仍失败。数据库兼容不共享实时执行所有权，已发布二进制仍有各自检查。

## 容器与安装资源

在工作台 Host 安装 Python 分发包。wheel/sdist 包含 WebUI 资源，用户不需 Node.js 或前端 checkout。Harness UI 不发布官方容器镜像；自建部署应固定分发版本并配置监听器、认证和持久挂载。`a13n-sandbox` 是 Agent 执行镜像，不是 WebUI 服务器。

仓库开发用 `make webui` 构建/安装资源，在 `var/harness-ui/` 下以隔离配置/数据启动前台服务器。未提供密钥时输出生成密钥登录链接。`WEBUI_ARGS` 传服务器选项，`CLI_ARGS` 传全局选项：

```bash
make webui WEBUI_ARGS='--port 9000 --no-share-computer'
```

初始化与环境覆盖见[开发指南](https://github.com/converge-ai-labs/agent-foundation/blob/main/dev/harness-ui/README.md)。

## 选项与职责

监听地址、端口、认证和原生共享是进程选项，额外准入 origin 位于根 YAML。完整选项见[命令参考](command-reference.md#webui)。

自己的客户端接入见 [HTTP 流程](http-api.md)，进程内集成使用 [Python App](embedding.md)，不需监听器的脚本使用[单次执行](automation-and-troubleshooting.md#automation-and-diagnostics)。
